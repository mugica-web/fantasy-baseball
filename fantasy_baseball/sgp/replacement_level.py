"""
Replacement level computation.

Replacement level is the projected stats of the last-rostered player in the
hitter or pitcher pool, given league roster construction. This flows entirely
from LeagueConfig — no hardcoded values.

Position assignment algorithm (Phase 1 → Phase 2 → Phase 3):

Phase 1 — Fill dedicated positions (C, 1B, 2B, 3B, SS, OF) using a
  scarcity-ordered greedy assignment:
    - Positions with fewer eligible players are filled first (e.g. C before OF)
    - Within each position, take the highest-valued unassigned eligible players
    - "Value" for initial ranking uses counting-stat SGP only (rate stats handled
      separately to avoid the circular dependency)
    - This is run BEFORE rate stats are computed; pipeline iterates once.

Phase 2 — Fill UTIL slots from all unassigned hitters (including DH-only players).
  DH-only players (Ohtani, Schwarber, etc.) enter competition here on equal
  footing with positional overflow — they are not penalized or excluded.

Phase 3 — Derive replacement level stats and team aggregates from the rostered set.

Pitchers follow the same pattern: SP slots filled first, then RP slots, then P flex.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from ..config.league_config import LeagueConfig
from ..data.schema import ConsensusProjection

logger = logging.getLogger(__name__)


@dataclass
class ReplacementLevel:
    """
    Replacement-level stats and team rate-stat aggregates.

    hitter_replacement / pitcher_replacement : pool-level replacement stats used
        in SGP calculation — the stats of the last-rostered hitter/pitcher overall
        (N = teams × total active hitter/pitcher slots). These are NOT
        per-position; every hitter uses hitter_replacement and every pitcher uses
        pitcher_replacement regardless of slot assignment.

    by_position : per-position replacement stats kept for display/debugging only.
        NOT used in SGP calculation.

    Team aggregates are derived from ALL rostered players (dedicated + UTIL),
    not just replacement-level players. They represent the "typical team" that
    a marginal player is being added to for rate stat valuation.
    """

    # Pool-level replacement stats used in SGP calculation
    hitter_replacement: dict[str, float]   # stats of last-rostered hitter overall
    pitcher_replacement: dict[str, float]  # stats of last-rostered pitcher overall

    # Per-position replacement-level stats (last rostered player at each slot) — display only
    by_position: dict[str, dict[str, float]]

    # Aggregate hitter team metrics (for rate stat marginal math)
    team_pa: float              # total PA across all rostered hitter slots (per team)
    team_obp_numerator: float   # sum(H + BB + HBP) across all rostered hitters (per team)
    team_avg_numerator: float   # sum(H) across all rostered hitters (per team)
    team_ab: float              # sum(AB) for AVG denominator (per team)

    # Aggregate pitcher team metrics
    team_ip: float              # total IP across all rostered pitcher slots (per team)
    team_era_er: float          # sum(ER) across all rostered pitchers (per team)
    team_whip_bb_h: float       # sum(BB + H) across all rostered pitchers (per team)

    # Display-only: names of the replacement-level players
    hitter_replacement_name: str = ""
    pitcher_replacement_name: str = ""
    by_position_names: dict[str, str] = field(default_factory=dict)

    @property
    def team_obp(self) -> float:
        if self.team_pa == 0:
            return 0.0
        return self.team_obp_numerator / self.team_pa

    @property
    def team_avg(self) -> float:
        if self.team_ab == 0:
            return 0.0
        return self.team_avg_numerator / self.team_ab

    @property
    def team_era(self) -> float:
        if self.team_ip == 0:
            return 0.0
        return (self.team_era_er / self.team_ip) * 9.0

    @property
    def team_whip(self) -> float:
        if self.team_ip == 0:
            return 0.0
        return self.team_whip_bb_h / self.team_ip



def compute_replacement_level(
    config: LeagueConfig,
    projections: list[ConsensusProjection],
    initial_sgp_values: dict[str, float] | None = None,
) -> ReplacementLevel:
    """
    Compute replacement level from league config and consensus projections.

    initial_sgp_values : optional dict of {fg_id: total_sgp} from a prior pass
        (counting stats only). If None, players are ranked by a simple proxy
        (HR + RBI + SB for hitters; K + SV + W for pitchers).

    This function is called twice by the pipeline:
      Pass 1: initial_sgp_values=None → rough ranking to bootstrap rate stats
      Pass 2: initial_sgp_values=counting_stat_sgp → refined replacement level
    """
    hitters = [p for p in projections if p.player_type == "hitter"]
    pitchers = [p for p in projections if p.player_type == "pitcher"]

    hitter_rank = rank_players(hitters, initial_sgp_values, "hitter")
    pitcher_rank = rank_players(pitchers, initial_sgp_values, "pitcher")

    rostered_hitters, position_assignments = assign_hitter_positions(
        hitter_rank, config
    )
    rostered_pitchers, pitcher_assignments = assign_pitcher_positions(
        pitcher_rank, config
    )

    by_position: dict[str, dict[str, float]] = {}
    by_position_names: dict[str, str] = {}

    # Replacement stats per hitter position (display/debugging only)
    for pos, assigned in position_assignments.items():
        if not assigned:
            continue
        last_rostered = assigned[-1]  # worst player who made the roster
        by_position[pos] = last_rostered.stats.copy()
        by_position_names[pos] = last_rostered.name

    # Replacement stats per pitcher position (display/debugging only)
    for pos, assigned in pitcher_assignments.items():
        if not assigned:
            continue
        last_rostered = assigned[-1]
        by_position[pos] = last_rostered.stats.copy()
        by_position_names[pos] = last_rostered.name

    # Phase 3: bench — extend the rostered pool by effective BN slots (IL excluded).
    # IL slots hold injured players who produce no stats and should not anchor
    # replacement level. Only real bench spots count toward pool depth.
    n = config.num_teams
    bench_needed = config.roster.effective_bench_slots * n
    if bench_needed > 0:
        hitter_bench_count = config.roster.effective_bench_hitter_slots * n
        pitcher_bench_count = config.roster.effective_bench_pitcher_slots * n

        active_hitter_ids = {p.fg_id for p in rostered_hitters}
        active_pitcher_ids = {p.fg_id for p in rostered_pitchers}
        bench_hitters = [p for p in hitter_rank if p.fg_id not in active_hitter_ids][:hitter_bench_count]
        bench_pitchers = [p for p in pitcher_rank if p.fg_id not in active_pitcher_ids][:pitcher_bench_count]
    else:
        bench_hitters = []
        bench_pitchers = []

    all_rostered_hitters = rostered_hitters + bench_hitters
    all_rostered_pitchers = rostered_pitchers + bench_pitchers

    # Team aggregates — per team (divide by num_teams).
    # Include bench players: they accumulate PA/IP through streaming and spot starts,
    # so the full rostered pool better represents the average team's aggregate stats.
    team_pa = sum(p.get_stat("PA") for p in all_rostered_hitters) / n
    team_obp_num = sum(
        p.get_stat("H") + p.get_stat("BB") + p.get_stat("HBP")
        for p in all_rostered_hitters
    ) / n
    team_avg_num = sum(p.get_stat("H") for p in all_rostered_hitters) / n
    team_ab = sum(p.get_stat("AB") for p in all_rostered_hitters) / n

    team_ip = sum(p.get_stat("IP") for p in all_rostered_pitchers) / n
    team_era_er = sum(p.get_stat("ER") for p in all_rostered_pitchers) / n
    team_whip_bb_h = sum(
        p.get_stat("BB") + p.get_stat("H") for p in all_rostered_pitchers
    ) / n

    # Pool-level replacement: last-rostered player in the full pool (active + bench)
    hitter_replacement = all_rostered_hitters[-1].stats.copy() if all_rostered_hitters else {}
    hitter_replacement_name = all_rostered_hitters[-1].name if all_rostered_hitters else ""
    pitcher_replacement = all_rostered_pitchers[-1].stats.copy() if all_rostered_pitchers else {}
    pitcher_replacement_name = all_rostered_pitchers[-1].name if all_rostered_pitchers else ""

    logger.info(
        "Replacement level — team OBP: %.3f, team ERA: %.2f, team WHIP: %.3f",
        team_obp_num / team_pa if team_pa else 0,
        (team_era_er / team_ip * 9) if team_ip else 0,
        team_whip_bb_h / team_ip if team_ip else 0,
    )

    return ReplacementLevel(
        hitter_replacement=hitter_replacement,
        pitcher_replacement=pitcher_replacement,
        by_position=by_position,
        hitter_replacement_name=hitter_replacement_name,
        pitcher_replacement_name=pitcher_replacement_name,
        by_position_names=by_position_names,
        team_pa=team_pa,
        team_obp_numerator=team_obp_num,
        team_avg_numerator=team_avg_num,
        team_ab=team_ab,
        team_ip=team_ip,
        team_era_er=team_era_er,
        team_whip_bb_h=team_whip_bb_h,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def rank_players(
    players: list[ConsensusProjection],
    sgp_values: dict[str, float] | None,
    player_type: Literal["hitter", "pitcher"],
) -> list[ConsensusProjection]:
    """Return players sorted best-to-worst by SGP (or proxy if SGP not yet computed)."""
    if sgp_values:
        return sorted(players, key=lambda p: sgp_values.get(p.fg_id, 0.0), reverse=True)

    # Proxy ranking before SGP is available
    if player_type == "hitter":
        def proxy(p: ConsensusProjection) -> float:
            return p.get_stat("HR") + p.get_stat("RBI") + p.get_stat("R") + p.get_stat("SB")
    else:
        def proxy(p: ConsensusProjection) -> float:
            return p.get_stat("K") / 10 + p.get_stat("SV") + p.get_stat("W")

    return sorted(players, key=proxy, reverse=True)


def assign_hitter_positions(
    ranked_hitters: list[ConsensusProjection],
    config: LeagueConfig,
) -> tuple[list[ConsensusProjection], dict[str, list[ConsensusProjection]]]:
    """
    Two-phase position assignment for hitters.

    Phase 1: Fill dedicated positions (C, 1B, 2B, 3B, SS, OF) in scarcity order.
    Phase 2: Fill UTIL slots with remaining players (including DH-only).

    Returns (all_rostered, {position: [assigned_players_best_to_worst]}).
    """
    dedicated_slots = config.roster.dedicated_hitter_slots  # {'C':1, '1B':1, ...}
    util_slots = config.roster.util_slots
    n = config.num_teams

    # Count eligible players per position to determine scarcity
    eligible_counts = {
        pos: sum(1 for p in ranked_hitters if pos in p.positions)
        for pos in dedicated_slots
    }
    # Fill scarcer positions first (ascending eligible count)
    fill_order = sorted(
        dedicated_slots.keys(),
        key=lambda pos: eligible_counts.get(pos, 0),
    )

    assigned: set[str] = set()  # fg_ids already placed
    position_assignments: dict[str, list[ConsensusProjection]] = {
        pos: [] for pos in list(dedicated_slots.keys()) + ["UTIL"]
    }

    # Phase 1: dedicated positions
    for pos in fill_order:
        slots_needed = dedicated_slots[pos] * n
        eligible = [p for p in ranked_hitters if pos in p.positions and p.fg_id not in assigned]
        for player in eligible[:slots_needed]:
            position_assignments[pos].append(player)
            assigned.add(player.fg_id)

    # Phase 2: UTIL — all unassigned hitters compete (positional overflow + DH-only)
    util_needed = util_slots * n
    util_pool = [p for p in ranked_hitters if p.fg_id not in assigned]
    for player in util_pool[:util_needed]:
        position_assignments["UTIL"].append(player)
        assigned.add(player.fg_id)

    all_rostered = [p for p in ranked_hitters if p.fg_id in assigned]
    return all_rostered, position_assignments


def assign_pitcher_positions(
    ranked_pitchers: list[ConsensusProjection],
    config: LeagueConfig,
) -> tuple[list[ConsensusProjection], dict[str, list[ConsensusProjection]]]:
    """
    Two-phase position assignment for pitchers.

    Phase 1: Fill dedicated SP then RP slots.
    Phase 2: Fill P flex slots with remaining pitchers (SP or RP eligible).
    """
    n = config.num_teams
    sp_needed = config.roster.sp_slots * n
    rp_needed = config.roster.rp_slots * n
    p_flex_needed = config.roster.p_flex_slots * n

    assigned: set[str] = set()
    assignments: dict[str, list[ConsensusProjection]] = {"SP": [], "RP": [], "P": []}

    # Fill SP (starters first — generally scarcer than RPs)
    sp_eligible = [p for p in ranked_pitchers if "SP" in p.positions]
    for player in sp_eligible[:sp_needed]:
        assignments["SP"].append(player)
        assigned.add(player.fg_id)

    # Fill RP
    rp_eligible = [p for p in ranked_pitchers if "RP" in p.positions and p.fg_id not in assigned]
    for player in rp_eligible[:rp_needed]:
        assignments["RP"].append(player)
        assigned.add(player.fg_id)

    # Fill P flex
    p_flex_pool = [p for p in ranked_pitchers if p.fg_id not in assigned]
    for player in p_flex_pool[:p_flex_needed]:
        assignments["P"].append(player)
        assigned.add(player.fg_id)

    all_rostered = [p for p in ranked_pitchers if p.fg_id in assigned]
    return all_rostered, assignments
