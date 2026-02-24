"""
Normalize raw projection DataFrames (from any source) to a common schema.

Each source uses slightly different column names and formats. After normalization
every row has the same stat columns, types, and a parsed positions list.

Common stat schema
------------------
All players:
  fg_id, name, team, positions (list), player_type, source, is_dh_only

Hitters:
  PA, AB, H, BB, HBP, R, HR, RBI, SB, AVG, OBP, SLG

Pitchers:
  IP, GS, G, W, L, SV, HLD, K, BB, H, ER, ERA, WHIP

Notes:
- FanGraphs uses 'SO' for strikeouts; we normalize to 'K'.
- OBP numerator (H + BB + HBP) is preserved so the rate stat engine can
  reconstruct it accurately from components rather than back-calculating
  from the rate stat itself.
- ERA is kept as-is (FanGraphs computes it); ER and IP are kept separately
  so the rate stat engine can work with raw components.
"""

from __future__ import annotations
import logging

import numpy as np
import pandas as pd

from .schema import RawProjection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column name aliases: maps source-specific names → standard names
# Applied before schema enforcement. Multiple aliases for the same standard
# name are tried in order; first match wins.
# ---------------------------------------------------------------------------

_BATTING_COL_ALIASES: dict[str, list[str]] = {
    "fg_id":  ["playerid", "PLAYERID", "fg_id", "mlbamid"],
    "name":   ["PlayerName", "Name", "BATTER", "Player"],
    "team":   ["Team", "TEAM", "Tm"],
    "pos":    ["minpos", "Pos", "POS", "Position", "POSITION"],
    "PA":     ["PA"],
    "AB":     ["AB"],
    "H":      ["H"],
    "BB":     ["BB"],
    "HBP":    ["HBP"],
    "R":      ["R"],
    "HR":     ["HR"],
    "RBI":    ["RBI"],
    "SB":     ["SB"],
    "AVG":    ["AVG"],
    "OBP":    ["OBP"],
    "SLG":    ["SLG"],
}

_PITCHING_COL_ALIASES: dict[str, list[str]] = {
    "fg_id":  ["playerid", "PLAYERID", "fg_id", "mlbamid"],
    "name":   ["PlayerName", "Name", "PITCHER", "Player"],
    "team":   ["Team", "TEAM", "Tm"],
    "pos":    ["minpos", "Pos", "POS", "Position", "POSITION"],
    "IP":     ["IP"],
    "GS":     ["GS"],
    "G":      ["G"],
    "W":      ["W"],
    "L":      ["L"],
    "SV":     ["SV"],
    "HLD":    ["HLD", "HD", "HO"],
    "K":      ["SO", "K", "SO9"],      # FanGraphs uses SO; some sources use K
    "BB":     ["BB"],
    "H":      ["H"],
    "ER":     ["ER"],
    "ERA":    ["ERA"],
    "WHIP":   ["WHIP"],
}

# Stats we want to keep; missing ones will be filled with NaN then 0
_BATTING_STATS = ["PA", "AB", "H", "BB", "HBP", "R", "HR", "RBI", "SB", "AVG", "OBP", "SLG"]
_PITCHING_STATS = ["IP", "GS", "G", "W", "L", "SV", "HLD", "K", "BB", "H", "ER", "ERA", "WHIP"]

