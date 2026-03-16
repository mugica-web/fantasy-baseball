"""
Live draft valuation: recompute dollar values as picks are made during an auction draft.

The SGP scores don't change — only the available dollar pool shrinks as players get
drafted. We redistribute the remaining marginal dollars among undrafted players.

Formula:
  - Each drafted player consumed ($price - $1) of the marginal pool (the $1 is the
    per-slot floor that is always reserved).
  - remaining_marginal = base_marginal - sum(price - 1 for each pick in that pool)
  - Undrafted players share the remaining marginal proportionally by total SGP,
    exactly as in the original dollar_value calculation.
"""

from __future__ import annotations
from dataclasses import dataclass

from ..config.league_config import LeagueConfig
from .dollar_value import PlayerValue


@dataclass
class DraftPick:
    fg_id: str
    name: str
    price: int


def compute_live_values(
    player_values: list[PlayerValue],
    draft_picks: list[DraftPick],
    base_hitter_pool: float,
    base_pitcher_pool: float,
    config: LeagueConfig,
) -> dict[str, float]:
    """
    Recompute dollar values given the picks made so far.

    Returns {fg_id: live_dollar_value} for every player:
      - Undrafted available players : redistributed values based on remaining pool
      - Drafted players             : their actual draft price
      - Keepers                     : their pre-draft dollar value (unchanged)

    base_hitter_pool / base_pitcher_pool should be the pools actually used by the
    pipeline (i.e. after any keeper deductions), stored in PipelineResult.
    """
    pv_by_id = {pv.fg_id: pv for pv in player_values}
    drafted_ids = {pick.fg_id for pick in draft_picks}

    # Split picks by player type
    hitter_picks = [
        p for p in draft_picks
        if pv_by_id.get(p.fg_id) and pv_by_id[p.fg_id].player_type == "hitter"
    ]
    pitcher_picks = [
        p for p in draft_picks
        if pv_by_id.get(p.fg_id) and pv_by_id[p.fg_id].player_type == "pitcher"
    ]

    # Original marginal pools (pool minus $1-per-slot floor)
    hitter_floor = config.num_teams * config.roster.total_hitter_slots
    pitcher_floor = config.num_teams * config.roster.total_pitcher_slots
    base_hitter_marginal = max(base_hitter_pool - hitter_floor, 0.0)
    base_pitcher_marginal = max(base_pitcher_pool - pitcher_floor, 0.0)

    # Each drafted player consumed (price - 1) of the marginal pool
    hitter_marginal_spent = sum(max(p.price - 1, 0) for p in hitter_picks)
    pitcher_marginal_spent = sum(max(p.price - 1, 0) for p in pitcher_picks)

    remaining_hitter_marginal = max(base_hitter_marginal - hitter_marginal_spent, 0.0)
    remaining_pitcher_marginal = max(base_pitcher_marginal - pitcher_marginal_spent, 0.0)

    # Undrafted, available (non-keeper) players with positive SGP
    undrafted_available = [
        pv for pv in player_values
        if pv.fg_id not in drafted_ids and pv.is_available
    ]
    rostered_hitters = [
        pv for pv in undrafted_available
        if pv.player_type == "hitter" and pv.total_sgp > 0
    ]
    rostered_pitchers = [
        pv for pv in undrafted_available
        if pv.player_type == "pitcher" and pv.total_sgp > 0
    ]

    live_values: dict[str, float] = {}

    # Redistribute remaining marginal proportionally by SGP
    total_hitter_sgp = sum(pv.total_sgp for pv in rostered_hitters)
    if total_hitter_sgp > 0:
        for pv in rostered_hitters:
            share = (pv.total_sgp / total_hitter_sgp) * remaining_hitter_marginal
            live_values[pv.fg_id] = round(1.0 + share, 2)

    total_pitcher_sgp = sum(pv.total_sgp for pv in rostered_pitchers)
    if total_pitcher_sgp > 0:
        for pv in rostered_pitchers:
            share = (pv.total_sgp / total_pitcher_sgp) * remaining_pitcher_marginal
            live_values[pv.fg_id] = round(1.0 + share, 2)

    # Undrafted players with zero/negative SGP get the $1 floor
    for pv in undrafted_available:
        if pv.fg_id not in live_values:
            live_values[pv.fg_id] = 1.0

    # Drafted players: their actual price
    for pick in draft_picks:
        live_values[pick.fg_id] = float(pick.price)

    # Keepers and any remaining players: preserve pre-draft value
    for pv in player_values:
        if pv.fg_id not in live_values:
            live_values[pv.fg_id] = pv.dollar_value

    return live_values
