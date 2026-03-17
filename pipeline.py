"""
Top-level calculation pipeline.

Orchestrates all modules in the correct order. This is the single entry point
for the UI — Streamlit calls run_pipeline() and receives a list of PlayerValue
objects ready for display.

Pipeline order:
  1. Fetch + normalize projections from all sources
  2. Build consensus projections
  3. Compute SGP denominators (Path 1 or Path 2)
  4. Pass 1: compute counting-stat SGP → rough ranking for replacement level
  5. Compute replacement level from rough ranking
  6. Pass 2: compute full SGP (counting + rate stats) with proper replacement level
  7. Compute final replacement level using full SGP ranking
  8. Recompute full SGP with final replacement level
  9. Convert SGP to dollars
  10. Apply keeper adjustments if applicable
  11. Return ranked PlayerValue list

The two-pass approach resolves the circular dependency between rate stat SGP
and replacement level without any arbitrary iteration — one iteration is
always sufficient in practice.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from fantasy_baseball.config.league_config import LeagueConfig
from fantasy_baseball.config.defaults import CNMFBL_CONFIG
from fantasy_baseball.data.fetcher import fetch_all_fg_projections, load_pecota_batting_csv, load_pecota_pitching_csv
from fantasy_baseball.data.normalizer import normalize_batting_df, normalize_pitching_df
from fantasy_baseball.data.reconciler import build_consensus, match_pecota_to_fg_ids
from fantasy_baseball.data.schema import ConsensusProjection, RawProjection
from fantasy_baseball.sgp.denominators import compute_sgp_denominators, SGPDenominators
from fantasy_baseball.sgp.replacement_level import compute_replacement_level, ReplacementLevel
from fantasy_baseball.sgp.counting_stats import counting_stat_sgp
from fantasy_baseball.sgp.rate_stats import rate_stat_sgp
from fantasy_baseball.valuation.dollar_value import compute_dollar_values, PlayerValue
from fantasy_baseball.valuation.keeper_logic import (
    apply_keeper_adjustments,
    KeeperEntry,
    KeeperMode,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineInputs:
    """All user-supplied inputs collected by the UI before running the pipeline."""
    config: LeagueConfig = field(default_factory=lambda: CNMFBL_CONFIG)

    # Data sources
    projection_systems: list[str] = field(
        default_factory=lambda: ["steamer", "zips", "atc", "depthcharts"]
    )
    pecota_batting_csv: str | None = None
    pecota_pitching_csv: str | None = None

    # SGP denominators
    standings_df: pd.DataFrame | None = None       # historical standings CSV
    sgp_overrides: dict[str, float] = field(default_factory=dict)

    # Keepers
    keeper_mode: KeeperMode = KeeperMode.NONE
    confirmed_keepers: list[KeeperEntry] = field(default_factory=list)

    # Player pool size
    player_limit: int = 500   # keep only the top N players by rough SGP; 0 = no limit


@dataclass
class PipelineResult:
    """Everything the UI needs to render the results page."""
    player_values: list[PlayerValue]
    denominators: SGPDenominators
    replacement_level: ReplacementLevel
    consensus_projections: list[ConsensusProjection]
    hitter_pool: float = 0.0   # effective hitter dollar pool (after keeper deductions)
    pitcher_pool: float = 0.0  # effective pitcher dollar pool (after keeper deductions)
    pre_keeper_dollar_values: dict[str, float] | None = None  # {fg_id: $} before keeper adjustments
    pre_keeper_total_sgp: dict[str, float] | None = None      # {fg_id: total_sgp} before keeper adjustments
    warnings: list[str] = field(default_factory=list)


def run_pipeline(inputs: PipelineInputs) -> PipelineResult:
    """
    Run the full valuation pipeline from raw inputs to dollar values.
    """
    warnings: list[str] = []
    config = inputs.config

    # ─── Step 1: Fetch and normalize projections ────────────────────────────
    logger.info("=== Step 1: Fetching projections ===")
    raw_fg = fetch_all_fg_projections(systems=inputs.projection_systems)

    raw_projections: list[RawProjection] = []
    for key, df in raw_fg.items():
        if key.startswith("batting_"):
            raw_projections.extend(normalize_batting_df(df))
        elif key.startswith("pitching_"):
            raw_projections.extend(normalize_pitching_df(df))

    # PECOTA (optional)
    if inputs.pecota_batting_csv:
        pecota_bat_df = load_pecota_batting_csv(inputs.pecota_batting_csv)
        pecota_bat_raw = normalize_batting_df(pecota_bat_df)
        # Match PECOTA players to FG IDs using FG projections as the reference
        fg_only = [p for p in raw_projections if p.source != "pecota"]
        pecota_bat_raw = match_pecota_to_fg_ids(pecota_bat_raw, fg_only)
        raw_projections.extend(pecota_bat_raw)

    if inputs.pecota_pitching_csv:
        pecota_pit_df = load_pecota_pitching_csv(inputs.pecota_pitching_csv)
        pecota_pit_raw = normalize_pitching_df(pecota_pit_df)
        fg_only = [p for p in raw_projections if p.source != "pecota"]
        pecota_pit_raw = match_pecota_to_fg_ids(pecota_pit_raw, fg_only)
        raw_projections.extend(pecota_pit_raw)

    # ─── Step 2: Build consensus ─────────────────────────────────────────────
    logger.info("=== Step 2: Building consensus projections ===")
    consensus = build_consensus(raw_projections)

    # ─── Step 3: SGP denominators ─────────────────────────────────────────────
    logger.info("=== Step 3: Computing SGP denominators ===")
    denominators = compute_sgp_denominators(
        config=config,
        standings_df=inputs.standings_df,
        user_overrides=inputs.sgp_overrides,
    )
    if denominators.source == "defaults":
        warnings.append(
            "Using generic default SGP denominators. Upload historical league standings "
            "for values calibrated to your specific league."
        )

    # ─── Step 4: Pass 1 — counting-stat SGP for rough ranking ────────────────
    logger.info("=== Step 4: Pass 1 — counting-stat SGP (bootstrap ranking) ===")
    rough_sgp: dict[str, float] = {}
    for player in consensus:
        cat_sgp = counting_stat_sgp(
            player=player,
            replacement=_zero_replacement(),
            denominators=denominators,
            config=config,
        )
        rough_sgp[player.fg_id] = sum(cat_sgp.values())

    # ─── Step 4b: Trim to player limit ────────────────────────────────────────
    if inputs.player_limit > 0 and len(consensus) > inputs.player_limit:
        # Sort hitters and pitchers separately so each type gets fair representation.
        # Split the limit 60/40 hitters/pitchers, mirroring the typical roster split.
        hitter_limit = round(inputs.player_limit * 0.60)
        pitcher_limit = inputs.player_limit - hitter_limit

        top_hitter_ids = {
            p.fg_id for p in sorted(
                [p for p in consensus if p.player_type == "hitter"],
                key=lambda p: rough_sgp.get(p.fg_id, 0),
                reverse=True,
            )[:hitter_limit]
        }
        top_pitcher_ids = {
            p.fg_id for p in sorted(
                [p for p in consensus if p.player_type == "pitcher"],
                key=lambda p: rough_sgp.get(p.fg_id, 0),
                reverse=True,
            )[:pitcher_limit]
        }
        top_ids = top_hitter_ids | top_pitcher_ids
        before = len(consensus)
        consensus = [p for p in consensus if p.fg_id in top_ids]
        rough_sgp = {fid: sgp for fid, sgp in rough_sgp.items() if fid in top_ids}
        logger.info("Player limit %d: trimmed %d → %d players", inputs.player_limit, before, len(consensus))

    # ─── Step 5: Replacement level from rough ranking ─────────────────────────
    logger.info("=== Step 5: Computing replacement level (Pass 1) ===")
    replacement_pass1 = compute_replacement_level(
        config=config,
        projections=consensus,
        initial_sgp_values=rough_sgp,
    )

    # ─── Step 6: Full SGP (counting + rate stats) using Pass 1 replacement ────
    logger.info("=== Step 6: Full SGP with Pass 1 replacement level ===")
    full_sgp_pass1, cat_sgp_pass1, pos_assignments_pass1 = _compute_full_sgp(
        consensus, replacement_pass1, denominators, config
    )

    # ─── Step 7: Final replacement level using full SGP ranking ───────────────
    logger.info("=== Step 7: Recomputing replacement level with full SGP ===")
    replacement_final = compute_replacement_level(
        config=config,
        projections=consensus,
        initial_sgp_values=full_sgp_pass1,
    )

    # ─── Step 8: Full SGP pass 2 ───────────────────────────────────────────────
    # Rank by full_sgp_pass1 so assignments use the same metric as dollar values.
    logger.info("=== Step 8: Full SGP pass 2 ===")
    full_sgp_pass2, cat_sgp_pass2, pos_assignments_pass2 = _compute_full_sgp(
        consensus, replacement_final, denominators, config,
        ranking_sgp=full_sgp_pass1,
    )

    # ─── Step 9: Converging pass — replacement level + SGP from pass 2 ────────
    # One extra iteration eliminates residual mismatches where a player's SGP
    # shifted between passes (e.g. rate-stat specialists like closers) causing
    # their assigned slot to diverge from their final dollar-value ranking.
    logger.info("=== Step 9: Converging pass ===")
    replacement_pass2 = compute_replacement_level(
        config=config,
        projections=consensus,
        initial_sgp_values=full_sgp_pass2,
    )
    full_sgp_final, cat_sgp_final, pos_assignments_final = _compute_full_sgp(
        consensus, replacement_pass2, denominators, config,
        ranking_sgp=full_sgp_pass2,
    )

    # ─── Step 10: Keeper adjustments ──────────────────────────────────────────
    available_consensus = consensus
    hitter_pool_override = None
    pitcher_pool_override = None
    hitter_slots_override = None
    pitcher_slots_override = None
    preliminary_values = None  # only set when keepers are active
    pre_keeper_dollar_values = None  # only set when keepers are active
    pre_keeper_total_sgp = None      # only set when keepers are active

    if inputs.keeper_mode != KeeperMode.NONE and inputs.confirmed_keepers:
        logger.info("=== Step 10: Applying keeper adjustments ===")
        # Build preliminary PlayerValue list to attach KeeperStatus
        preliminary_values = compute_dollar_values(
            config=config,
            projections=consensus,
            category_sgp_map=cat_sgp_final,
            position_assignments=pos_assignments_final,
        )
        # Snapshot pre-keeper values for display in the UI
        pre_keeper_dollar_values = {pv.fg_id: pv.dollar_value for pv in preliminary_values}
        pre_keeper_total_sgp = {pv.fg_id: pv.total_sgp for pv in preliminary_values}

        available_consensus, hitter_pool_override, pitcher_pool_override, hitter_slots_override, pitcher_slots_override = apply_keeper_adjustments(
            config=config,
            projections=consensus,
            player_values=preliminary_values,
            confirmed_keepers=inputs.confirmed_keepers,
        )

        # SGP is NOT recomputed after keeper removal. Replacement level and individual
        # SGP values reflect the full league — keepers occupy roster spots but don't
        # change the replacement standard. Recomputing SGP after removal lowers the
        # replacement baseline, inflating total SGP pool-wide and incorrectly diluting
        # each remaining player's share of a smaller dollar pool, causing values to drop.
        # Using pre-keeper SGP ensures available players correctly benefit from reduced
        # pool competition: fewer dollars, fewer players, same SGP distribution.

    # ─── Step 11: Dollar conversion ────────────────────────────────────────────
    logger.info("=== Step 11: Converting to dollar values ===")
    if preliminary_values is not None:
        # Keepers active: distribute the reduced dollar pool only among available
        # players. Keeper entries (with KeeperStatus already set) are merged back
        # separately so they appear in the output but don't dilute the auction pool.
        available_values = compute_dollar_values(
            config=config,
            projections=available_consensus,
            category_sgp_map=cat_sgp_final,
            position_assignments=pos_assignments_final,
            hitter_pool_override=hitter_pool_override,
            pitcher_pool_override=pitcher_pool_override,
            hitter_slots_override=hitter_slots_override,
            pitcher_slots_override=pitcher_slots_override,
        )
        keeper_fg_ids = {k.fg_id for k in inputs.confirmed_keepers}
        keeper_values = [pv for pv in preliminary_values if pv.fg_id in keeper_fg_ids]
        player_values = sorted(
            available_values + keeper_values,
            key=lambda pv: (pv.dollar_value, pv.total_sgp),
            reverse=True,
        )
    else:
        player_values = compute_dollar_values(
            config=config,
            projections=consensus,
            category_sgp_map=cat_sgp_final,
            position_assignments=pos_assignments_final,
        )

    logger.info("Pipeline complete. %d players valued.", len(player_values))

    return PipelineResult(
        player_values=player_values,
        denominators=denominators,
        replacement_level=replacement_final,
        consensus_projections=consensus,
        hitter_pool=hitter_pool_override if hitter_pool_override is not None else config.hitter_pool_dollars,
        pitcher_pool=pitcher_pool_override if pitcher_pool_override is not None else config.pitcher_pool_dollars,
        pre_keeper_dollar_values=pre_keeper_dollar_values,
        pre_keeper_total_sgp=pre_keeper_total_sgp,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_full_sgp(
    projections: list[ConsensusProjection],
    replacement: ReplacementLevel,
    denominators: SGPDenominators,
    config: LeagueConfig,
    ranking_sgp: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, dict[str, float]], dict[str, str]]:
    """
    Compute full SGP (counting + rate) for all players.

    ranking_sgp : if provided, use these values for position assignment ranking
        instead of recomputing counting-only SGP. Pass the previous pass's full
        SGP to ensure rostering decisions match the final dollar-value metric.

    Returns:
      total_sgp_by_id   : {fg_id: total_sgp}
      category_sgp_by_id: {fg_id: {category: sgp}}
      position_by_id    : {fg_id: assigned_position}
    """
    from fantasy_baseball.sgp.replacement_level import (
        assign_hitter_positions,
        assign_pitcher_positions,
        rank_players,
    )

    hitters = [p for p in projections if p.player_type == "hitter"]
    pitchers = [p for p in projections if p.player_type == "pitcher"]

    if ranking_sgp:
        # Use provided SGP values (full, from previous pass) for ranking
        sgp_for_ranking = ranking_sgp
    else:
        # First pass: no prior full SGP available; rank by counting stats only
        sgp_for_ranking = {}
        for p in projections:
            cat_sgp = counting_stat_sgp(
                p,
                replacement=replacement,
                denominators=denominators,
                config=config,
            )
            sgp_for_ranking[p.fg_id] = sum(cat_sgp.values())

    hitter_rank = rank_players(hitters, sgp_for_ranking, "hitter")
    pitcher_rank = rank_players(pitchers, sgp_for_ranking, "pitcher")

    _, hitter_assignments = assign_hitter_positions(hitter_rank, config)
    _, pitcher_assignments = assign_pitcher_positions(pitcher_rank, config)

    position_by_id: dict[str, str] = {}
    for pos, players in {**hitter_assignments, **pitcher_assignments}.items():
        for p in players:
            position_by_id[p.fg_id] = pos
    # Players not assigned to any active slot go to BN
    for p in projections:
        if p.fg_id not in position_by_id:
            position_by_id[p.fg_id] = "BN"

    category_sgp_by_id: dict[str, dict[str, float]] = {}
    total_sgp_by_id: dict[str, float] = {}

    for player in projections:
        pos = position_by_id.get(player.fg_id)
        cat_sgp = counting_stat_sgp(player, replacement, denominators, config, assigned_position=pos)
        rate_sgp = rate_stat_sgp(player, replacement, denominators, config)
        all_sgp = {**cat_sgp, **rate_sgp}
        category_sgp_by_id[player.fg_id] = all_sgp
        total_sgp_by_id[player.fg_id] = sum(all_sgp.values())

    return total_sgp_by_id, category_sgp_by_id, position_by_id



def _zero_replacement() -> ReplacementLevel:
    """Placeholder replacement level with zeros — used only for rough pass."""
    return ReplacementLevel(
        hitter_replacement={},
        pitcher_replacement={},
        by_position={},
        team_pa=1.0,
        team_obp_numerator=0.0,
        team_avg_numerator=0.0,
        team_ab=1.0,
        team_ip=1.0,
        team_era_er=0.0,
        team_whip_bb_h=0.0,
    )
