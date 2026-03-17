"""
Convert SGP values to auction dollar values.

Algorithm:
  1. Split players into hitter and pitcher pools.
  2. Assign a $1 minimum floor to every active roster slot (the minimum bid
     any player can receive at auction). This floor is deducted from each pool
     before proportional distribution.
  3. Distribute the remaining pool dollars proportionally to players with
     positive total SGP. Players with zero or negative SGP receive only $1.
  4. Final dollar value = $1 + (player_sgp / sum_positive_pool_sgp) × remaining_pool

When keepers are active, keeper salaries are subtracted from the pool and
kept players are removed from the proportional calculation before calling
this function (handled in keeper_logic.py).

Dollar values are rounded to two decimal places. No player receives less than $1.
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
    dollar_value: float                # auction value ($1 minimum)
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
) -> list[PlayerValue]:
    """
    Convert per-player SGP contributions to auction dollar values.

    category_sgp_map     : {fg_id: {category: sgp}} from the SGP calculation pass
    position_assignments : {fg_id: assigned_position} from replacement level
    hitter_pool_override : use when keeper salaries have been subtracted (keeper mode)
    pitcher_pool_override: same for pitchers
    hitter_slots_override: remaining hitter auction slots after keeper slots are filled;
                           caps the positive-SGP pool so total distributed = pool exactly
    pitcher_slots_override: same for pitchers
    """
    hitter_pool = hitter_pool_override if hitter_pool_override is not None else config.hitter_pool_dollars
    pitcher_pool = pitcher_pool_override if pitcher_pool_override is not None else config.pitcher_pool_dollars

    hitter_slots = hitter_slots_override if hitter_slots_override is not None else config.active_hitter_slots
    pitcher_slots = pitcher_slots_override if pitcher_slots_override is not None else config.active_pitcher_slots

    proj_by_id = {p.fg_id: p for p in projections}

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

    # Cap each pool to the number of auctionable slots, sorted by SGP descending.
    # This ensures total_distributed = slots × $1 + marginal = pool exactly.
    # Players outside the cap receive $0 — they would not be drafted at auction.
    positive_hitters = sorted(
        [pv for pv in player_values if pv.player_type == "hitter" and pv.total_sgp > 0],
        key=lambda pv: pv.total_sgp, reverse=True,
    )
    positive_pitchers = sorted(
        [pv for pv in player_values if pv.player_type == "pitcher" and pv.total_sgp > 0],
        key=lambda pv: pv.total_sgp, reverse=True,
    )

    rostered_hitters = positive_hitters[:hitter_slots]
    rostered_pitchers = positive_pitchers[:pitcher_slots]

    # Floor = actual number of capped players (may be < slots if pool is shallow)
    hitter_floor = len(rostered_hitters)
    pitcher_floor = len(rostered_pitchers)

    hitter_marginal = max(hitter_pool - hitter_floor, 0.0)
    pitcher_marginal = max(pitcher_pool - pitcher_floor, 0.0)

    logger.info(
        "Dollar pools — hitters: $%.0f (%d slots, floor $%d, marginal $%.0f) | "
        "pitchers: $%.0f (%d slots, floor $%d, marginal $%.0f)",
        hitter_pool, hitter_slots, hitter_floor, hitter_marginal,
        pitcher_pool, pitcher_slots, pitcher_floor, pitcher_marginal,
    )

    _assign_dollars(rostered_hitters, hitter_marginal)
    _assign_dollars(rostered_pitchers, pitcher_marginal)

    return sorted(player_values, key=lambda pv: (pv.dollar_value, pv.total_sgp), reverse=True)


def _assign_dollars(
    rostered_players: list[PlayerValue],
    marginal_pool: float,
) -> None:
    """
    Assign dollar values in-place for a pool of players.

    Players with positive SGP share the marginal pool proportionally.
    Players at or below zero SGP receive only the $1 floor (already set).
    """
    positive = [pv for pv in rostered_players if pv.total_sgp > 0]
    total_positive_sgp = sum(pv.total_sgp for pv in positive)

    if total_positive_sgp == 0:
        return  # all players at floor — nothing to distribute

    for pv in positive:
        share = (pv.total_sgp / total_positive_sgp) * marginal_pool
        pv.dollar_value = round(1.0 + share, 2)
