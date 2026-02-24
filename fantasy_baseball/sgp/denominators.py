"""
SGP denominator calculation.

Path 1 (preferred): compute from historical final standings CSV.
  - Average gap between adjacent-ranked teams across all seasons and all gaps.
  - Recent seasons are weighted more heavily (linear recency weighting).
  - Calculated denominators are returned with sample-size metadata so the
    UI can display confidence and prompt the user to override thin values.

Path 2 (fallback): use published generic defaults.
  - Clearly labeled in the UI as estimates; users are prompted to upload data.

User overrides: applied on top of either path. The UI lets users inspect
  computed values and override individual categories before running the pipeline.

Rate stat denominators:
  Computed exactly the same way as counting stat denominators — the average
  absolute gap in team aggregate rate stat between adjacent standings positions.
  The special treatment for rate stats lives in rate_stats.py (the marginal
  team model), not here. The denominator is just the standings gap.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from ..config.league_config import LeagueConfig
from ..config.defaults import FALLBACK_SGP_DENOMINATORS

logger = logging.getLogger(__name__)


@dataclass
class SGPDenominators:
    values: dict[str, float]           # final effective values (after overrides)
    source: Literal["historical", "defaults"]
    seasons_used: list[int]            # empty if defaults
    sample_sizes: dict[str, int]       # number of gaps observed per category
    user_overrides: dict[str, float]   # categories the user manually changed
    raw_computed: dict[str, float]     # computed values before overrides (for display)

    def get(self, category: str) -> float:
        return self.values[category]


def compute_sgp_denominators(
    config: LeagueConfig,
    standings_df: pd.DataFrame | None = None,
    user_overrides: dict[str, float] | None = None,
) -> SGPDenominators:
    """
    Compute SGP denominators for all scoring categories in config.

    standings_df : optional DataFrame with one row per team per season.
        Required columns: one column per scoring category + a 'season' column.
        Rate stat columns (OBP, ERA, WHIP) should be the team's full-season
        aggregate value (not individual player values).

    user_overrides : dict of {category: value} to override computed/default values.
    """
    overrides = user_overrides or {}

    if standings_df is not None:
        raw_values, sample_sizes, seasons_used = _compute_from_standings(
            standings_df, config
        )
        source: Literal["historical", "defaults"] = "historical"
    else:
        raw_values = {
            cat: FALLBACK_SGP_DENOMINATORS.get(cat, 1.0)
            for cat in config.categories.all_categories
        }
        sample_sizes = {cat: 0 for cat in config.categories.all_categories}
        seasons_used = []
        source = "defaults"
        logger.warning(
            "No standings data provided — using generic default SGP denominators. "
            "Upload historical standings for more accurate values."
        )

    # Apply user overrides
    final_values = {**raw_values, **overrides}

    # Validate: all categories must have a denominator
    missing = [c for c in config.categories.all_categories if c not in final_values]
    if missing:
        raise ValueError(
            f"No SGP denominator found for categories: {missing}. "
            "Add them to user_overrides or ensure they appear in the standings CSV."
        )

    return SGPDenominators(
        values=final_values,
        source=source,
        seasons_used=seasons_used,
        sample_sizes=sample_sizes,
        user_overrides=overrides,
        raw_computed=raw_values,
    )


def _compute_from_standings(
    df: pd.DataFrame,
    config: LeagueConfig,
) -> tuple[dict[str, float], dict[str, int], list[int]]:
    """
    Compute average gap between adjacent standings positions from historical data.

    For each category:
      1. For each season, rank teams, compute gaps between adjacent ranks.
      2. Weight each season's gaps by recency (linear: most recent = highest weight).
      3. Average all weighted gaps.

    Returns (denominators, sample_sizes, seasons_used).
    """
    if "season" not in df.columns:
        raise ValueError("Standings CSV must include a 'season' column.")

    seasons = sorted(df["season"].unique())
    weights = _recency_weights(seasons)

    denominators: dict[str, float] = {}
    sample_sizes: dict[str, int] = {}

    for cat in config.categories.all_categories:
        if cat not in df.columns:
            logger.warning(
                "Category '%s' not found in standings CSV — using fallback default", cat
            )
            denominators[cat] = FALLBACK_SGP_DENOMINATORS.get(cat, 1.0)
            sample_sizes[cat] = 0
            continue

        lower_is_better = cat in config.categories.lower_is_better
        all_gaps: list[float] = []
        all_weights: list[float] = []

        for season in seasons:
            season_df = df[df["season"] == season].copy()
            values = season_df[cat].dropna()
            if len(values) < 2:
                continue

            sorted_vals = values.sort_values(ascending=lower_is_better).values
            gaps = np.abs(np.diff(sorted_vals))
            season_gaps = gaps.tolist()

            w = weights[season]
            all_gaps.extend(season_gaps)
            all_weights.extend([w] * len(season_gaps))

        if not all_gaps:
            logger.warning(
                "No valid gaps found for '%s' — using fallback default", cat
            )
            denominators[cat] = FALLBACK_SGP_DENOMINATORS.get(cat, 1.0)
            sample_sizes[cat] = 0
        else:
            denominators[cat] = float(np.average(all_gaps, weights=all_weights))
            sample_sizes[cat] = len(all_gaps)
            logger.info(
                "  %s: denominator=%.4f (n=%d gaps across %d seasons)",
                cat,
                denominators[cat],
                sample_sizes[cat],
                len(seasons),
            )

    return denominators, sample_sizes, [int(s) for s in seasons]


def _recency_weights(seasons: list[int]) -> dict[int, float]:
    """
    Assign linear recency weights to seasons.

    Most recent season gets weight 2.0; oldest gets weight 1.0.
    All weights sum to len(seasons), so averaging behavior is preserved
    when you switch between weighted and unweighted.
    """
    n = len(seasons)
    if n == 1:
        return {seasons[0]: 1.0}

    sorted_seasons = sorted(seasons)
    raw_weights = {s: 1.0 + (i / (n - 1)) for i, s in enumerate(sorted_seasons)}
    total = sum(raw_weights.values())
    # Normalize so weights sum to n
    return {s: w * n / total for s, w in raw_weights.items()}
