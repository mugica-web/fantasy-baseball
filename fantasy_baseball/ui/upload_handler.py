"""
Streamlit upload handlers for optional user-provided data:
  - Historical standings CSV (for SGP denominator calculation)
  - PECOTA projections CSVs (batting and pitching)
  - Prior-year rosters CSV (for keeper analysis)
  - Manual keeper entries (form-based input)

Each function renders its own UI section and returns the parsed data
(or None if not provided), keeping the main app.py clean.
"""

from __future__ import annotations
import io
import tempfile
import os

import pandas as pd
import streamlit as st

from ..valuation.keeper_logic import KeeperMode, KeeperEntry


def render_standings_upload() -> pd.DataFrame | None:
    """
    Render the historical standings uploader.

    Expected CSV format:
      season, Team, R, HR, RBI, SB, OBP, K, W, SV, ERA, WHIP
      (columns must match the league's scoring categories; Team column optional)

    Returns a DataFrame if uploaded and valid, otherwise None.
    """
    st.subheader("Historical Standings (Recommended)")
    st.caption(
        "Upload final standings from past seasons to calibrate SGP denominators "
        "to your specific league. One row per team per season. "
        "Requires a `season` column and one column per scoring category."
    )

    uploaded = st.file_uploader(
        "Upload standings CSV",
        type=["csv"],
        key="standings_upload",
        help="e.g. 3-5 years of final standings exported from your league platform",
    )

    if uploaded is None:
        st.info(
            "No standings uploaded — generic default denominators will be used. "
            "Values will be less accurate without your league's historical data."
        )
        return None

    try:
        df = pd.read_csv(uploaded)
        if "season" not in df.columns:
            st.error("Standings CSV must include a `season` column.")
            return None
        st.success(f"Loaded standings: {len(df)} rows across {df['season'].nunique()} season(s).")
        with st.expander("Preview standings data"):
            st.dataframe(df.head(20), use_container_width=True)
        return df
    except Exception as e:
        st.error(f"Error reading standings CSV: {e}")
        return None


def render_pecota_upload() -> tuple[str | None, str | None]:
    """
    Render PECOTA projection uploaders.

    Returns (batting_csv_path, pitching_csv_path) as temp file paths,
    or (None, None) if not uploaded.
    """
    with st.expander("PECOTA Projections (Optional)"):
        st.caption(
            "Upload PECOTA CSVs from Baseball Prospectus to include in the "
            "consensus projection average. FanGraphs projections are always "
            "fetched automatically; PECOTA is additive."
        )

        bat_file = st.file_uploader("PECOTA batting projections CSV", type=["csv"], key="pecota_bat")
        pit_file = st.file_uploader("PECOTA pitching projections CSV", type=["csv"], key="pecota_pit")

        bat_path = _save_upload_to_tempfile(bat_file, "pecota_bat")
        pit_path = _save_upload_to_tempfile(pit_file, "pecota_pit")

    return bat_path, pit_path


def render_keeper_input(
    preliminary_values=None,
    projections=None,
) -> tuple[KeeperMode, list[KeeperEntry]]:
    """
    Render keeper input UI. Returns (mode, confirmed_keepers).

    Three modes:
      NONE             — no keepers
      MANUAL           — user enters player + salary pairs
      PRIOR_YEAR       — upload prior-year roster CSV
    """
    st.subheader("Keepers (Optional)")

    mode_label = st.radio(
        "Keeper mode",
        options=["No keepers", "Enter keepers manually", "Upload prior-year rosters"],
        horizontal=True,
        key="keeper_mode_radio",
    )

    if mode_label == "No keepers":
        return KeeperMode.NONE, []

    elif mode_label == "Enter keepers manually":
        return _render_manual_keeper_form(projections or [])

    else:
        return _render_prior_year_upload(preliminary_values or [], projections or [])


def _render_manual_keeper_form(
    projections,
) -> tuple[KeeperMode, list[KeeperEntry]]:
    """Render a dynamic form for entering keeper name + salary pairs."""
    st.caption("Enter each keeper and their contract salary.")

    # Build searchable player options from the actual projection pool
    _BLANK = "— select player —"
    player_options = [_BLANK] + sorted(
        {f"{p.name} ({p.team})" for p in projections}
    )
    proj_by_label = {f"{p.name} ({p.team})": p for p in projections}

    if "keeper_rows" not in st.session_state:
        st.session_state["keeper_rows"] = [{"label": _BLANK, "salary": 1}]

    rows = st.session_state["keeper_rows"]
    # Migrate old-format rows (name/team text fields → label selectbox)
    for row in rows:
        if "label" not in row:
            row["label"] = _BLANK

    for i, row in enumerate(rows):
        c1, c2, c3 = st.columns([5, 2, 1])
        with c1:
            current_idx = player_options.index(row["label"]) if row["label"] in player_options else 0
            row["label"] = st.selectbox(
                "Player",
                options=player_options,
                index=current_idx,
                key=f"kplayer_{i}",
            )
        with c2:
            row["salary"] = st.number_input(
                "Salary $",
                min_value=1, max_value=500,
                value=max(1, int(row.get("salary", 1))),
                key=f"ksal_{i}",
            )
        with c3:
            st.markdown("<br>", unsafe_allow_html=True)  # align button vertically
            if st.button("✕", key=f"kremove_{i}"):
                rows.pop(i)
                st.rerun()

    if st.button("+ Add keeper"):
        rows.append({"label": _BLANK, "salary": 1})
        st.rerun()

    # Build KeeperEntry objects directly — no fuzzy matching needed
    entries: list[KeeperEntry] = []
    for row in rows:
        label = row.get("label", _BLANK)
        if not label or label == _BLANK:
            continue
        proj = proj_by_label.get(label)
        if proj is None:
            continue
        entries.append(KeeperEntry(
            fg_id=proj.fg_id,
            name=proj.name,
            team=proj.team,
            salary=float(row["salary"]),
        ))

    if entries:
        st.success(f"{len(entries)} keeper(s) entered.")
    return KeeperMode.MANUAL, entries


