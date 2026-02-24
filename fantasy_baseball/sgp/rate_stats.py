"""
SGP contribution for rate stats (OBP, AVG, ERA, WHIP).

The core insight: a player's rate stat projection does NOT map linearly to
standings points because rate stats are team-level weighted averages.

Model: "marginal team impact"
─────────────────────────────
We model adding a player to a replacement-level team and compute the resulting
change in that team's aggregate rate stat. That delta is then divided by the
SGP denominator (average gap between adjacent teams in the standings).

For OBP (higher is better):
──────────────────────────
  A team's OBP = (sum of H + BB + HBP across all hitters) / (sum of PA)
              = team_obp_numerator / team_pa

  Adding player P (displacing a replacement hitter):
    marginal_obp = (P.OBP - team_obp) × (P.PA / team_pa)

  Intuition: a .400 OBP player with 100 PA moves the team needle far less
  than a .400 OBP player with 600 PA. A player at exactly team_obp contributes
  zero marginal value regardless of playing time.

  sgp_obp = marginal_obp / denominator_OBP

For AVG (higher is better, same logic with AB instead of PA):
──────────────────────────────────────────────────────────────
  marginal_avg = (P.AVG - team_avg) × (P.AB / team_ab)
  sgp_avg = marginal_avg / denominator_AVG

For ERA (lower is better):
──────────────────────────
  A team's ERA = (sum of ER across all pitchers) / (sum of IP) × 9
              = (team_era_er / team_ip) × 9

  Adding player P (displacing a replacement pitcher):
    marginal_era_delta = -(P.ERA - team_era) × (P.IP / team_ip)
    (negative: moving ERA below team ERA improves standing)

  sgp_era = marginal_era_delta / denominator_ERA
  Note: denominator_ERA is a positive number (average absolute gap).

For WHIP (lower is better):
───────────────────────────
  A team's WHIP = (sum of BB + H across all pitchers) / (sum of IP)
               = team_whip_bb_h / team_ip

  marginal_whip_delta = -(P.WHIP - team_whip) × (P.IP / team_ip)
  sgp_whip = marginal_whip_delta / denominator_WHIP

OBP numerator reconstruction:
  We need H, BB, HBP, and PA separately (not just OBP) to apply this model
  correctly. The normalizer ensures all four are always present. OBP is
  reconstructed as (H + BB + HBP) / PA rather than trusting the raw OBP
  field to avoid small rounding inconsistencies between sources.
"""

from __future__ import annotations
import logging

from ..config.league_config import LeagueConfig
from ..data.schema import ConsensusProjection
from .denominators import SGPDenominators
from .replacement_level import ReplacementLevel

logger = logging.getLogger(__name__)


def rate_stat_sgp(
    player: ConsensusProjection,
    replacement: ReplacementLevel,
    denominators: SGPDenominators,
    config: LeagueConfig,
) -> dict[str, float]:
    """
    Compute per-category SGP contribution for all rate stats.

    Returns a dict of {category: sgp_contribution}.
    Categories not applicable to the player type return 0.0.
    """
    contributions: dict[str, float] = {}

    if player.player_type == "hitter":
        for cat in config.categories.hitting_rate_stats:
            contributions[cat] = _hitter_rate_sgp(player, cat, replacement, denominators)
    else:
        for cat in config.categories.pitching_rate_stats:
            contributions[cat] = _pitcher_rate_sgp(player, cat, replacement, denominators)

    return contributions


# ---------------------------------------------------------------------------
# Hitter rate stats
# ---------------------------------------------------------------------------

def _hitter_rate_sgp(
    player: ConsensusProjection,
    category: str,
    replacement: ReplacementLevel,
    denominators: SGPDenominators,
) -> float:
    denom = denominators.get(category)
    if denom == 0:
        return 0.0

    if category == "OBP":
        return _obp_sgp(player, replacement, denom)
    elif category == "AVG":
        return _avg_sgp(player, replacement, denom)
    else:
        logger.warning("Unknown hitter rate stat '%s' — returning 0", category)
        return 0.0


def _obp_sgp(
    player: ConsensusProjection,
    replacement: ReplacementLevel,
    denom: float,
) -> float:
    pa = player.get_stat("PA")
    if pa == 0 or replacement.team_pa == 0:
        return 0.0

    # Reconstruct OBP from components for accuracy
    h = player.get_stat("H")
    bb = player.get_stat("BB")
    hbp = player.get_stat("HBP")
    player_obp = (h + bb + hbp) / pa if pa > 0 else player.get_stat("OBP")

    marginal_obp = (player_obp - replacement.team_obp) * (pa / replacement.team_pa)
    return marginal_obp / denom


def _avg_sgp(
    player: ConsensusProjection,
    replacement: ReplacementLevel,
    denom: float,
) -> float:
    ab = player.get_stat("AB")
    if ab == 0 or replacement.team_ab == 0:
        return 0.0

    h = player.get_stat("H")
    player_avg = h / ab if ab > 0 else player.get_stat("AVG")

    marginal_avg = (player_avg - replacement.team_avg) * (ab / replacement.team_ab)
    return marginal_avg / denom


# ---------------------------------------------------------------------------
# Pitcher rate stats
# ---------------------------------------------------------------------------

def _pitcher_rate_sgp(
    player: ConsensusProjection,
    category: str,
    replacement: ReplacementLevel,
    denominators: SGPDenominators,
) -> float:
    denom = denominators.get(category)
    if denom == 0:
        return 0.0

    if category == "ERA":
        return _era_sgp(player, replacement, denom)
    elif category == "WHIP":
        return _whip_sgp(player, replacement, denom)
    else:
        logger.warning("Unknown pitcher rate stat '%s' — returning 0", category)
        return 0.0


def _era_sgp(
    player: ConsensusProjection,
    replacement: ReplacementLevel,
    denom: float,
) -> float:
    ip = player.get_stat("IP")
    if ip == 0 or replacement.team_ip == 0:
        return 0.0

    # Reconstruct ERA from ER and IP for accuracy
    er = player.get_stat("ER")
    player_era = (er / ip * 9) if ip > 0 else player.get_stat("ERA")

    # Negative: lower ERA is better → moving below team ERA improves standings
    marginal_era = -(player_era - replacement.team_era) * (ip / replacement.team_ip)
    return marginal_era / denom


def _whip_sgp(
    player: ConsensusProjection,
    replacement: ReplacementLevel,
    denom: float,
) -> float:
    ip = player.get_stat("IP")
    if ip == 0 or replacement.team_ip == 0:
        return 0.0

    # Reconstruct WHIP from components
    bb = player.get_stat("BB")
    h = player.get_stat("H")
    player_whip = (bb + h) / ip if ip > 0 else player.get_stat("WHIP")

    # Negative: lower WHIP is better
    marginal_whip = -(player_whip - replacement.team_whip) * (ip / replacement.team_ip)
    return marginal_whip / denom
