"""
Streamlit application entry point.

Run with:
  streamlit run app.py   (from project root)

Page structure:
  Sidebar : league config editor (persists across sessions)
  Main    : tabbed interface
    Tab 1 — Data & Setup    : projection sources, standings upload, SGP denominators
    Tab 2 — Keepers         : keeper entry / upload / review
    Tab 3 — Player Values   : ranked filterable results table
    Tab 4 — SGP Breakdown   : denominator display and replacement level info
"""

import logging

import streamlit as st

from ..config.defaults import CNMFBL_CONFIG
from .config_editor import render_config_editor
from .upload_handler import (
    render_standings_upload,
    render_pecota_upload,
    render_keeper_input,
    render_sgp_override_form,
)
from .results_table import render_results_table
from .live_draft import render_live_draft_tab

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    st.set_page_config(
        page_title="Fantasy Baseball Auction Values",
        page_icon="⚾",
        layout="wide",
    )

    st.title("⚾ Fantasy Baseball Auction Value Calculator")
    st.caption("Rotisserie · SGP-based valuation · Any league configuration")

    # ── Sidebar: League Config ──────────────────────────────────────────────
    config = render_config_editor()

    # ── Main tabs ───────────────────────────────────────────────────────────
    live_draft_enabled = st.session_state.get("live_draft_enabled", False)

    if live_draft_enabled:
        tab_setup, tab_keepers, tab_values, tab_live, tab_sgp = st.tabs(
            ["Data & Setup", "Keepers", "Player Values", "Live Pick Entry", "SGP Breakdown"]
        )
    else:
        tab_setup, tab_keepers, tab_values, tab_sgp = st.tabs(
            ["Data & Setup", "Keepers", "Player Values", "SGP Breakdown"]
        )
        tab_live = None

    with tab_setup:
        _render_setup_tab(config)

    with tab_keepers:
        _render_keepers_tab(config)

    with tab_values:
        _render_values_tab(config)

    if tab_live is not None:
        with tab_live:
            render_live_draft_tab(
                player_values=st.session_state.get("player_values"),
                config=config,
                base_hitter_pool=st.session_state.get("hitter_pool", config.hitter_pool_dollars),
                base_pitcher_pool=st.session_state.get("pitcher_pool", config.pitcher_pool_dollars),
            )

    with tab_sgp:
        _render_sgp_tab()


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------

def _render_setup_tab(config):
    st.header("Data Sources & Configuration")

    # Projection systems
    st.subheader("FanGraphs Projection Systems")
    st.caption("All checked systems are fetched automatically. Deselect to speed up data loading.")
    col1, col2, col3, col4 = st.columns(4)
    systems = []
    with col1:
        if st.checkbox("Steamer", value=True, key="sys_steamer"):
            systems.append("steamer")
    with col2:
        if st.checkbox("ZiPS", value=True, key="sys_zips"):
            systems.append("zips")
    with col3:
        if st.checkbox("ATC", value=True, key="sys_atc"):
            systems.append("atc")
    with col4:
        if st.checkbox("Depth Charts", value=True, key="sys_dc"):
            systems.append("depthcharts")

    st.session_state["systems"] = systems

    # Historical standings
    standings_df = render_standings_upload()
    st.session_state["standings_df"] = standings_df

    # SGP override form (renders after denominators are computed)
    if "denominators" in st.session_state:
        overrides = render_sgp_override_form(st.session_state["denominators"])
        st.session_state["sgp_overrides"] = overrides
    else:
        st.session_state.setdefault("sgp_overrides", {})

    # PECOTA upload — store under _path keys to avoid conflicting with widget keys
    bat_path, pit_path = render_pecota_upload()
    st.session_state["pecota_bat_path"] = bat_path
    st.session_state["pecota_pit_path"] = pit_path

    st.divider()

    # Live draft mode
    st.subheader("Live Draft")
    live_enabled = st.checkbox(
        "Enable Live Pick Entry",
        value=st.session_state.get("live_draft_enabled", False),
        key="live_draft_enabled",
        help="Adds a Live Pick Entry tab to log auction picks in real time and recalculate "
             "remaining player values as dollars are spent.",
    )
    if live_enabled:
        st.caption("Live Pick Entry tab is active. Log picks there after running the pipeline.")

    st.divider()

    # Player pool size
    st.subheader("Player Pool Size")
    player_limit = st.number_input(
        "Max players to evaluate",
        min_value=50,
        max_value=2000,
        value=st.session_state.get("player_limit", 500),
        step=50,
        help="Top N players by rough SGP kept for full evaluation (split ~60% hitters / 40% pitchers). "
             "Set higher to include more fringe players; lower for faster runs.",
        key="player_limit_input",
    )
    st.session_state["player_limit"] = player_limit

    # Run button
    if st.button("🚀 Calculate Player Values", type="primary", use_container_width=True):
        _run_pipeline(config)


