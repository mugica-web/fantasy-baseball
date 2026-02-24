# Fantasy Baseball Auction Value Calculator — Session Context

## What We've Built

A Streamlit app at `/home/mugica/projects/fantasy-baseball/` that computes SGP-based auction dollar values for fantasy baseball. Key components:

- **`app.py`** — top-level entry point (`streamlit run app.py`)
- **`pipeline.py`** — multi-pass SGP calculation pipeline (currently 11 steps)
- **`fantasy_baseball/data/`** — FanGraphs API fetcher, normalizer, reconciler, schema
- **`fantasy_baseball/sgp/`** — SGP denominators, replacement level, counting stats, rate stats
- **`fantasy_baseball/valuation/`** — dollar value conversion, keeper logic
- **`fantasy_baseball/ui/`** — Streamlit UI pages
- **`fantasy_baseball/config/`** — league config (CNMFBL: 14 teams, mixed categories)

## Bugs Fixed This Session

1. **All hitters showing as DH** — FanGraphs API returns `"minpos"` not `"Pos"`. Fixed in `normalizer.py` by adding `"minpos"` as first alias for `pos` in both batting and pitching alias dicts.

2. **`$ Value` column not visible** — was buried as ~22nd column. Fixed in `results_table.py` by moving `$ Value` and `Total SGP` into `base_cols`.

3. **Rankings going alphabetical after ~65 players** — all $1-floor players maintained insertion order. Fixed in `dollar_value.py` by adding `total_sgp` as sort tiebreaker.

4. **Ohtani appearing only as pitcher** — two bugs in `reconciler.py`: loop iterated `projections[1:]` (wrong base), and no two-way player handling. Fixed by detecting hitter+pitcher projections for same `fg_id` and keeping only hitter projections.

5. **$4 pitcher with less SGP than $1 pitcher** — position assignment used counting-stat SGP for ranking, but dollar values used full SGP. Fixed in `pipeline.py` by passing `ranking_sgp=full_sgp_pass1` to final `_compute_full_sgp` call.

6. **SGP cliff at ~#136 for BN pitchers** — BN players fell back to RP replacement stats, distorting per-category SGP for BN starters/closers. Fixed in `_compute_full_sgp` by using `_guess_position(player)` for replacement level lookup when `assigned_pos == "BN"`.

7. **Pete Fairbanks dollar/SGP mismatch** — iteration convergence issue. Fixed by adding a third convergence pass (Step 9) to `pipeline.py`.

## Current Open Issue: Replacement Level Is Slot-Dependent (WRONG)

### The Problem

The current code computes replacement level **per assigned slot** (C, 1B, 2B, 3B, SS, OF, UTIL, SP, RP, P). This creates two critical errors:

**Hitting example:** A player assigned to UTIL gets compared to UTIL replacement level, which is the "best remaining hitter at any position after primary slots are filled" — this is a *higher* bar than OF replacement level (fewer players can fill UTIL since it's overflow). So an OF assigned to UTIL gets inflated SGP_HR vs. an identical OF assigned to OF. The assigned slot shouldn't affect SGP calculation.

**Pitching example:** A pitcher assigned to the P flex slot gets compared to P replacement level instead of SP replacement level. Since P replacement is the weakest pitcher rostered, their K SGP looks higher than an identical pitcher assigned to an SP slot.

### The Correct Approach (User-Specified)

**Replacement level should be based on position eligibility and pool depth, not slot assignment.**

For hitters:
- Compute one replacement level per **primary position** (C, 1B, 2B, 3B, SS, OF) based on how many of each position are rostered across the league.
- Each player's replacement level = the one for their **scarcest/most valuable position** eligibility.
- UTIL/bench replacement = the Nth best remaining hitter where N = teams × UTIL slots.
- The key insight: UTIL replacement is NOT the same as "UTIL slot replacement" — it's the best remaining player in the open pool.

For pitchers:
- Use a **single pitcher replacement level** based on the full rostered pitcher pool (teams × total pitcher slots).
- Do not give SP and RP separate replacement levels.
- Slot assignment is an output of valuation, not an input.

### Where the Fix Needs to Go

**`fantasy_baseball/sgp/replacement_level.py`** — `compute_replacement_level()` and `ReplacementLevel.get_hitter_replacement()` / `get_pitcher_replacement()`

Current code:
```python
def get_hitter_replacement(self, position: str) -> dict[str, float]:
    return self.by_position.get(position, self.by_position.get("UTIL", {}))

def get_pitcher_replacement(self, position: str) -> dict[str, float]:
    return self.by_position.get(position, self.by_position.get("RP", {}))
```

These return per-slot replacement stats. The fix should:
1. Store a single "hitter replacement" level (stats of the Nth ranked hitter, where N = teams × active_hitter_slots)
2. Store a single "pitcher replacement" level (stats of the Nth ranked pitcher, where N = teams × active_pitcher_slots)
3. Optionally keep per-position breakdowns for display/debugging, but NOT use them in SGP calculation

**`pipeline.py` `_compute_full_sgp()`** — currently passes `repl_pos` to `counting_stat_sgp`:
```python
repl_pos = assigned_pos if assigned_pos != "BN" else _guess_position(player)
cat_sgp = counting_stat_sgp(player, repl_pos, replacement, denominators, config)
```
After the fix, `repl_pos` becomes irrelevant — every player uses the same pool-level replacement stats.

**`fantasy_baseball/sgp/counting_stats.py`** — `counting_stat_sgp()` currently does:
```python
repl_stats = replacement.get_hitter_replacement(assigned_position)
```
This should just be `replacement.hitter_replacement` (a single dict).

## Next Steps When Resuming

1. Read `replacement_level.py` fully to understand the current `ReplacementLevel` dataclass and `compute_replacement_level()` function.
2. Redesign `ReplacementLevel` to store `hitter_replacement: dict[str, float]` and `pitcher_replacement: dict[str, float]` as flat dicts (pool-level, not per-slot).
3. Update `compute_replacement_level()` to compute those pool-level stats from the Nth-ranked hitter/pitcher.
4. Update `counting_stats.py` to use the flat replacement dicts instead of position-keyed lookup.
5. Update `_compute_full_sgp()` in `pipeline.py` to remove the `repl_pos` logic.
6. Keep `by_position` in `ReplacementLevel` only for display/diagnostics, not for SGP calculation.
7. Restart Streamlit and verify that identical players at different assigned slots now have identical per-category SGP.
