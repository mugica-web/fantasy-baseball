"""
Default configurations.

CNMFBL_CONFIG     : the reference league implementation; used as the UI starting point.
FALLBACK_SGP_DENOMINATORS : generic 12-team defaults used when no historical
                            standings CSV has been uploaded. Clearly labeled as
                            such in the UI — users should supply real standings
                            data for accurate values.
"""

from .league_config import LeagueConfig, RosterSlots, ScoringCategories

CNMFBL_CONFIG = LeagueConfig(
    name="CNMFBL",
    num_teams=12,
    budget=250,
    roster=RosterSlots(
        slots={
            "C": 1,
            "1B": 1,
            "2B": 1,
            "3B": 1,
            "SS": 1,
            "OF": 3,
            "UTIL": 2,
            "SP": 4,
            "RP": 1,
            "P": 2,
            "BN": 7,  # includes 3 IL slots
        }
    ),
    categories=ScoringCategories(
        hitting=["R", "HR", "RBI", "SB", "OBP"],
        pitching=["K", "W", "SV", "ERA", "WHIP"],
        rate_stats=["OBP", "ERA", "WHIP"],
        lower_is_better=["ERA", "WHIP"],
    ),
    hitter_split=0.67,
)

# Generic fallback SGP denominators for a 12-team rotisserie league.
# Source: reasonable published defaults; NOT calibrated to any real league.
# The UI will display a prominent warning when these are in use.
#
# For rate stats, the denominator is the average gap in the *team's aggregate*
# rate stat between adjacent standings positions (not individual player values).
#   OBP  : ~0.003 points of team OBP separates adjacent spots
#   ERA  : ~0.07 points of team ERA separates adjacent spots
#   WHIP : ~0.025 points of team WHIP separates adjacent spots
FALLBACK_SGP_DENOMINATORS: dict[str, float] = {
    # Hitting counting stats
    "R": 20.0,
    "HR": 14.0,
    "RBI": 18.0,
    "SB": 9.0,
    # Hitting rate stats
    "OBP": 0.003,
    "AVG": 0.0025,  # included for leagues using AVG instead of OBP
    # Pitching counting stats
    "K": 27.0,
    "W": 2.5,
    "SV": 9.0,
    # Pitching rate stats
    "ERA": 0.07,
    "WHIP": 0.025,
}