# Traditional field positions; anything not in this set (or 'DH') is DH-only
_TRADITIONAL_POSITIONS = {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF"}
# OF sub-positions that should collapse to OF
_OF_ALIASES = {"LF", "CF", "RF"}


def _resolve_column(df: pd.DataFrame, standard_name: str, aliases: list[str]) -> str | None:
    """Return the first alias that exists as a column in df, or None."""
    for alias in aliases:
        if alias in df.columns:
            return alias
    return None


def _apply_aliases(df: pd.DataFrame, alias_map: dict[str, list[str]]) -> pd.DataFrame:
    """
    Rename source columns to standard names using alias_map.
    Adds NaN columns for any standard names not found in df.
    """
    df = df.copy()
    rename = {}
    for standard, aliases in alias_map.items():
        src = _resolve_column(df, standard, aliases)
        if src and src != standard:
            rename[src] = standard
        elif not src:
            df[standard] = np.nan
    df = df.rename(columns=rename)
    return df


def _parse_positions(pos_str) -> tuple[list[str], bool]:
    """
    Parse a FanGraphs position string into (positions, is_dh_only).

    'SS'        → (['SS'], False)
    '2B/SS'     → (['2B', 'SS'], False)
    'DH'        → ([], True)
    '1B/DH'     → (['1B'], False)
    'SP'        → (['SP'], False)
    ''  / NaN   → ([], True)   — no eligible position; treated as DH-only
    """
    if not pos_str or (isinstance(pos_str, float) and np.isnan(pos_str)):
        return [], True

    raw_parts = [p.strip() for p in str(pos_str).split("/")]
    # Collapse OF sub-positions to OF
    parts = ["OF" if p in _OF_ALIASES else p for p in raw_parts]
    # Remove DH from the list; track whether anything traditional remains
    traditional = [p for p in parts if p in _TRADITIONAL_POSITIONS]
    is_dh_only = len(traditional) == 0

    return list(dict.fromkeys(traditional)), is_dh_only  # deduplicated, order-preserving


def _coerce_stats(df: pd.DataFrame, stat_cols: list[str]) -> pd.DataFrame:
    """Convert stat columns to float; fill NaN with 0."""
    for col in stat_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0
    return df


def normalize_batting_df(df: pd.DataFrame) -> list[RawProjection]:
    """
    Normalize a raw batting projection DataFrame (any source) to RawProjection objects.

    The 'source' column must already be set on df (done by the fetcher).
    """
    source = df["source"].iloc[0] if "source" in df.columns else "unknown"
    df = _apply_aliases(df, _BATTING_COL_ALIASES)
    df = _coerce_stats(df, _BATTING_STATS)

    projections: list[RawProjection] = []
    for _, row in df.iterrows():
        fg_id = str(row.get("fg_id", "")).strip()
        if not fg_id or fg_id in ("nan", "0", ""):
            continue

        pos_raw = row.get("pos", "")
        positions, is_dh_only = _parse_positions(pos_raw)

        stats = {col: float(row[col]) for col in _BATTING_STATS if col in row}

        projections.append(
            RawProjection(
                fg_id=fg_id,
                name=str(row.get("name", "")).strip(),
                team=str(row.get("team", "")).strip(),
                positions=positions,
                player_type="hitter",
                stats=stats,
                source=source,
                is_dh_only=is_dh_only,
            )
        )

    logger.debug("Normalized %d batting rows from source '%s'", len(projections), source)
    return projections


def normalize_pitching_df(df: pd.DataFrame) -> list[RawProjection]:
    """
    Normalize a raw pitching projection DataFrame to RawProjection objects.

    Pitcher positions are inferred from GS (starts) rather than trusting the
    Pos field, since two-way player handling needs special care:
      - GS > 0  → eligible for SP (and P flex)
      - GS == 0 → eligible for RP (and P flex)
    The explicit Pos field is used if present and makes sense; GS is the fallback.
    """
    source = df["source"].iloc[0] if "source" in df.columns else "unknown"
    df = _apply_aliases(df, _PITCHING_COL_ALIASES)
    df = _coerce_stats(df, _PITCHING_STATS)

    projections: list[RawProjection] = []
    for _, row in df.iterrows():
        fg_id = str(row.get("fg_id", "")).strip()
        if not fg_id or fg_id in ("nan", "0", ""):
            continue

        # Determine pitcher position eligibility
        pos_raw = row.get("pos", "")
        gs = float(row.get("GS", 0))
        positions = _infer_pitcher_positions(pos_raw, gs)

        stats = {col: float(row[col]) for col in _PITCHING_STATS if col in row}

        projections.append(
            RawProjection(
                fg_id=fg_id,
                name=str(row.get("name", "")).strip(),
                team=str(row.get("team", "")).strip(),
                positions=positions,
                player_type="pitcher",
                stats=stats,
                source=source,
                is_dh_only=False,
            )
        )

    logger.debug("Normalized %d pitching rows from source '%s'", len(projections), source)
    return projections


def _infer_pitcher_positions(pos_raw, gs: float) -> list[str]:
    """
    Infer SP/RP eligibility from position string and projected GS.

    A pitcher can be dual-eligible (SP + RP) if their Pos field says so,
    but for most pitchers one role dominates.
    """
    pos_str = str(pos_raw).strip().upper() if pos_raw else ""

    # If the source provides explicit pitcher position, use it
    explicit = [p for p in pos_str.split("/") if p in {"SP", "RP", "P"}]
    if explicit:
        # Normalize 'P' (generic pitcher) to both SP and RP for eligibility
        result = []
        for p in explicit:
            if p == "P":
                result += ["SP", "RP"]
            else:
                result.append(p)
        return list(dict.fromkeys(result))

    # Fallback: infer from projected starts
    if gs >= 5:
        return ["SP"]
    return ["RP"]