def _render_keepers_tab(config):
    st.header("Keeper Analysis")

    preliminary_values = st.session_state.get("player_values")
    projections = st.session_state.get("consensus_projections")

    if preliminary_values is None:
        st.info("Run the pipeline first (Data & Setup tab) to enable keeper analysis.")
        return

    mode, confirmed_keepers = render_keeper_input(
        preliminary_values=preliminary_values,
        projections=projections,
    )
    st.session_state["keeper_mode"] = mode
    st.session_state["confirmed_keepers"] = confirmed_keepers

    if confirmed_keepers:
        st.divider()
        if st.button("♻️ Recalculate with Keepers", type="primary", use_container_width=True):
            _run_pipeline(config)


def _render_values_tab(config):
    st.header("Player Values")

    player_values = st.session_state.get("player_values")
    if player_values is None:
        st.info("Run the pipeline in the 'Data & Setup' tab to see player values.")
        return

    warnings = st.session_state.get("pipeline_warnings", [])
    live_draft_enabled = st.session_state.get("live_draft_enabled", False)
    render_results_table(
        player_values,
        config,
        warnings,
        live_draft_enabled=live_draft_enabled,
        live_dollar_values=st.session_state.get("live_dollar_values"),
        live_drafted_ids=st.session_state.get("live_drafted_ids"),
    )


def _render_sgp_tab():
    st.header("SGP Breakdown")

    denominators = st.session_state.get("denominators")
    replacement = st.session_state.get("replacement_level")

    if denominators is None:
        st.info("Run the pipeline first to see SGP details.")
        return

    # Denominator table
    st.subheader("SGP Denominators")
    source_label = (
        f"Computed from {len(denominators.seasons_used)} seasons of historical standings"
        if denominators.source == "historical"
        else "⚠️ Generic defaults — upload historical standings for better accuracy"
    )
    st.caption(source_label)

    import pandas as pd
    denom_rows = []
    for cat, val in denominators.values.items():
        raw = denominators.raw_computed.get(cat, val)
        override = denominators.user_overrides.get(cat)
        n = denominators.sample_sizes.get(cat, 0)
        denom_rows.append({
            "Category": cat,
            "Computed": round(raw, 4),
            "Override": round(override, 4) if override else "—",
            "In Use": round(val, 4),
            "Sample (gaps)": n if n > 0 else "default",
        })
    st.dataframe(pd.DataFrame(denom_rows), use_container_width=True, hide_index=True)

    # Replacement level
    if replacement:
        st.subheader("Replacement Level")
        repl_rows = []
        for pos, stats in replacement.by_position.items():
            row = {"Position": pos}
            for stat, val in stats.items():
                if stat in ("R", "HR", "RBI", "SB", "K", "W", "SV"):
                    row[stat] = round(val, 1)
                elif stat in ("OBP", "AVG", "ERA", "WHIP"):
                    row[stat] = round(val, 3)
                elif stat == "PA":
                    row[stat] = round(val, 0)
                elif stat == "IP":
                    row[stat] = round(val, 1)
            repl_rows.append(row)
        if repl_rows:
            st.dataframe(pd.DataFrame(repl_rows), use_container_width=True, hide_index=True)

        st.subheader("Replacement Team Rate-Stat Baselines")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Team OBP", f"{replacement.team_obp:.3f}")
        c2.metric("Team ERA", f"{replacement.team_era:.2f}")
        c3.metric("Team WHIP", f"{replacement.team_whip:.3f}")
        c4.metric("Team PA / IP", f"{replacement.team_pa:.0f} / {replacement.team_ip:.0f}")


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def _run_pipeline(config):
    """Execute the full pipeline and store results in session state."""
    from pipeline import PipelineInputs, run_pipeline
    from ..valuation.keeper_logic import KeeperMode

    with st.spinner("Fetching projections and calculating values…"):
        try:
            inputs = PipelineInputs(
                config=config,
                projection_systems=st.session_state.get("systems", ["steamer", "zips", "atc", "depthcharts"]),
                pecota_batting_csv=st.session_state.get("pecota_bat_path"),
                pecota_pitching_csv=st.session_state.get("pecota_pit_path"),
                standings_df=st.session_state.get("standings_df"),
                sgp_overrides=st.session_state.get("sgp_overrides", {}),
                keeper_mode=st.session_state.get("keeper_mode", KeeperMode.NONE),
                confirmed_keepers=st.session_state.get("confirmed_keepers", []),
                player_limit=st.session_state.get("player_limit", 500),
            )

            result = run_pipeline(inputs)

            st.session_state["player_values"] = result.player_values
            st.session_state["denominators"] = result.denominators
            st.session_state["replacement_level"] = result.replacement_level
            st.session_state["consensus_projections"] = result.consensus_projections
            st.session_state["hitter_pool"] = result.hitter_pool
            st.session_state["pitcher_pool"] = result.pitcher_pool
            st.session_state["pipeline_warnings"] = result.warnings
            # Clear any stale live values when pipeline reruns
            st.session_state.pop("live_dollar_values", None)
            st.session_state.pop("live_drafted_ids", None)

            st.success(
                f"Done! Valued {len(result.player_values)} players. "
                "Go to the 'Player Values' tab to see results."
            )

        except Exception as e:
            st.error(f"Pipeline failed: {e}")
            logger.exception("Pipeline error")
            raise


if __name__ == "__main__":
    main()