def _render_prior_year_upload(
    preliminary_values,
    projections,
) -> tuple[KeeperMode, list[KeeperEntry]]:
    """Render prior-year roster uploader and keeper suggestion table."""
    from ..valuation.keeper_logic import parse_prior_year_roster_csv

    st.caption(
        "Upload a prior-year roster CSV. "
        "Expected columns: name, team, salary, eligible_to_keep (optional), owner (optional)."
    )

    uploaded = st.file_uploader("Prior-year roster CSV", type=["csv"], key="prior_year_upload")
    if not uploaded:
        return KeeperMode.PRIOR_YEAR_ROSTERS, []

    path = _save_upload_to_tempfile(uploaded, "prior_year")
    if not path:
        return KeeperMode.PRIOR_YEAR_ROSTERS, []

    try:
        all_entries, review_df = parse_prior_year_roster_csv(path, projections, preliminary_values)
    except Exception as e:
        st.error(f"Error parsing roster CSV: {e}")
        return KeeperMode.PRIOR_YEAR_ROSTERS, []

    st.markdown("**Review suggested keepers** (check the ones being kept):")

    # Render editable table — user checks which players are confirmed keepers
    confirmed_ids: set[str] = set()
    for _, row in review_df.iterrows():
        color = "🟢" if row["suggested_keep"] else "🔴"
        checked = st.checkbox(
            f"{color} {row['name']} ({row['team']}) — "
            f"Salary: ${row['salary']} | Proj. value: ${row['projected_value']} | "
            f"Surplus: ${row['surplus']:+.2f}",
            value=bool(row["suggested_keep"]),
            key=f"keep_{row['name']}_{row['team']}",
        )
        if checked:
            # Find the matching entry by name
            for entry in all_entries:
                if entry.name == row["name"] and entry.team == row["team"]:
                    confirmed_ids.add(entry.fg_id)

    confirmed_entries = [e for e in all_entries if e.fg_id in confirmed_ids]
    st.info(f"{len(confirmed_entries)} keeper(s) confirmed.")
    return KeeperMode.PRIOR_YEAR_ROSTERS, confirmed_entries


def render_sgp_override_form(denominators) -> dict[str, float]:
    """
    Render an SGP denominator override form.

    Shows the computed (or default) denominator values and lets users
    override individual categories before running the pipeline.
    """
    overrides: dict[str, float] = {}

    source_label = (
        f"Historical ({len(denominators.seasons_used)} seasons)"
        if denominators.source == "historical"
        else "Generic defaults ⚠️"
    )

    # When denominators change (e.g. historical standings uploaded after defaults),
    # reset the widget state so inputs reflect the newly computed values rather than
    # stale stored values. Streamlit only honors `value=` on first widget creation;
    # updating session state keys directly is the only way to force a refresh.
    denom_fingerprint = tuple(sorted((k, round(v, 6)) for k, v in denominators.values.items()))
    if st.session_state.get("_denom_fingerprint") != denom_fingerprint:
        st.session_state["_denom_fingerprint"] = denom_fingerprint
        for cat, val in denominators.values.items():
            st.session_state[f"sgp_override_{cat}"] = float(val)

    with st.expander(f"SGP Denominators — Source: {source_label}"):
        if denominators.source == "defaults":
            st.warning(
                "These are generic defaults, not calibrated to your league. "
                "Upload historical standings above for better accuracy."
            )

        st.caption("Override any value below (leave blank to keep computed value).")

        for cat, val in denominators.raw_computed.items():
            n = denominators.sample_sizes.get(cat, 0)
            label = f"{cat}  (computed: {val:.4f}, n={n} gaps)"
            override_val = st.number_input(
                label,
                min_value=0.0001,
                value=float(denominators.values.get(cat, val)),
                format="%.4f",
                step=0.0001,
                key=f"sgp_override_{cat}",
            )
            if abs(override_val - val) > 1e-6:
                overrides[cat] = override_val

    return overrides


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_upload_to_tempfile(uploaded_file, prefix: str) -> str | None:
    """Save a Streamlit uploaded file to a temp file and return its path."""
    if uploaded_file is None:
        return None
    suffix = ".csv"
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(uploaded_file.read())
        return path
    except Exception:
        return None
