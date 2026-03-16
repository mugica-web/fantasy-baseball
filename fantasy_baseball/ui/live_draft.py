"""
Live Pick Entry tab — log auction picks in real time and recalculate remaining
player values based on dollars spent so far.
"""

from __future__ import annotations

import streamlit as st

from ..valuation.dollar_value import PlayerValue
from ..valuation.live_valuation import DraftPick, compute_live_values
from ..config.league_config import LeagueConfig


def render_live_draft_tab(
    player_values: list[PlayerValue] | None,
    config: LeagueConfig,
    base_hitter_pool: float,
    base_pitcher_pool: float,
) -> None:
    """Render the Live Pick Entry tab."""
    st.header("Live Pick Entry")
    st.caption(
        "Log picks as they happen during the auction. "
        "Hit **Recalculate Live Values** to redistribute remaining dollars among undrafted players."
    )

    if player_values is None:
        st.info("Run the pipeline first (Data & Setup tab) to enable live draft tracking.")
        return

    if "draft_picks" not in st.session_state:
        st.session_state["draft_picks"] = []

    picks: list[dict] = st.session_state["draft_picks"]

    # ── Summary ─────────────────────────────────────────────────────────────
    _render_summary(picks, config, base_hitter_pool, base_pitcher_pool)

    st.divider()

    # ── Recalculate button ───────────────────────────────────────────────────
    if st.button("🔄 Recalculate Live Values", type="primary", use_container_width=True):
        _recalculate(player_values, picks, config, base_hitter_pool, base_pitcher_pool)
        st.success("Live values updated — go to the Player Values tab to see results.")

    st.divider()

    # ── Pick entry ───────────────────────────────────────────────────────────
    st.subheader("Log a Pick")

    _BLANK = "— select player —"
    drafted_ids = {p["fg_id"] for p in picks}

    # Only show undrafted, available players (not keepers)
    available_players = sorted(
        [pv for pv in player_values if pv.fg_id not in drafted_ids and pv.is_available],
        key=lambda pv: pv.name,
    )
    player_options = [_BLANK] + [f"{pv.name} ({pv.team})" for pv in available_players]
    pv_by_label = {f"{pv.name} ({pv.team})": pv for pv in player_values}

    col1, col2, col3 = st.columns([5, 2, 1])
    with col1:
        selected = st.selectbox("Player", options=player_options, key="live_pick_player")
    with col2:
        price = st.number_input(
            "Price $",
            min_value=1,
            max_value=config.budget,
            value=1,
            key="live_pick_price",
        )
    with col3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Add", key="live_pick_add"):
            if selected != _BLANK:
                pv = pv_by_label.get(selected)
                if pv:
                    picks.append({
                        "fg_id": pv.fg_id,
                        "name": pv.name,
                        "team": pv.team,
                        "price": int(price),
                        "type": pv.player_type,
                    })
                    st.rerun()

    # ── Picks log ────────────────────────────────────────────────────────────
    st.divider()
    if picks:
        st.subheader(f"Picks Logged ({len(picks)})")
        for i, pick in enumerate(picks):
            c1, c2, c3, c4 = st.columns([4, 2, 2, 1])
            c1.write(f"**{pick['name']}** ({pick['team']})")
            c2.write(pick["type"].capitalize())
            c3.write(f"${pick['price']}")
            with c4:
                if st.button("✕", key=f"live_remove_{i}"):
                    picks.pop(i)
                    st.rerun()
    else:
        st.info("No picks logged yet.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_summary(
    picks: list[dict],
    config: LeagueConfig,
    base_hitter_pool: float,
    base_pitcher_pool: float,
) -> None:
    hitter_picks = [p for p in picks if p.get("type") == "hitter"]
    pitcher_picks = [p for p in picks if p.get("type") == "pitcher"]
    hitter_spent = sum(p["price"] for p in hitter_picks)
    pitcher_spent = sum(p["price"] for p in pitcher_picks)
    total_spent = hitter_spent + pitcher_spent
    remaining_total = max(base_hitter_pool - hitter_spent, 0) + max(base_pitcher_pool - pitcher_spent, 0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Picks logged", len(picks))
    c2.metric("Total spent", f"${total_spent:,.0f}")
    c3.metric("Hitter $ spent", f"${hitter_spent:,.0f}")
    c4.metric("Pitcher $ spent", f"${pitcher_spent:,.0f}")
    c5.metric("Remaining pool", f"${remaining_total:,.0f}")


def _recalculate(
    player_values: list[PlayerValue],
    picks: list[dict],
    config: LeagueConfig,
    base_hitter_pool: float,
    base_pitcher_pool: float,
) -> None:
    draft_picks = [
        DraftPick(fg_id=p["fg_id"], name=p["name"], price=p["price"])
        for p in picks
    ]
    live_values = compute_live_values(
        player_values=player_values,
        draft_picks=draft_picks,
        base_hitter_pool=base_hitter_pool,
        base_pitcher_pool=base_pitcher_pool,
        config=config,
    )
    st.session_state["live_dollar_values"] = live_values
    st.session_state["live_drafted_ids"] = {p["fg_id"] for p in picks}
