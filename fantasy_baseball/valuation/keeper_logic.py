"""
Keeper handling — three input modes:

  KeeperMode.NONE
    No keepers entered. Full player pool, full dollar budget.
    Used for preliminary values or leagues without keepers.

  KeeperMode.MANUAL
    User enters keeper name + salary pairs directly in the UI.
    Players are matched to consensus projections by FG ID or name fuzzy match.

  KeeperMode.PRIOR_YEAR_ROSTERS
    User uploads a CSV of prior-year rosters. Tool flags players where
    projected auction value > keeper salary as "suggested keepers."
    User reviews and confirms which players are actually being kept.

Pipeline adjustments when keepers are present:
  1. Remove confirmed keepers from the available pool (they won't be at auction).
  2. Subtract keeper salaries from the relevant dollar pool (those dollars are
     no longer available to bid on auction players).
  3. Recompute replacement level from the reduced available pool.
  4. Recompute SGP and dollar values for remaining players.
  5. Kept players remain visible in output with is_available=False and
     their keeper salary shown for reference.

Prior-year roster CSV expected columns:
  name, team, salary, eligible_to_keep
  Optionally: fg_id (if present, used for exact matching; otherwise fuzzy match)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd
from rapidfuzz import process, fuzz

if TYPE_CHECKING:
    from .dollar_value import PlayerValue
    from ..data.schema import ConsensusProjection

logger = logging.getLogger(__name__)


class KeeperMode(Enum):
    NONE = "none"
    MANUAL = "manual"
    PRIOR_YEAR_ROSTERS = "prior_year_rosters"


@dataclass
class KeeperStatus:
    salary: float
    projected_value: float
    surplus: float               # projected_value - salary  (positive = worth keeping)
    is_confirmed_keeper: bool    # user explicitly confirmed as kept
    is_suggested_keeper: bool    # tool suggests keeping (surplus > 0)
    is_available: bool           # False if confirmed kept by any team


@dataclass
class KeeperEntry:
    """A single keeper declaration from the user."""
    fg_id: str | None     # may be None if only name is available (matched later)
    name: str
    team: str
    salary: float
    owner: str = ""       # which team is keeping (optional; for reference)


def parse_manual_keepers(
    keeper_data: list[dict],
    projections: list[ConsensusProjection],
) -> list[KeeperEntry]:
    """
    Parse manual keeper entries (from UI form or dict list).

    Each dict should have: name (str), salary (float), optionally fg_id and team.
    Players are matched to projections to resolve FG IDs.
    """
    proj_by_id = {p.fg_id: p for p in projections}
    # Build fuzzy lookup
    proj_keys = [f"{p.name.lower()}|{p.team.lower()}" for p in projections]
    proj_by_key = {f"{p.name.lower()}|{p.team.lower()}": p for p in projections}

    entries: list[KeeperEntry] = []
    for item in keeper_data:
        fg_id = item.get("fg_id", "").strip()
        name = item.get("name", "").strip()
        team = item.get("team", "").strip()
        salary = float(item.get("salary", 0))

        # Try exact FG ID match first
        if fg_id and fg_id in proj_by_id:
            entries.append(KeeperEntry(fg_id=fg_id, name=name, team=team, salary=salary))
            continue

        # Fuzzy name + team match
        query = f"{name.lower()}|{team.lower()}"
        result = process.extractOne(query, proj_keys, scorer=fuzz.token_sort_ratio, score_cutoff=80)
        if result:
            matched_key, score, _ = result
            matched_proj = proj_by_key[matched_key]
            logger.info("Keeper match: '%s' → '%s' (score=%d)", name, matched_proj.name, score)
            entries.append(
                KeeperEntry(fg_id=matched_proj.fg_id, name=matched_proj.name, team=matched_proj.team, salary=salary)
            )
        else:
            logger.warning("Could not match keeper '%s' (%s) to any projection — skipping", name, team)

    return entries


def parse_prior_year_roster_csv(
    csv_path: str,
    projections: list[ConsensusProjection],
    preliminary_values: list[PlayerValue],
) -> tuple[list[KeeperEntry], pd.DataFrame]:
    """
    Load prior-year rosters CSV and flag likely keepers.

    Returns:
      - list of KeeperEntry for all keeper-eligible players (user confirms which are kept)
      - DataFrame with columns: name, team, salary, eligible, projected_value, surplus, suggested
        for display in the UI keeper review table
    """
    df = pd.read_csv(csv_path)

    # Normalize column names
    col_map = {
        "Name": "name", "PLAYER": "name", "Player": "name",
        "Team": "team", "TEAM": "team",
        "Salary": "salary", "SALARY": "salary", "Cost": "salary",
        "EligibleToKeep": "eligible_to_keep", "eligible": "eligible_to_keep",
        "Owner": "owner", "OWNER": "owner",
        "fg_id": "fg_id", "FGID": "fg_id",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    required = {"name", "salary"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Prior-year roster CSV missing required columns: {missing}")

    if "eligible_to_keep" not in df.columns:
        df["eligible_to_keep"] = True
    if "owner" not in df.columns:
        df["owner"] = ""

    # Build value lookup
    value_by_id = {pv.fg_id: pv.dollar_value for pv in preliminary_values}

    # Match each player to a projection
    proj_keys = [f"{p.name.lower()}|{p.team.lower()}" for p in projections]
    proj_by_key = {f"{p.name.lower()}|{p.team.lower()}": p for p in projections}

    records = []
    entries: list[KeeperEntry] = []

    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        team = str(row.get("team", "")).strip() if "team" in row else ""
        salary = float(row.get("salary", 0))
        eligible = bool(row.get("eligible_to_keep", True))
        owner = str(row.get("owner", "")).strip()

        if not eligible:
            continue

        # Match to projection
        fg_id = str(row.get("fg_id", "")).strip() if "fg_id" in row else ""
        matched_proj = None

        if fg_id:
            for p in projections:
                if p.fg_id == fg_id:
                    matched_proj = p
                    break

        if matched_proj is None:
            query = f"{name.lower()}|{team.lower()}"
            result = process.extractOne(
                query, proj_keys, scorer=fuzz.token_sort_ratio, score_cutoff=75
            )
            if result:
                matched_key, _, _ = result
                matched_proj = proj_by_key[matched_key]

        if matched_proj is None:
            logger.warning("Prior-year roster: could not match '%s' — skipping", name)
            continue

        proj_value = value_by_id.get(matched_proj.fg_id, 1.0)
        surplus = proj_value - salary
        suggested = surplus > 0

        entries.append(
            KeeperEntry(
                fg_id=matched_proj.fg_id,
                name=matched_proj.name,
                team=matched_proj.team,
                salary=salary,
                owner=owner,
            )
        )
        records.append({
            "name": matched_proj.name,
            "team": matched_proj.team,
            "owner": owner,
            "salary": salary,
            "projected_value": round(proj_value, 2),
            "surplus": round(surplus, 2),
            "suggested_keep": suggested,
            "confirmed": False,      # user will check this in the UI
        })

    review_df = pd.DataFrame(records).sort_values("surplus", ascending=False)
    return entries, review_df


def apply_keeper_adjustments(
    config,
    projections: list[ConsensusProjection],
    player_values: list[PlayerValue],
    confirmed_keepers: list[KeeperEntry],
) -> tuple[list[ConsensusProjection], float, float]:
    """
    Remove confirmed keepers from the available pool and subtract their
    salaries from the relevant dollar pools.

    Returns:
      - available_projections: projections list with keepers removed
      - adjusted_hitter_pool: dollars remaining for hitter auction
      - adjusted_pitcher_pool: dollars remaining for pitcher auction

    KeeperStatus is set on the PlayerValue objects in-place.
    """
    keeper_ids = {k.fg_id for k in confirmed_keepers}
    keeper_salary_by_id = {k.fg_id: k.salary for k in confirmed_keepers}
    value_by_id = {pv.fg_id: pv.dollar_value for pv in player_values}
    type_by_id = {p.fg_id: p.player_type for p in projections}

    hitter_keeper_spend = 0.0
    pitcher_keeper_spend = 0.0

    for keeper in confirmed_keepers:
        player_type = type_by_id.get(keeper.fg_id, "hitter")
        if player_type == "hitter":
            hitter_keeper_spend += keeper.salary
        else:
            pitcher_keeper_spend += keeper.salary

    # Attach KeeperStatus to kept PlayerValue objects
    for pv in player_values:
        if pv.fg_id in keeper_ids:
            salary = keeper_salary_by_id[pv.fg_id]
            pv.keeper_status = KeeperStatus(
                salary=salary,
                projected_value=pv.dollar_value,
                surplus=round(pv.dollar_value - salary, 2),
                is_confirmed_keeper=True,
                is_suggested_keeper=pv.dollar_value > salary,
                is_available=False,
            )

    # Remove keepers from pool
    available = [p for p in projections if p.fg_id not in keeper_ids]

    adjusted_hitter_pool = config.hitter_pool_dollars - hitter_keeper_spend
    adjusted_pitcher_pool = config.pitcher_pool_dollars - pitcher_keeper_spend

    logger.info(
        "Keepers: %d players removed. Hitter pool: $%.0f → $%.0f. Pitcher pool: $%.0f → $%.0f",
        len(keeper_ids),
        config.hitter_pool_dollars, adjusted_hitter_pool,
        config.pitcher_pool_dollars, adjusted_pitcher_pool,
    )

    return available, adjusted_hitter_pool, adjusted_pitcher_pool
