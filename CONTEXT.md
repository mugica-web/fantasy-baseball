# Fantasy Baseball Auction Value Calculator — Session Context

## What We've Built

A Streamlit app at `/home/mugica/projects/fantasy-baseball/` that computes SGP-based auction dollar values for fantasy baseball. Key components:

- **`app.py`** — top-level entry point (`streamlit run app.py`)
- **`pipeline.py`** — multi-pass SGP calculation pipeline (11 steps + keeper logic)
- **`fantasy_baseball/data/`** — FanGraphs API fetcher, normalizer, reconciler, schema
- **`fantasy_baseball/sgp/`** — SGP denominators, replacement level, counting stats, rate stats
- **`fantasy_baseball/valuation/`** — dollar value conversion, keeper logic
- **`fantasy_baseball/ui/`** — Streamlit UI pages (app, config editor, upload handler, results table)
- **`fantasy_baseball/config/`** — league config (CNMFBL: 12 teams, $250/team, 5x5 categories)

## Bugs Fixed (All Sessions)

1. **All hitters showing as DH** — FanGraphs API returns `"minpos"` not `"Pos"`. Fixed in `normalizer.py`.
2. **`$ Value` column not visible** — moved to `base_cols` in `results_table.py`.
3. **Rankings going alphabetical after ~65 players** — added `total_sgp` as sort tiebreaker in `dollar_value.py`.
4. **Ohtani appearing only as pitcher** — duplicate projection handling fixed in `reconciler.py`.
5. **$4 pitcher with less SGP than $1 pitcher** — position assignment now uses full SGP ranking, fixed in `pipeline.py`.
6. **SGP cliff at ~#136 for BN pitchers** — BN players now use `_guess_position()` for replacement lookup.
7. **Pete Fairbanks dollar/SGP mismatch** — fixed by adding a third convergence pass (Step 9).
8. **Keepers not removed from player pool / dollars not recalculated** — `pipeline.py` Step 11 was passing full `consensus` to `compute_dollar_values`, including keepers in the marginal pool distribution. Fixed by passing `available_consensus` only, then merging keeper entries (with `KeeperStatus` set) from `preliminary_values` back into the final output.
9. **Manual keeper form used fuzzy matching** — replaced text inputs with a searchable selectbox populated from the projection pool; `fg_id` resolved directly, no fuzzy matching.
10. **SGP denominator overrides not updating when historical standings loaded** — Streamlit only honors `value=` on first widget render. Fixed with a fingerprint check in `render_sgp_override_form()` that force-writes session state keys when denominators change.
11. **Pre-draft values lower than pre-keeper values after adding keepers** — pipeline was recomputing replacement level and SGP after removing keepers, which lowered the baseline and inflated total SGP pool-wide, diluting each player's share. Fix: keep pre-keeper `cat_sgp_final` unchanged after keeper removal; only the dollar pools change.
12. **Bench players overvalued due to IL slots in replacement level** — `RosterSlots` now has `il_slots` field; replacement level and dollar floor use `effective_bench_slots` (BN minus IL) so IL-parked players don't anchor replacement. CNMFBL set to `il_slots=3`.

## Features Added

- **Keeper modes**: manual entry (searchable selectbox, direct fg_id resolution) and prior-year roster CSV upload with surplus-based suggestions
- **Player pool size limit**: configurable max players to evaluate (default 500, split 60/40 hitters/pitchers), trimmed after rough SGP pass in Step 4b of pipeline
- **Show/hide columns toggle**: compact view (Name, Team, Positions, $ Value, Total SGP) vs. full view
- **Available $ metric**: displayed in summary bar at top of Player Values tab — total league budget minus confirmed keeper salaries
- **SGP denominator overrides**: auto-reset to newly computed values when historical standings change the denominators
- **Live Draft mode**: "Enable Live Pick Entry" checkbox in Data & Setup adds a Live Pick Entry tab; log picks (player + price) during auction and hit Recalculate to redistribute remaining dollars among undrafted players. `live_valuation.py` handles the math.
- **Three-column valuation**: Pre-Keeper $ Value (no keepers), Pre-Draft $ Value (after keeper pool adjustment), Live $ Value (during draft). Pre-Keeper only shows when keepers are active.
- **Status column**: shows "✓ Kept" / "✓ Drafted" / blank; Keeper $ and Surplus moved next to Status in full column view.
- **$0 floor for below-replacement players**: players at or below replacement SGP now valued at $0 instead of $1.
- **IL slots excluded from replacement level**: `RosterSlots.il_slots` field separates IL bench spots from real bench spots for replacement level and dollar floor calculations.

## Web Deployment Status

### Goal
Deploy the Streamlit app publicly and link it as a tile in the existing cook-timer dashboard at `https://github.com/mugica-web/cook-timer` (Flask app, hosted on Vercel).

### Why Streamlit Can't Go on Vercel
Vercel is serverless — stateless functions, 10-30s timeouts, no WebSocket support. Streamlit requires persistent server + WebSocket connections + in-memory session state. They must be separate deployments.

### Deployment Steps

1. **GitHub repo** ✅ — Code committed and pushed to `https://github.com/mugica-web/fantasy-baseball`
   - Initial commit: 28 files, all source code + `.gitignore` + `requirements.txt`
   - Remote set: `https://github.com/mugica-web/fantasy-baseball.git`

2. **Streamlit Community Cloud** ✅ — Deployed at `https://fantasy-baseball-2pnaov7ybx2ssquqmkrpmt.streamlit.app/`
   - Free tier: apps sleep after inactivity, wake on first visit (~30s delay)

3. **Add dashboard tile** ✅ — Added to `cook-timer/templates/dashboard.html`, committed and pushed.

4. **Auth gating** (optional) — Streamlit app is public once deployed. To restrict access, add a password check using `st.secrets` at the top of `app.py` before any rendering.

### Keeping the Repos in Sync
After any code changes locally, run:
```bash
git add -A && git commit -m "description" && git push
```
Streamlit Community Cloud auto-redeploys on push to `main`.
