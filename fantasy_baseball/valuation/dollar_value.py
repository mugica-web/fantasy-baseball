"""
Convert SGP values to auction dollar values.

Algorithm:
  1. Split players into hitter and pitcher pools.
  2. Include all rostered players (active + bench) sorted by SGP descending,
     capped to effective_total slots.
  3. Assign a participation weight to each player:
       - Active-slot players (top active_slots by SGP): weight = 1.0
       - Bench players: weight decays linearly from a type-specific maximum
         down to a minimum as bench depth increases.
         Pitchers decay from 0.65 → 0.20 (streamable by matchup).
         Hitters decay from 0.40 → 0.05 (spot-starts / injury fill-ins).
  4. Weighted floor = Σ weight_i  (replaces the old integer slot count)
  5. Marginal pool = total_pool - weighted_floor
  6. Each player's dollar value = weight_i + (sgp_i × weight_i / Σ(sgp_j × weight_j)) × marginal
     This guarantees Σ dollar_values = total_pool exactly.

When keepers are active, keeper salaries are subtracted from the pool and
kept players are removed from the proportional calculation before calling
this function (handled in keeper_logic.py).

Dollar values are rounded to two decimal places.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .keeper_logic import KeeperStatus

from ..config.league_config import LeagueConfig
from ..data.schema import ConsensusProjection

logger = logging.getLogger(__name__)

# Bench participation rates: (top_of_bench, bottom_of_bench)
# Represents expected fraction of a full-season stat contribution.
# Pitchers are higher because they can be streamed by favourable matchup.
_BENCH_PITCHER_UTILIZATION = (0.65, 0.20)
_BENCH_HITTER_UTILIZATION  = (0.40, 0.05)


@dataclass
class PlayerValue:
    fg_id: str
    name: str
    team: str
    positions: list[str]
    player_type: Literal["hitter", "pitcher"]
    is_dh_only: bool
    consensus_stats: dict[str, float]
    category_sgp: dict[str, float]     # per-category SGP contribution
    total_sgp: float
    dollar_value: float                # auction value
    assigned_position: str             # which slot this player fills
    sources_available: list[str] = field(default_factory=list)
    keeper_status: KeeperStatus | None = None   # set by keeper_logic

    @property
    def is_available(self) -> bool:
        """False if player is confirmed kept by another team."""
        if self.keeper_status is None:
            return True
        return self.keeper_status.is_available


def compute_dollar_values(
    config: LeagueConfig,
    projections: list[ConsensusProjection],
    category_sgp_map: dict[str, dict[str, float]],
    position_assignments: dict[str, str],   # {fg_id: assigned_position}
    hitter_pool_override: float | None = None,
    pitcher_pool_override: float | None = None,
    hitter_slots_override: int | None = None,
    pitcher_slots_override: int | None = None,
    hitter_active_override: int | None = None,
    pitcher_active_override: int | None = None,
) -> list[PlayerValue]:
    """
    Convert per-player SGP contributions to auction dollar values.

    hitter_slots_override  : total pool cap (active + bench - keepers) in keeper mode
    pitcher_slots_override : same for pitchers
    hitter_active_override : active/bench boundary in keeper mode
    pitcher_active_override: same for pitchers
    """
    hitter_pool = hitter_pool_override if hitter_pool_override is not None else config.hitter_pool_dollars
    pitcher_pool = pitcher_pool_override if pitcher_pool_override is not None else config.pitcher_pool_dollars

    # Total players eligible for dollar distribution (active + bench)
    hitter_slots = hitter_slots_override if hitter_slots_override is not None else config.effective_total_hitter_slots
    pitcher_slots = pitcher_slots_override if pitcher_slots_override is not None else config.effective_total_pitcher_slots

    # Active/bench boundary — bench players receive discounted participation weights
    hitter_active = hitter_active_override if hitter_active_override is not None else config.active_hitter_slots
    pitcher_active = pitcher_active_override if pitcher_active_override is not None else config.active_pitcher_slots

    # Build PlayerValue objects
    player_values: list[PlayerValue] = []
    for proj in projections:
        cat_sgp = category_sgp_map.get(proj.fg_id, {})
        total_sgp = sum(cat_sgp.values())
        assigned_pos = position_assignments.get(proj.fg_id, "BN")

        player_values.append(
            PlayerValue(
                fg_id=proj.fg_id,
                name=proj.name,
                team=proj.team,
                positions=proj.positions,
                player_type=proj.player_type,
                is_dh_only=proj.is_dh_only,
                consensus_stats=proj.stats.copy(),
                category_sgp=cat_sgp,
                total_sgp=total_sgp,
                dollar_value=0.0,
                assigned_position=assigned_pos,
                sources_available=list(proj.sources_available),
            )
        )

    # Sort by SGP descending, cap to total slots
    positive_hitters = sorted(
        [pv for pv in player_values if pv.player_type == "hitter" and pv.total_sgp > 0],
        key=lambda pv: pv.total_sgp, reverse=True,
    )[:hitter_slots]

    positive_pitchers = sorted(
        [pv for pv in player_values if pv.player_type == "pitcher" and pv.total_sgp > 0],
        key=lambda pv: pv.total_sgp, reverse=True,
    )[:pitcher_slots]

    hitter_weights = _participation_weights(positive_hitters, hitter_active)
    pitcher_weights = _participation_weights(positive_pitchers, pitcher_active)

    hitter_floor = sum(hitter_weights[pv.fg_id] for pv in positive_hitters)
    pitcher_floor = sum(pitcher_weights[pv.fg_id] for pv in positive_pitchers)

    hitter_marginal = max(hitter_pool - hitter_floor, 0.0)
    pitcher_marginal = max(pitcher_pool - pitcher_floor, 0.0)

    logger.info(
        "Dollar pools — hitters: $%.0f (%d total / %d active, floor $%.1f, marginal $%.0f) | "
        "pitchers: $%.0f (%d total / %d active, floor $%.1f, marginal $%.0f)",
        hitter_pool, len(positive_hitters), hitter_active, hitter_floor, hitter_marginal,
        pitcher_pool, len(positive_pitchers), pitcher_active, pitcher_floor, pitcher_marginal,
    )

    _assign_dollars(positive_hitters, hitter_marginal, hitter_weights)
    _assign_dollars(positive_pitchers, pitcher_marginal, pitcher_weights)

    return sorted(player_values, key=lambda pv: (pv.dollar_value, pv.total_sgp), reverse=True)


def _participation_weights(
    rostered_players: list[PlayerValue],
    active_cutoff: int,
) -> dict[str, float]:
    """
    Compute participation weights for a sorted (best→worst) player list.

    Active players (rank < active_cutoff) get weight 1.0.
    Bench players get a linearly decaying weight based on depth and type.
    """
    bench_players = rostered_players[active_cutoff:]
    bench_size = len(bench_players)

    weights: dict[str, float] = {}
    for rank, pv in enumerate(rostered_players):
        if rank < active_cutoff:
            weights[pv.fg_id] = 1.0
        else:
            bench_rank = rank - active_cutoff
            top, bottom = (
                _BENCH_PITCHER_UTILIZATION if pv.player_type == "pitcher"
                else _BENCH_HITTER_UTILIZATION
            )
            frac = bench_rank / max(bench_size - 1, 1)  # 0.0 → 1.0 across bench depth
            weights[pv.fg_id] = round(top + frac * (bottom - top), 4)

    return weights


def _assign_dollars(
    rostered_players: list[PlayerValue],
    marginal_pool: float,
    weights: dict[str, float],
) -> None:
    """
    Assign dollar values in-place.

    dollar_value_i = weight_i + (sgp_i × weight_i / Σ(sgp_j × weight_j)) × marginal_pool

    This guarantees Σ dollar_values = Σ weights + marginal_pool = total_pool.
    """
    positive = [pv for pv in rostered_players if pv.total_sgp > 0]
    total_weighted_sgp = sum(pv.total_sgp * weights.get(pv.fg_id, 1.0) for pv in positive)

    if total_weighted_sgp == 0:
        return

    for pv in positive:
        w = weights.get(pv.fg_id, 1.0)
        share = (pv.total_sgp * w / total_weighted_sgp) * marginal_pool
        pv.dollar_value = round(w + share, 2)
