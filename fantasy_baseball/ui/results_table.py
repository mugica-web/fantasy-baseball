"""
Render the main output table of player values.

Filterable by:
  - Player type (hitters / pitchers / all)
  - Position eligibility
  - Availability (available / kept / all)
  - Minimum projected dollar value
  - Player name search

Columns displayed:
  Name, Team, Positions, Type, [per-category SGP columns],
  Total SGP, Projected $, Keeper Status, Sources
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ..valuation.dollar_value import PlayerValue
from ..config.league_config import LeagueConfig


def render_results_table(
    player_values: list[PlayerValue],
    config: LeagueConfig,
    warnings: list[str],
    live_draft_enabled: bool = False,
    pre_keeper_dollar_values: dict[str, float] | None = None,
    pre_keeper_total_sgp: dict[str, float] | None = None,
    live_dollar_values: dict[str, float] | None = None,
    live_drafted_ids: set[str] | None = None,
) -> None:
    """Render the full results table with filters."""

    if warnings:
        for w in warnings:
            st.warning(w)

    if not player_values:
        st.error("No player values computed. Check data sources and try again.")
        return

    has_keepers = pre_keeper_dollar_values is not None
    df = _build_dataframe(
        player_values, config,
        live_draft_enabled, pre_keeper_dollar_values, pre_keeper_total_sgp,
        live_dollar_values, live_drafted_ids,
    )

    st.subheader(f"Player Values — {config.name}")
    _render_summary_metrics(player_values, config, live_draft_enabled, live_drafted_ids, pre_keeper_total_sgp)
    filtered_df = _render_filters(df, config, live_draft_enabled, has_keepers, live_drafted_ids)
    _render_table(filtered_df, config, live_draft_enabled, has_keepers, live_dollar_values is not None)
    _render_download_button(filtered_df)


def _build_dataframe(
    player_values: list[PlayerValue],
    config: LeagueConfig,
    live_draft_enabled: bool = False,
    pre_keeper_dollar_values: dict[str, float] | None = None,
    pre_keeper_total_sgp: dict[str, float] | None = None,
    live_dollar_values: dict[str, float] | None = None,
    live_drafted_ids: set[str] | None = None,
) -> pd.DataFrame:
    """Convert PlayerValue list to a display DataFrame."""
    has_keepers = pre_keeper_dollar_values is not None
    drafted_ids = live_drafted_ids or set()
    # Rename main pipeline output column when keepers or live mode are active
    dollar_col = "Pre-Draft $ Value" if (live_draft_enabled or has_keepers) else "$ Value"
    sgp_col = "Pre-Draft SGP" if has_keepers else "Total SGP"
    show_status_col = live_draft_enabled or has_keepers

    rows = []
    for pv in player_values:
        is_drafted = live_draft_enabled and pv.fg_id in drafted_ids
        is_kept = pv.keeper_status is not None and pv.keeper_status.is_confirmed_keeper

        row: dict = {
            "Name": pv.name,
            "Team": pv.team,
            "Positions": "/".join(pv.positions) if pv.positions else "DH",
            "Type": pv.player_type.capitalize(),
            "Assigned Slot": pv.assigned_position,
            sgp_col: round(pv.total_sgp, 2),
            dollar_col: pv.dollar_value,
            "Sources": ", ".join(sorted(pv.sources_available)),
            "Available": pv.is_available,
            "fg_id": pv.fg_id,
            "_drafted": is_drafted,
        }

        if has_keepers:
            row["Pre-Keeper $ Value"] = pre_keeper_dollar_values.get(pv.fg_id, pv.dollar_value)
            if pre_keeper_total_sgp is not None:
                row["Pre-Keeper SGP"] = round(pre_keeper_total_sgp.get(pv.fg_id, pv.total_sgp), 2)

        if live_dollar_values is not None:
            row["Live $ Value"] = live_dollar_values.get(pv.fg_id, pv.dollar_value)

        if show_status_col:
            if is_kept:
                row["Status"] = "✓ Kept"
            elif is_drafted:
                row["Status"] = "✓ Drafted"
            else:
                row["Status"] = ""

        # Per-category SGP
        for cat, val in pv.category_sgp.items():
            row[f"SGP_{cat}"] = round(val, 3)

        # Key projected stats
        stats = pv.consensus_stats
        if pv.player_type == "hitter":
            for stat in ["PA", "R", "HR", "RBI", "SB", "OBP", "AVG"]:
                row[stat] = _fmt_stat(stats.get(stat), stat)
        else:
            for stat in ["IP", "W", "SV", "K", "ERA", "WHIP"]:
                row[stat] = _fmt_stat(stats.get(stat), stat)

        # Keeper salary / surplus (only relevant when keeper mode is active)
        if pv.keeper_status is not None:
            ks = pv.keeper_status
            row["Keeper $"] = ks.salary
            row["Surplus"] = round(ks.surplus, 2)
        else:
            row["Keeper $"] = None
            row["Surplus"] = None

        rows.append(row)

    return pd.DataFrame(rows)


def _fmt_stat(val, stat: str):
    """Format a stat value for display."""
    if val is None:
        return None
    if stat in ("OBP", "AVG", "ERA", "WHIP"):
        return round(float(val), 3)
    return round(float(val), 1)


def _render_summary_metrics(
    player_values: list[PlayerValue],
    config: LeagueConfig,
    live_draft_enabled: bool = False,
    live_drafted_ids: set[str] | None = None,
    pre_keeper_total_sgp: dict[str, float] | None = None,
) -> None:
    """Show high-level summary stats above the table."""
    drafted_ids = live_drafted_ids or set()
    total_players = len(player_values)
    available = sum(1 for pv in player_values if pv.is_available)
    kept = total_players - available
    hitters = sum(1 for pv in player_values if pv.player_type == "hitter" and pv.is_available)
    pitchers = sum(1 for pv in player_values if pv.player_type == "pitcher" and pv.is_available)

    total_budget = config.num_teams * config.budget
    keeper_spend = sum(
        pv.keeper_status.salary
        for pv in player_values
        if pv.keeper_status is not None and pv.keeper_status.is_confirmed_keeper
    )
    available_dollars = total_budget - keeper_spend

    if live_draft_enabled and drafted_ids:
        # Show remaining pool from session state (more accurate — uses actual prices)
        picks = st.session_state.get("draft_picks", [])
        total_draft_spent = sum(p["price"] for p in picks)
        remaining_dollars = available_dollars - total_draft_spent
        drafted_count = len(drafted_ids)
        cols = st.columns(7)
        cols[0].metric("Total players", total_players)
        cols[1].metric("Available", available - drafted_count)
        cols[2].metric("Kept", kept)
        cols[3].metric("Drafted", drafted_count)
        cols[4].metric("Rostered hitters", f"{hitters} / {config.total_hitter_slots}")
        cols[5].metric("Rostered pitchers", f"{pitchers} / {config.total_pitcher_slots}")
        cols[6].metric("Remaining $", f"${remaining_dollars:,.0f}")
    else:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total players", total_players)
        c2.metric("Available", available)
        c3.metric("Kept", kept)
        c4.metric("Rostered hitters", f"{hitters} / {config.total_hitter_slots}")
        c5.metric("Rostered pitchers", f"{pitchers} / {config.total_pitcher_slots}")
        c6.metric("Available $", f"${available_dollars:,.0f}")

    # SGP pool diagnostics — always shown once the pipeline has run
    hitter_slots = config.effective_total_hitter_slots
    pitcher_slots = config.effective_total_pitcher_slots

    num_hitter_keepers = sum(
        1 for pv in player_values
        if pv.keeper_status is not None and pv.keeper_status.is_confirmed_keeper
        and pv.player_type == "hitter"
    )
    num_pitcher_keepers = sum(
        1 for pv in player_values
        if pv.keeper_status is not None and pv.keeper_status.is_confirmed_keeper
        and pv.player_type == "pitcher"
    )
    remaining_hitter_slots = max(hitter_slots - num_hitter_keepers, 0)
    remaining_pitcher_slots = max(pitcher_slots - num_pitcher_keepers, 0)

    # Current auction pool SGP (available players, capped to remaining slots)
    cur_h_sgp = sum(
        v for v in sorted(
            [pv.total_sgp for pv in player_values if pv.player_type == "hitter" and pv.is_available],
            reverse=True,
        )[:remaining_hitter_slots]
        if v > 0
    )
    cur_p_sgp = sum(
        v for v in sorted(
            [pv.total_sgp for pv in player_values if pv.player_type == "pitcher" and pv.is_available],
            reverse=True,
        )[:remaining_pitcher_slots]
        if v > 0
    )

    with st.expander("SGP Pool Diagnostics (denominator used for $ distribution)"):
        if pre_keeper_total_sgp is not None:
            # Pre-keeper: top hitter_slots hitters by pre-keeper SGP across all players
            pre_h_sgp = sum(
                v for v in sorted(
                    [pre_keeper_total_sgp.get(pv.fg_id, 0.0) for pv in player_values if pv.player_type == "hitter"],
                    reverse=True,
                )[:hitter_slots]
                if v > 0
            )
            pre_p_sgp = sum(
                v for v in sorted(
                    [pre_keeper_total_sgp.get(pv.fg_id, 0.0) for pv in player_values if pv.player_type == "pitcher"],
                    reverse=True,
                )[:pitcher_slots]
                if v > 0
            )
            d1, d2, d3, d4 = st.columns(4)
            d1.metric(f"Pre-Keeper Hitter SGP ({hitter_slots} slots)", f"{pre_h_sgp:.1f}")
            d2.metric(
                f"Auction Hitter SGP ({remaining_hitter_slots} slots)",
                f"{cur_h_sgp:.1f}",
                delta=f"{cur_h_sgp - pre_h_sgp:+.1f}",
            )
            d3.metric(f"Pre-Keeper Pitcher SGP ({pitcher_slots} slots)", f"{pre_p_sgp:.1f}")
            d4.metric(
                f"Auction Pitcher SGP ({remaining_pitcher_slots} slots)",
                f"{cur_p_sgp:.1f}",
                delta=f"{cur_p_sgp - pre_p_sgp:+.1f}",
            )
        else:
            d1, d2 = st.columns(2)
            d1.metric(f"Hitter SGP in pool ({remaining_hitter_slots} slots)", f"{cur_h_sgp:.1f}")
            d2.metric(f"Pitcher SGP in pool ({remaining_pitcher_slots} slots)", f"{cur_p_sgp:.1f}")


def _render_filters(
    df: pd.DataFrame,
    config: LeagueConfig,
    live_draft_enabled: bool = False,
    has_keepers: bool = False,
    live_drafted_ids: set[str] | None = None,
) -> pd.DataFrame:
    """Render filter controls and return the filtered DataFrame."""
    value_col = "Pre-Draft $ Value" if (live_draft_enabled or has_keepers) else "$ Value"

    num_filter_cols = 6 if live_draft_enabled else 5
    filter_cols = st.columns(num_filter_cols)

    with filter_cols[0]:
        player_type = st.selectbox(
            "Player type",
            options=["All", "Hitters", "Pitchers"],
            key="filter_type",
        )

    with filter_cols[1]:
        all_positions = sorted({
            pos
            for pos_str in df["Positions"].dropna()
            for pos in str(pos_str).split("/")
            if pos
        })
        position_filter = st.selectbox(
            "Position",
            options=["All"] + all_positions,
            key="filter_pos",
        )

    with filter_cols[2]:
        availability = st.selectbox(
            "Availability",
            options=["Available only", "All", "Kept only"],
            key="filter_avail",
        )

    with filter_cols[3]:
        min_value = st.number_input(
            "Min $ value",
            min_value=0,
            max_value=100,
            value=1,
            step=1,
            key="filter_minval",
        )

    with filter_cols[4]:
        name_search = st.text_input("Search name", key="filter_name").strip().lower()

    # Live draft: show/hide drafted toggle
    show_drafted = True
    if live_draft_enabled and live_drafted_ids:
        with filter_cols[5]:
            show_drafted = st.toggle("Show drafted", value=True, key="filter_show_drafted")

    # Apply filters
    filtered = df.copy()

    if player_type == "Hitters":
        filtered = filtered[filtered["Type"] == "Hitter"]
    elif player_type == "Pitchers":
        filtered = filtered[filtered["Type"] == "Pitcher"]

    if position_filter != "All":
        filtered = filtered[filtered["Positions"].str.contains(position_filter, na=False)]

    if availability == "Available only":
        filtered = filtered[filtered["Available"] == True]
    elif availability == "Kept only":
        filtered = filtered[filtered["Available"] == False]

    filtered = filtered[filtered[value_col] >= min_value]

    if name_search:
        filtered = filtered[filtered["Name"].str.lower().str.contains(name_search, na=False)]

    if live_draft_enabled and not show_drafted:
        filtered = filtered[filtered["_drafted"] == False]

    # Sort drafted players to the bottom when showing them
    if live_draft_enabled and show_drafted and "_drafted" in filtered.columns:
        filtered = filtered.sort_values("_drafted", kind="stable")

    return filtered


def _render_table(
    df: pd.DataFrame,
    config: LeagueConfig,
    live_draft_enabled: bool = False,
    has_keepers: bool = False,
    has_live_values: bool = False,
) -> None:
    """Render the results DataFrame with appropriate column formatting."""
    dollar_col = "Pre-Draft $ Value" if (live_draft_enabled or has_keepers) else "$ Value"
    sgp_col = "Pre-Draft SGP" if has_keepers else "Total SGP"
    show_status_col = live_draft_enabled or has_keepers

    show_all = st.toggle("Show all columns", value=False, key="toggle_all_cols")

    if not show_all:
        compact_cols = ["Name", "Team", "Positions"]
        if has_keepers:
            compact_cols += ["Pre-Keeper $ Value", "Pre-Keeper SGP"]
        compact_cols.append(dollar_col)
        compact_cols.append(sgp_col)
        if has_live_values:
            compact_cols.append("Live $ Value")
        if show_status_col:
            compact_cols.append("Status")
        display_cols = [c for c in compact_cols if c in df.columns]
    else:
        # Value columns in order: Pre-Keeper $ → Pre-Keeper SGP → Pre-Draft $ → Pre-Draft SGP → Live $ → Status → Keeper $ → Surplus
        base_cols = ["Name", "Team", "Positions", "Type", "Assigned Slot"]
        if has_keepers:
            base_cols += ["Pre-Keeper $ Value", "Pre-Keeper SGP"]
        base_cols += [dollar_col, sgp_col]
        if has_live_values:
            base_cols.append("Live $ Value")
        if show_status_col:
            base_cols.append("Status")
        # Keeper $ and Surplus right after Status
        base_cols += [c for c in ["Keeper $", "Surplus"] if c in df.columns]

        has_hitters = "Hitter" in df["Type"].values
        has_pitchers = "Pitcher" in df["Type"].values

        stat_cols: list[str] = []
        if has_hitters and not has_pitchers:
            stat_cols = [c for c in ["PA", "R", "HR", "RBI", "SB", "OBP", "AVG"] if c in df.columns]
        elif has_pitchers and not has_hitters:
            stat_cols = [c for c in ["IP", "W", "SV", "K", "ERA", "WHIP"] if c in df.columns]
        else:
            stat_cols = [c for c in ["PA", "R", "HR", "RBI", "SB", "OBP", "IP", "W", "SV", "K", "ERA", "WHIP"] if c in df.columns]

        sgp_cols = sorted([c for c in df.columns if c.startswith("SGP_")])

        display_cols = base_cols + stat_cols + sgp_cols
        display_cols = [c for c in display_cols if c in df.columns]

    # Drop internal columns before display
    display_df = df[[c for c in display_cols if c in df.columns]].copy()
    display_df.index = range(1, len(display_df) + 1)

    if live_draft_enabled and not has_live_values:
        st.info("Log picks and hit **Recalculate Live Values** in the Live Pick Entry tab to see updated values.")

    column_config = {
        dollar_col: st.column_config.NumberColumn(dollar_col, format="$%.2f"),
        "Pre-Keeper $ Value": st.column_config.NumberColumn("Pre-Keeper $ Value", format="$%.2f"),
        "Live $ Value": st.column_config.NumberColumn("Live $ Value", format="$%.2f"),
        "Pre-Draft SGP": st.column_config.NumberColumn("Pre-Draft SGP", format="%.2f"),
        "Pre-Keeper SGP": st.column_config.NumberColumn("Pre-Keeper SGP", format="%.2f"),
        "Total SGP": st.column_config.NumberColumn("Total SGP", format="%.2f"),
        "Surplus": st.column_config.NumberColumn("Surplus", format="$%.2f"),
        "Keeper $": st.column_config.NumberColumn("Keeper $", format="$%.0f"),
        "OBP": st.column_config.NumberColumn("OBP", format="%.3f"),
        "AVG": st.column_config.NumberColumn("AVG", format="%.3f"),
        "ERA": st.column_config.NumberColumn("ERA", format="%.2f"),
        "WHIP": st.column_config.NumberColumn("WHIP", format="%.3f"),
    }

    st.dataframe(
        display_df,
        use_container_width=True,
        height=1200,
        column_config=column_config,
    )

    st.caption(f"Showing {len(display_df)} of {len(df)} players")


def _render_download_button(df: pd.DataFrame) -> None:
    """Render a CSV download button for the filtered results."""
    _internal = {"Available", "fg_id", "_drafted"}
    export_df = df.drop(columns=[c for c in _internal if c in df.columns])
    csv = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download filtered results as CSV",
        data=csv,
        file_name="fantasy_baseball_values.csv",
        mime="text/csv",
    )
