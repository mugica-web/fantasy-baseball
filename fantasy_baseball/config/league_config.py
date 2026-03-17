from __future__ import annotations
from dataclasses import dataclass
from typing import ClassVar


@dataclass
class RosterSlots:
    """
    Roster construction for one team. Drives replacement-level depth calculations.

    Position keys:
      Dedicated hitters : C, 1B, 2B, 3B, SS, OF
      Flex hitter       : UTIL  (any hitter, including DH-only players)
      Dedicated SP/RP   : SP, RP
      Flex pitcher      : P     (SP or RP eligible)
      Bench             : BN    (all bench slots including IL)

    il_slots : how many of the BN slots are IL spots. IL players are parked
        there injured and produce no stats, so they should NOT anchor
        replacement level. Only (BN - IL) slots count for valuation depth.
    """

    slots: dict[str, int]
    il_slots: int = 0  # IL slots within BN — excluded from replacement level

    # Class-level position sets — used by replacement_level module
    HITTER_POSITIONS: ClassVar[frozenset[str]] = frozenset(
        {"C", "1B", "2B", "3B", "SS", "OF", "UTIL"}
    )
    PITCHER_POSITIONS: ClassVar[frozenset[str]] = frozenset({"SP", "RP", "P"})
    DEDICATED_HITTER_POSITIONS: ClassVar[frozenset[str]] = frozenset(
        {"C", "1B", "2B", "3B", "SS", "OF"}
    )

    @property
    def active_hitter_slots(self) -> int:
        """Total active hitter slots (dedicated + UTIL); excludes bench."""
        return sum(v for k, v in self.slots.items() if k in self.HITTER_POSITIONS)

    @property
    def active_pitcher_slots(self) -> int:
        """Total active pitcher slots (SP + RP + P flex); excludes bench."""
        return sum(v for k, v in self.slots.items() if k in self.PITCHER_POSITIONS)

    @property
    def bench_slots(self) -> int:
        return self.slots.get("BN", 0)

    @property
    def bench_hitter_slots(self) -> int:
        """Bench slots allocated to hitters (proportional to active hitter/pitcher ratio)."""
        total_active = self.active_hitter_slots + self.active_pitcher_slots
        if total_active == 0:
            return self.bench_slots // 2
        return round(self.bench_slots * self.active_hitter_slots / total_active)

    @property
    def bench_pitcher_slots(self) -> int:
        """Bench slots allocated to pitchers."""
        return self.bench_slots - self.bench_hitter_slots

    @property
    def total_hitter_slots(self) -> int:
        """Active hitter slots + bench hitter slots (full roster including IL)."""
        return self.active_hitter_slots + self.bench_hitter_slots

    @property
    def total_pitcher_slots(self) -> int:
        """Active pitcher slots + bench pitcher slots (full roster including IL)."""
        return self.active_pitcher_slots + self.bench_pitcher_slots

    # -- Effective slots (IL excluded) — used for replacement level and dollar valuation --

    @property
    def effective_bench_slots(self) -> int:
        """Real playing bench slots, excluding IL spots."""
        return max(0, self.bench_slots - self.il_slots)

    @property
    def effective_bench_hitter_slots(self) -> int:
        """Effective bench slots allocated to hitters (same active ratio, IL excluded)."""
        total_active = self.active_hitter_slots + self.active_pitcher_slots
        if total_active == 0:
            return self.effective_bench_slots // 2
        return round(self.effective_bench_slots * self.active_hitter_slots / total_active)

    @property
    def effective_bench_pitcher_slots(self) -> int:
        """Effective bench slots allocated to pitchers (IL excluded)."""
        return self.effective_bench_slots - self.effective_bench_hitter_slots

    @property
    def effective_total_hitter_slots(self) -> int:
        """Active hitter slots + effective bench hitter slots (IL excluded)."""
        return self.active_hitter_slots + self.effective_bench_hitter_slots

    @property
    def effective_total_pitcher_slots(self) -> int:
        """Active pitcher slots + effective bench pitcher slots (IL excluded)."""
        return self.active_pitcher_slots + self.effective_bench_pitcher_slots

    @property
    def dedicated_hitter_slots(self) -> dict[str, int]:
        """Positional hitter slots only (not UTIL)."""
        return {k: v for k, v in self.slots.items() if k in self.DEDICATED_HITTER_POSITIONS}

    @property
    def util_slots(self) -> int:
        return self.slots.get("UTIL", 0)

    @property
    def sp_slots(self) -> int:
        return self.slots.get("SP", 0)

    @property
    def rp_slots(self) -> int:
        return self.slots.get("RP", 0)

    @property
    def p_flex_slots(self) -> int:
        return self.slots.get("P", 0)


@dataclass
class ScoringCategories:
    """
    Scoring categories for a rotisserie league.

    rate_stats     : categories that require PA/IP-weighted marginal team modelling
                     rather than simple (player - replacement) / denominator
    lower_is_better: rate stats where a lower value is better (ERA, WHIP)
                     — sign is flipped when computing SGP contribution
    """

    hitting: list[str]
    pitching: list[str]
    rate_stats: list[str]
    lower_is_better: list[str]

    @property
    def all_categories(self) -> list[str]:
        return self.hitting + self.pitching

    @property
    def counting_stats(self) -> list[str]:
        return [c for c in self.all_categories if c not in self.rate_stats]

    @property
    def hitting_rate_stats(self) -> list[str]:
        return [c for c in self.rate_stats if c in self.hitting]

    @property
    def pitching_rate_stats(self) -> list[str]:
        return [c for c in self.rate_stats if c in self.pitching]


@dataclass
class LeagueConfig:
    """
    Single source of truth for all league-specific parameters.

    No calculation module should hardcode any value that belongs here.
    The CNMFBL default is defined in defaults.py; other leagues are created
    by editing this config through the UI or JSON persistence layer.
    """

    name: str
    num_teams: int
    budget: int          # per-team auction budget in dollars
    roster: RosterSlots
    categories: ScoringCategories
    hitter_split: float  # fraction of auction dollars allocated to hitters, e.g. 0.67

    @property
    def total_dollars(self) -> int:
        return self.num_teams * self.budget

    @property
    def hitter_pool_dollars(self) -> float:
        return self.total_dollars * self.hitter_split

    @property
    def pitcher_pool_dollars(self) -> float:
        return self.total_dollars * (1.0 - self.hitter_split)

    @property
    def total_hitter_slots(self) -> int:
        """Total hitter roster slots across all teams (active + full bench including IL)."""
        return self.num_teams * self.roster.total_hitter_slots

    @property
    def total_pitcher_slots(self) -> int:
        """Total pitcher roster slots across all teams (active + full bench including IL)."""
        return self.num_teams * self.roster.total_pitcher_slots

    @property
    def effective_total_hitter_slots(self) -> int:
        """Total hitter slots for valuation — IL excluded from bench count."""
        return self.num_teams * self.roster.effective_total_hitter_slots

    @property
    def effective_total_pitcher_slots(self) -> int:
        """Total pitcher slots for valuation — IL excluded from bench count."""
        return self.num_teams * self.roster.effective_total_pitcher_slots

    @property
    def active_hitter_slots(self) -> int:
        """Active hitter slots only (no bench) across all teams — used for dollar distribution."""
        return self.num_teams * self.roster.active_hitter_slots

    @property
    def active_pitcher_slots(self) -> int:
        """Active pitcher slots only (no bench) across all teams — used for dollar distribution."""
        return self.num_teams * self.roster.active_pitcher_slots
