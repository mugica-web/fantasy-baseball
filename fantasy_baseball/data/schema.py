"""
Core projection data models.

RawProjection      : one source's projection for one player, after normalization
                     to the common stat schema.
ConsensusProjection: mean across all available sources for one player.
                     This is what flows into the SGP calculation pipeline.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RawProjection:
    fg_id: str                           # FanGraphs player ID — canonical cross-source key
    name: str
    team: str
    positions: list[str]                 # e.g. ['SS', '2B']; empty list for DH-only
    player_type: Literal["hitter", "pitcher"]
    stats: dict[str, float]              # normalized stat dict (common column names)
    source: str                          # 'steamer'|'zips'|'atc'|'depthcharts'|'pecota'
    is_dh_only: bool = False             # True when positions is empty / only 'DH'


@dataclass
class ConsensusProjection:
    fg_id: str
    name: str
    team: str
    positions: list[str]
    player_type: Literal["hitter", "pitcher"]
    stats: dict[str, float]              # simple mean across available sources
    sources_available: list[str]
    sources_missing: list[str]
    is_dh_only: bool = False

    def get_stat(self, key: str, default: float = 0.0) -> float:
        return self.stats.get(key, default)
