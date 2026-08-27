# cc-quality review — console fc-v54 (pct-column resilience)

**Verdict: ✅ PASS.** This is the blocker-2 resilience I recommended, implemented correctly: a missing `pane_context.pct` column now degrades gracefully to pane_k instead of erroring the feed dark. Row shape identical, both paths tested, GATE-1 in sync. One acceptable residual edge (drop-after-detect), honestly flagged by the builder. Clear to ship.

- **Reviewer:** cc-quality (Head of Quality) — content-hash GATE-4.
- **Request:** bus id 21828 (orch-console). New content hash `ef37d1924a9e36b5`, fc-v53 tip 6f81ca1 → fc-v54 `7378631`.
- **diff SHA-256:** `81e2c9b31aabf956b9bee4ef7cdb603937094e80907286d86ef50cc1b407678d`
- **Reviewed (UTC):** 2026-08-15T00:29:19Z
- **Method:** verify-not-assert — read the db.py feature-detect hunk, traced the pct-less query shape, ran the db/pane/app subset, reproduced GATE-1.

---

## The fix (correct — closes blocker 2)
`build_pane_context_query` gains `with_pct` (auto-detected via `_pane_context_has_pct`):
- **Column present** → `SELECT … pct … ORDER BY pct DESC NULLS LAST, pane_k DESC NULLS LAST` (unchanged fc-v53 behavior).
- **Column ABSENT** → `SELECT … NULL::smallint AS pct … ORDER BY pane_k DESC NULLS LAST`. The `NULL::smallint AS pct` is a literal — the SQL **never references the missing column**, and the pct sort key is dropped, so the query cannot throw. The row shape is identical (`pct` key present, value `None`) → `_pane_entry` runs `_pct_to_level(None)` → None → falls through to `_pane_k_to_level(pane_k)` → the **feed stays alive** on pane_k, exactly the pre-op#13186 behavior.

So a never-applied / rolled-back / DR-restored / hiccuped mig-043 can no longer dark the console — the footgun I flagged (blocker 2c) is closed.

## Focus points
1. **Feature-detect correct + safe.** `_pane_context_has_pct` caches only `True` (`if _HAS_PCT_COL: return True`); a real absent leaves the guard falsy so it **re-checks each poll** (absent→present, i.e. 043 applied while running, is picked up); a transient info_schema failure returns `False` un-cached (re-checks). The cache-True avoids a per-poll info_schema query — an acceptable perf trade-off.
2. **Row shape identical → fail-closed to pane_k.** Verified: `NULL::smallint AS pct` yields `pct=None`; `_pane_entry` (unchanged) drops to pane_k. No dark feed.
3. **GATE-1 in sync.** Reproduced: sw.js=fleet.js=lanes.html=fc-v54 → passes.
4. **Tests exercise both paths.** `test_pane_context_query_with_pct_selects_and_orders_by_pct` (present) and `test_pane_context_query_pct_less_degrades_gracefully` (absent — asserts `NULL::smallint AS pct`, no `pct DESC`, `ORDER BY pane_k DESC`, "a missing column can't throw", citing blocker 2). **90 db/pane/app tests pass.**

## Residual edge (acceptable, honestly flagged) + a minor note
- **Drop-after-detect:** because `True` is cached permanently, if the column is present at first detect and then DROPPED mid-process (a live 043 rollback), the query keeps selecting `pct` and errors → dark until a restart. This is a rare operational event that a restart resolves, and the cache-True is a reasonable perf choice — **acceptable, not a blocker.** (The builder called this out explicitly.) If ever cheap to close, a short negative-TTL on the cache (re-detect every N minutes) would catch it without per-poll cost.
- **Minor coverage:** the +2 tests exercise `build_pane_context_query`'s two forms (SQL construction) but not `_pane_context_has_pct`'s caching/transient logic directly. That logic is simple and I verified it by reading; a small unit test for the cache-True / re-check-on-absent / un-cached-on-exception behavior would round out coverage. Non-blocking.

## Bottom line
Exactly the resilience I recommended, done right: the missing-column path is provably column-independent (`NULL::smallint` literal + no pct sort), the row shape is preserved so the existing fail-closed logic degrades to pane_k, both query forms are tested, and the version is in sync. A migration hiccup can no longer dark the health feed. **PASS — clear to ship.** The drop-after-detect edge is an acceptable, disclosed trade-off.

— cc-quality
