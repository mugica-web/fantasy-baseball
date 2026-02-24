"""
Serialize and deserialize LeagueConfig to/from JSON.

Saved configs are stored in ~/.fantasy_baseball/configs/ by default so
they persist across Streamlit sessions without any server-side storage.
"""

import json
from pathlib import Path

from .league_config import LeagueConfig, RosterSlots, ScoringCategories

DEFAULT_CONFIG_DIR = Path.home() / ".fantasy_baseball" / "configs"


def config_to_dict(config: LeagueConfig) -> dict:
    return {
        "name": config.name,
        "num_teams": config.num_teams,
        "budget": config.budget,
        "roster": {"slots": config.roster.slots},
        "categories": {
            "hitting": config.categories.hitting,
            "pitching": config.categories.pitching,
            "rate_stats": config.categories.rate_stats,
            "lower_is_better": config.categories.lower_is_better,
        },
        "hitter_split": config.hitter_split,
    }


def config_from_dict(d: dict) -> LeagueConfig:
    return LeagueConfig(
        name=d["name"],
        num_teams=d["num_teams"],
        budget=d["budget"],
        roster=RosterSlots(slots=d["roster"]["slots"]),
        categories=ScoringCategories(
            hitting=d["categories"]["hitting"],
            pitching=d["categories"]["pitching"],
            rate_stats=d["categories"]["rate_stats"],
            lower_is_better=d["categories"]["lower_is_better"],
        ),
        hitter_split=d["hitter_split"],
    )


def save_config(config: LeagueConfig, path: Path | None = None) -> Path:
    """Save config to JSON. Defaults to ~/.fantasy_baseball/configs/{name}.json."""
    if path is None:
        path = DEFAULT_CONFIG_DIR / f"{config.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config_to_dict(config), f, indent=2)
    return path


def load_config(path: Path) -> LeagueConfig:
    with open(path) as f:
        return config_from_dict(json.load(f))


def list_saved_configs() -> list[Path]:
    """Return paths of all saved config files."""
    if not DEFAULT_CONFIG_DIR.exists():
        return []
    return sorted(DEFAULT_CONFIG_DIR.glob("*.json"))
