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
) -> None:
    """Render the full results table with filters."""

    if warnings:
        for w in warnings:
            st.warning(w)

    if not player_values:
        st.error("No player values computed. Check data sources and try again.")
        return

    df = _build_dataframe(player_values, config)

    st.subheader(f"Player Values — {config.name}")
    _render_summary_metrics(player_values, config)
    filtered_df = _render_filters(df, config)
    _render_table(filtered_df, config)
    _render_download_button(filtered_df)


def _build_dataframe(player_values: list[PlayerValue], config: LeagueConfig) -> pd.DataFrame:
    """Convert PlayerValue list to a display DataFrame."""
    rows = []
    for pv in player_values:
        row: dict = {
            "Name": pv.name,
            "Team": pv.team,
            "Positions": "/".join(pv.positions) if pv.positions else "DH",
            "Type": pv.player_type.capitalize(),
            "Assigned Slot": pv.assigned_position,
            "Total SGP": round(pv.total_sgp, 2),
            "$ Value": pv.dollar_value,
            "Sources": ", ".join(sorted(pv.sources_available)),
            "Available": pv.is_available,
            "fg_id": pv.fg_id,
        }

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

        # Keeper info
        if pv.keeper_status is not None:
            ks = pv.keeper_status
            row["Keeper $"] = ks.salary
            row["Surplus"] = round(ks.surplus, 2)
            row["Keeper"] = "✓ Kept" if ks.is_confirmed_keeper else ""
        else:
            row["Keeper $"] = None
            row["Surplus"] = None
            row["Keeper"] = ""

        rows.append(row)

    return pd.DataFrame(rows)


def _fmt_stat(val, stat: str):
    """Format a stat value for display."""
    if val is None:
        return None
    if stat in ("OBP", "AVG", "ERA", "WHIP"):
        return round(float(val), 3)
    return round(float(val), 1)


def _render_summary_metrics(player_values: list[PlayerValue], config: LeagueConfig) -> None:
    """Show high-level summary stats above the table."""
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

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total players", total_players)
    c2.metric("Available", available)
    c3.metric("Kept", kept)
    c4.metric("Rostered hitters", f"{hitters} / {config.total_hitter_slots}")
    c5.metric("Rostered pitchers", f"{pitchers} / {config.total_pitcher_slots}")
    c6.metric("Available $", f"${available_dollars:,.0f}")


def _render_filters(df: pd.DataFrame, config: LeagueConfig) -> pd.DataFrame:
    """Render filter controls and return the filtered DataFrame."""
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        player_type = st.selectbox(
            "Player type",
            options=["All", "Hitters", "Pitchers"],
            key="filter_type",
        )

    with col2:
        # Collect all positions from the data
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

    with col3:
        availability = st.selectbox(
            "Availability",
            options=["Available only", "All", "Kept only"],
            key="filter_avail",
        )

    with col4:
        min_value = st.number_input(
            "Min $ value",
            min_value=1,
            max_value=100,
            value=1,
            step=1,
            key="filter_minval",
        )

    with col5:
        name_search = st.text_input("Search name", key="filter_name").strip().lower()

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

    filtered = filtered[filtered["$ Value"] >= min_value]

    if name_search:
        filtered = filtered[filtered["Name"].str.lower().str.contains(name_search, na=False)]

    return filtered


def _render_table(df: pd.DataFrame, config: LeagueConfig) -> None:
    """Render the results DataFrame with appropriate column formatting."""

    show_all = st.toggle("Show all columns", value=False, key="toggle_all_cols")

    if not show_all:
        compact_cols = ["Name", "Team", "Positions", "$ Value", "Total SGP"]
        display_cols = [c for c in compact_cols if c in df.columns]
    else:
        # Build display column order — $ Value and Total SGP first for visibility
        base_cols = ["Name", "Team", "Positions", "Type", "Assigned Slot", "$ Value", "Total SGP"]

        # Stat columns depend on player type mix in filtered df
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
        keeper_cols = [c for c in ["Keeper", "Keeper $", "Surplus"] if c in df.columns]

        display_cols = base_cols + stat_cols + sgp_cols + keeper_cols
        display_cols = [c for c in display_cols if c in df.columns]

    display_df = df[display_cols].copy()
    display_df.index = range(1, len(display_df) + 1)

    st.dataframe(
        display_df,
        use_container_width=True,
        height=1200,
        column_config={
            "$ Value": st.column_config.NumberColumn("$ Value", format="$%.2f"),
            "Total SGP": st.column_config.NumberColumn("Total SGP", format="%.2f"),
            "Surplus": st.column_config.NumberColumn("Surplus", format="$%.2f"),
            "Keeper $": st.column_config.NumberColumn("Keeper $", format="$%.0f"),
            "OBP": st.column_config.NumberColumn("OBP", format="%.3f"),
            "AVG": st.column_config.NumberColumn("AVG", format="%.3f"),
            "ERA": st.column_config.NumberColumn("ERA", format="%.2f"),
            "WHIP": st.column_config.NumberColumn("WHIP", format="%.3f"),
        },
    )

    st.caption(f"Showing {len(display_df)} of {len(df)} players")


def _render_download_button(df: pd.DataFrame) -> None:
    """Render a CSV download button for the filtered results."""
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download filtered results as CSV",
        data=csv,
        file_name="fantasy_baseball_values.csv",
        mime="text/csv",
    )
