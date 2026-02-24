"""
SGP contribution for counting stats.

Formula: (projected_stat - replacement_stat) / sgp_denominator

Replacement stats are pool-level (all hitters share one replacement baseline,
all pitchers share one). Slot assignment does not affect replacement level.
"""

from __future__ import annotations

from ..config.league_config import LeagueConfig
from ..data.schema import ConsensusProjection
from .denominators import SGPDenominators
from .replacement_level import ReplacementLevel


def counting_stat_sgp(
    player: ConsensusProjection,
    replacement: ReplacementLevel,
    denominators: SGPDenominators,
    config: LeagueConfig,
) -> dict[str, float]:
    """
    Compute per-category SGP contribution for all counting stats.

    Returns a dict of {category: sgp_contribution}.
    Only includes counting stats (rate stats are handled separately).
    """
    if player.player_type == "hitter":
        repl_stats = replacement.hitter_replacement
        categories = [c for c in config.categories.counting_stats if c in config.categories.hitting]
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
