"""
Fetch projection data from FanGraphs (via their JSON API) and PECOTA CSV uploads.

FanGraphs projection systems supported:
  steamer      : Steamer
  zips         : ZiPS
  atc          : ATC (Average of Available Projections)
  depthcharts  : Depth Charts (playing-time weighted)

We hit the FanGraphs projections JSON API directly rather than through pybaseball
because pybaseball does not expose a clean wrapper for all four projection systems.
The endpoint is stable and used by many fantasy tools.

Rate limiting: 1.5s between requests to be a polite API consumer.
"""

import time
import logging

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# FanGraphs projection API — returns a JSON array of player dicts
_FG_PROJ_URL = (
    "https://www.fangraphs.com/api/projections"
    "?type={system}&stats={stat_type}&pos=all&team=0&lg=all&players=0"
)

PROJECTION_SYSTEMS = ["steamer", "zips", "atc", "depthcharts"]

_REQUEST_DELAY = 1.5  # seconds between requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; fantasy-baseball-tool/1.0; "
        "+https://github.com/your-repo)"
    ),
    "Accept": "application/json",
}


def _fetch_fg(system: str, stat_type: str) -> pd.DataFrame:
    """
    Fetch a single FanGraphs projection system for batters or pitchers.

    stat_type : 'bat' | 'pit'
    Returns raw DataFrame with FanGraphs column names; adds a 'source' column.
    Raises requests.HTTPError on non-200 responses.
    """
    url = _FG_PROJ_URL.format(system=system, stat_type=stat_type)
    logger.info("Fetching %s %s projections from FanGraphs...", system, stat_type)
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()
    df = pd.DataFrame(data)
    df["source"] = system
    time.sleep(_REQUEST_DELAY)
    return df


def fetch_fg_batting_projections(system: str) -> pd.DataFrame:
    return _fetch_fg(system, "bat")


def fetch_fg_pitching_projections(system: str) -> pd.DataFrame:
    return _fetch_fg(system, "pit")


def fetch_all_fg_projections(
    systems: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Fetch batting and pitching projections for all requested systems.

    Returns a dict with keys like 'batting_steamer', 'pitching_zips', etc.
    Systems that fail (network error, rate limit, etc.) are skipped with a
    warning rather than crashing — the reconciler handles missing sources.
    """
    if systems is None:
        systems = PROJECTION_SYSTEMS

    results: dict[str, pd.DataFrame] = {}

    for system in systems:
        for stat_type, label in [("bat", "batting"), ("pit", "pitching")]:
            key = f"{label}_{system}"
            try:
                results[key] = _fetch_fg(system, stat_type)
                logger.info("  ✓ %s (%d players)", key, len(results[key]))
            except Exception as exc:
                logger.warning("  ✗ Failed to fetch %s: %s", key, exc)

    return results


# ---------------------------------------------------------------------------
# PECOTA (Baseball Prospectus) — manual CSV upload
# ---------------------------------------------------------------------------

def load_pecota_batting_csv(path: str) -> pd.DataFrame:
    """
    Load a PECOTA batting projections CSV.

    Expected columns (BP may change names year-to-year — normalizer handles mapping):
      BATTER or Name, TEAM or Team, PA, R, HR, RBI, SB, AVG, OBP, SLG, BB, H
    Adds source='pecota'.
    """
    df = pd.read_csv(path)
    df["source"] = "pecota"
    return df


def load_pecota_pitching_csv(path: str) -> pd.DataFrame:
    """
    Load a PECOTA pitching projections CSV.

    Expected columns: PITCHER or Name, TEAM or Team, IP, W, SV, ERA, WHIP, K, BB, H, ER
    Adds source='pecota'.
    """
    df = pd.read_csv(path)
    df["source"] = "pecota"
    return df
