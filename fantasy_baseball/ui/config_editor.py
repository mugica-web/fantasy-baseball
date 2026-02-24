"""
Streamlit league config editor.

Renders an editable form for LeagueConfig. On save, persists the config to
~/.fantasy_baseball/configs/{name}.json so it survives across sessions.

The CNMFBL config is always available as the default starting point.
"""

from __future__ import annotations

import streamlit as st

from ..config.league_config import LeagueConfig, RosterSlots, ScoringCategories
from ..config.defaults import CNMFBL_CONFIG
from ..config.persistence import save_config, load_config, list_saved_configs, config_from_dict


# Standard rotisserie categories the UI offers as checkboxes
_HITTING_CATS = ["R", "HR", "RBI", "SB", "OBP", "AVG", "H", "TB", "XBH", "BB", "K"]
_PITCHING_CATS = ["K", "W", "SV", "ERA", "WHIP", "HLD", "QS", "K/BB", "IP"]
_RATE_STATS = {"OBP", "AVG", "ERA", "WHIP", "K/BB"}
_LOWER_IS_BETTER = {"ERA", "WHIP"}


def render_config_editor() -> LeagueConfig:
    """
    Render the league config editor in the Streamlit sidebar.

    Returns the current active LeagueConfig (either loaded or edited).
    Uses st.session_state["config"] as the canonical config across reruns.
    """
    st.sidebar.header("League Configuration")

    # ── Load saved config ──────────────────────────────────────────────────
    saved_paths = list_saved_configs()
    saved_names = [p.stem for p in saved_paths]
    config_options = ["CNMFBL (default)"] + saved_names

    selected = st.sidebar.selectbox(
        "Load saved config",
        options=config_options,
        index=0,
        key="config_selector",
    )

    if selected == "CNMFBL (default)":
        base_config = CNMFBL_CONFIG
    else:
        path = next(p for p in saved_paths if p.stem == selected)
        base_config = load_config(path)

    # Use session state so edits survive reruns without saving
    if "config" not in st.session_state or st.session_state.get("_loaded_config") != selected:
        st.session_state["config"] = base_config
        st.session_state["_loaded_config"] = selected

    cfg = st.session_state["config"]

    with st.sidebar.expander("Edit League Settings", expanded=False):
        cfg = _render_form(cfg)
        st.session_state["config"] = cfg

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Save Config"):
                path = save_config(cfg)
                st.success(f"Saved to {path}")
        with col2:
            if st.button("Reset to CNMFBL"):
                st.session_state["config"] = CNMFBL_CONFIG
                st.rerun()

    return st.session_state["config"]


def _render_form(cfg: LeagueConfig) -> LeagueConfig:
    """Render the editable config form and return the updated config."""

    name = st.text_input("League name", value=cfg.name)
    num_teams = st.number_input("Number of teams", min_value=4, max_value=30, value=cfg.num_teams, step=1)
    budget = st.number_input("Auction budget ($ per team)", min_value=100, max_value=1000, value=cfg.budget, step=10)
    hitter_split = st.slider(
        "Hitter/Pitcher dollar split",
        min_value=50, max_value=80, value=int(cfg.hitter_split * 100), step=1,
        format="%d%% hitters",
        help="Percentage of total auction dollars allocated to hitters. 67% is the standard.",
    )

    st.markdown("**Roster Construction**")
    slots = dict(cfg.roster.slots)
    positions_to_edit = ["C", "1B", "2B", "3B", "SS", "OF", "UTIL", "SP", "RP", "P", "BN"]
    for pos in positions_to_edit:
        slots[pos] = st.number_input(
            f"  {pos} slots",
            min_value=0, max_value=10,
            value=slots.get(pos, 0),
            step=1,
            key=f"slot_{pos}",
        )

    st.markdown("**Hitting Categories**")
    hitting = [
        cat for cat in _HITTING_CATS
        if st.checkbox(cat, value=cat in cfg.categories.hitting, key=f"hit_{cat}")
    ]
    if not hitting:
        st.warning("Select at least one hitting category.")

    st.markdown("**Pitching Categories**")
    pitching = [
        cat for cat in _PITCHING_CATS
        if st.checkbox(cat, value=cat in cfg.categories.pitching, key=f"pit_{cat}")
    ]
    if not pitching:
        st.warning("Select at least one pitching category.")

    all_cats = hitting + pitching
    rate_stats = [c for c in all_cats if c in _RATE_STATS]
    lower_is_better = [c for c in all_cats if c in _LOWER_IS_BETTER]

    return LeagueConfig(
        name=name,
        num_teams=int(num_teams),
        budget=int(budget),
        roster=RosterSlots(slots={k: int(v) for k, v in slots.items() if v > 0}),
        categories=ScoringCategories(
            hitting=hitting,
            pitching=pitching,
            rate_stats=rate_stats,
            lower_is_better=lower_is_better,
        ),
        hitter_split=float(hitter_split) / 100,
    )
