"""
Build consensus projections by averaging across available sources per player.

Cross-source identity: FanGraphs player ID is canonical. PECOTA and other
non-FG sources are matched to FG IDs via name + team fuzzy matching when
an exact FG ID is unavailable.

Averaging rules:
  - Simple mean across all sources that have a value for a given stat.
  - Sources that are missing a stat for a player do not pull that stat toward 0;
    they are simply excluded from that stat's average.
  - A player appears in consensus if they appear in at least one source.
  - sources_available / sources_missing are tracked per player for the UI.

Position handling:
  - Take the union of positions across all sources (a player eligible at SS in
    Steamer and 2B in ZiPS is eligible at both SS and 2B in consensus).
  - is_dh_only = True only if ALL sources agree the player has no traditional
    position (conservative — better to over-include than exclude DH players
    who might have emergency eligibility).
"""

from __future__ import annotations
import dataclasses
import logging
from collections import defaultdict

import numpy as np
from rapidfuzz import process, fuzz

from .schema import RawProjection, ConsensusProjection

logger = logging.getLogger(__name__)

ALL_SOURCES = ["steamer", "zips", "atc", "depthcharts", "pecota"]


def build_consensus(projections: list[RawProjection]) -> list[ConsensusProjection]:
    """
    Average projections across sources, grouped by fg_id.

    projections : flat list of RawProjection from all sources, already normalized.
    Returns     : list of ConsensusProjection, one per unique player.
    """
    # Group by fg_id
    by_id: dict[str, list[RawProjection]] = defaultdict(list)
    for proj in projections:
        by_id[proj.fg_id].append(proj)

    consensus: list[ConsensusProjection] = []
    for fg_id, player_projections in by_id.items():
        c = _merge_player_projections(fg_id, player_projections)
        consensus.append(c)

    # Sort: hitters then pitchers, then by name
    consensus.sort(key=lambda p: (p.player_type, p.name))
    logger.info("Built consensus for %d players", len(consensus))
    return consensus


def _merge_player_projections(
    fg_id: str,
    projections: list[RawProjection],
) -> ConsensusProjection:
    """Merge multiple source projections for one player into a consensus."""
    # Two-way player handling: if the same fg_id has both hitter and pitcher
    # projections (e.g. Ohtani), use only the hitter projections. A player
    # occupies one roster spot, and hitters are valued as such.
    hitter_projs = [p for p in projections if p.player_type == "hitter"]
    pitcher_projs = [p for p in projections if p.player_type == "pitcher"]
    if hitter_projs and pitcher_projs:
        logger.info(
            "Two-way player '%s' (%s) — using hitter projections only",
            projections[0].name, fg_id,
        )
        projections = hitter_projs

    # Use the first projection with a non-empty name as the metadata base
    base = projections[0]
    for p in projections:
        if p.name:
            base = p
            break

    sources_present = sorted({p.source for p in projections})
    sources_missing = [s for s in ALL_SOURCES if s not in sources_present]

    # Collect all stat keys seen across any source
    all_stat_keys: set[str] = set()
    for p in projections:
        all_stat_keys.update(p.stats.keys())

    # Average each stat across sources that have it (ignore missing/zero only for
    # stats that are truly structural zeros like HLD for non-closers — we allow 0s)
    consensus_stats: dict[str, float] = {}
    for key in all_stat_keys:
        values = [p.stats[key] for p in projections if key in p.stats]
        if values:
            consensus_stats[key] = float(np.mean(values))

    # Union of positions; is_dh_only only if ALL sources say so
    all_positions: list[str] = []
    for p in projections:
        for pos in p.positions:
            if pos not in all_positions:
                all_positions.append(pos)

    is_dh_only = all(p.is_dh_only for p in projections)

    return ConsensusProjection(
        fg_id=fg_id,
        name=base.name,
        team=base.team,
        positions=all_positions,
        player_type=base.player_type,
        stats=consensus_stats,
        sources_available=sources_present,
        sources_missing=sources_missing,
        is_dh_only=is_dh_only,
    )


# ---------------------------------------------------------------------------
# PECOTA ID matching — fuzzy name+team matching to assign FG IDs
# ---------------------------------------------------------------------------

def match_pecota_to_fg_ids(
    pecota_projections: list[RawProjection],
    fg_projections: list[RawProjection],
    score_cutoff: int = 85,
) -> list[RawProjection]:
    """
    Assign FanGraphs IDs to PECOTA projections that lack them.

    Matching strategy: fuzzy match on 'name + team' string using token_sort_ratio,
    which handles name-order differences (e.g. "Ohtani, Shohei" vs "Shohei Ohtani").
    Players below score_cutoff are dropped with a warning.

    Returns a new list of RawProjection with fg_id filled in where matched.
    """
    # Build a lookup: "name|team" → fg_id for all FG projections
    fg_lookup: dict[str, str] = {}
    fg_keys: list[str] = []
    for p in fg_projections:
        if p.fg_id:
            key = f"{p.name.lower()}|{p.team.lower()}"
            fg_lookup[key] = p.fg_id
            fg_keys.append(key)

    matched: list[RawProjection] = []
    unmatched = 0

    for p in pecota_projections:
        if p.fg_id and p.fg_id not in ("", "nan"):
            # Already has a FG ID (e.g. BP sometimes includes it)
            matched.append(p)
            continue

        query = f"{p.name.lower()}|{p.team.lower()}"
        result = process.extractOne(
            query, fg_keys, scorer=fuzz.token_sort_ratio, score_cutoff=score_cutoff
        )
        if result is None:
            logger.warning(
                "PECOTA: could not match '%s' (%s) to a FanGraphs player — skipping",
                p.name,
                p.team,
            )
            unmatched += 1
            continue

        matched_key, _score, _ = result
        fg_id = fg_lookup[matched_key]

        matched.append(dataclasses.replace(p, fg_id=fg_id))

    if unmatched:
        logger.warning(
            "PECOTA: %d players could not be matched and were excluded", unmatched
        )
    return matched
