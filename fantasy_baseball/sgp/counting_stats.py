"""
SGP contribution for counting stats.

Formula: (projected_stat - replacement_stat) / sgp_denominator

Replacement stats are position-specific for dedicated slots (C, 1B, 2B, 3B,
SS, OF, SP, RP): the last-rostered player at that position is the baseline.
Players filling UTIL, P-flex, or BN slots use the pool-level replacement
(last-rostered player across the entire hitter/pitcher pool).

This prevents catchers from being penalized for low SB relative to all hitters
when the relevant comparison is against replacement-level catchers.
"""

from __future__ import annotations

from ..config.league_config import LeagueConfig
from ..data.schema import ConsensusProjection
from .denominators import SGPDenominators
from .replacement_level import ReplacementLevel


_UTIL_SLOTS = {"UTIL", "P", "BN"}  # slots that use pool-level replacement


def counting_stat_sgp(
    player: ConsensusProjection,
    replacement: ReplacementLevel,
    denominators: SGPDenominators,
    config: LeagueConfig,
    assigned_position: str | None = None,
) -> dict[str, float]:
    """
    Compute per-category SGP contribution for all counting stats.

    assigned_position : the slot this player fills (e.g. "C", "OF", "SP").
        When provided and the slot is a dedicated position with a known
        per-position replacement level, that replacement is used instead of
        the pool-level baseline. UTIL / P-flex / BN always use the pool level.

    Returns a dict of {category: sgp_contribution}.
    Only includes counting stats (rate stats are handled separately).
    """
    use_pool = assigned_position is None or assigned_position in _UTIL_SLOTS

    if player.player_type == "hitter":
        if not use_pool and assigned_position in replacement.by_position:
            repl_stats = replacement.by_position[assigned_position]
        else:
            repl_stats = replacement.hitter_replacement
        categories = [c for c in config.categories.counting_stats if c in config.categories.hitting]
    else:
        if not use_pool and assigned_position in replacement.by_position:
            repl_stats = replacement.by_position[assigned_position]
        else:
            repl_stats = replacement.pitcher_replacement
        categories = [c for c in config.categories.counting_stats if c in config.categories.pitching]

    contributions: dict[str, float] = {}
    for cat in categories:
        proj_val = player.get_stat(cat)
        repl_val = repl_stats.get(cat, 0.0)
        denom = denominators.get(cat)

        if denom == 0:
            contributions[cat] = 0.0
        else:
            contributions[cat] = (proj_val - repl_val) / denom

    return contributions


def total_counting_sgp(
    player: ConsensusProjection,
    replacement: ReplacementLevel,
    denominators: SGPDenominators,
    config: LeagueConfig,
) -> float:
    """Sum of counting stat SGP contributions across all relevant categories."""
    return sum(
        counting_stat_sgp(player, replacement, denominators, config).values()
    )
