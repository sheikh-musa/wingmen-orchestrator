# cc-quality Gate-4 review — console content-hash `9ca8a095f8c2ffd8`

**Verdict: PASS** (deploy-cleared). **Auditor:** cc-quality (Opus 4.8). **Date:** 2026-08-24.
**Commit:** 8397d4b (fable/substrate-safe-fixes, HEAD) — never-blank worker lane-context (item #2/#3, Musa-flagged via Nazim #32472/#32489).
**Requester:** cc-fleet-health #32515 (deploy-blocking, Nazim's `deploy_console` Gate-4).

Hash covers all served static + every console backend `*.py` + the Dockerfile (item-4b). Content hash re-computed
locally via `console_content_hash` = `9ca8a095f8c2ffd8` at this HEAD; version-sync gate step already green.

## Tests (run myself at HEAD, not taken on report)
- `pytest tests/console/test_pane_bloat.py tests/console/test_db.py tests/test_pane_context_upsert.py` → **46 passed**.
- `node tests/console/fleet_lanectx.test.js` → **12 assertions passed**.
- The upsert test exercises the SHIPPED `_PANE_UPSERT_SQL` against a real rolled-back scratch row (freeze-vs-bump proven via a seeded PAST `pane_k_at`, since `now()` is tx-stable). Green.

## KEY INVARIANT ("try to break the header/card separation") — HOLDS, both ends
A LAST-KNOWN (stale) reading may reach ONLY the per-lane card, never the header/glance alert.
- **Server:** stale entries are emitted only when `allow_stale=True`, and the ONLY caller passing True is
  `context_bloat = _pane_bloat(pane_rows, allow_stale=True)` (app.py:1384, the worker per-lane-card feed). The
  whole-fleet GLANCE (`bloat_glance`, 1385/1264), the header (`_pane_header`, separate fn), and the irsyad view
  (1560) all default `allow_stale=False` → LIVE-only. `_pane_entry`: fresh (`pane_k_age_s < 660`) always emitted;
  last-known (`660 ≤ age < 3600`) emitted only if `allow_stale`; aged-out (`≥ 3600`) dropped; `pct` (cliff line) is
  never COALESCE-kept so a pct reading is always LIVE.
- **Client:** `ctxDisplayFrom` → {off, live, stale, label}; the bloat-highlight/alert escalation fires **only on
  `mode==="live"` amber/red** (`var bloat = disp.mode === "live" && …`), so a stale card reading renders (dimmed
  `~{k}k · {age}`) but never drives an alert — matching the server live-only header.
- **Attack attempts that FAILED to break it:**
  1. `pane_k_at IS NULL → treated as LIVE` branch: bounded by the row-TTL `WHERE updated_at > now() - PANE_TTL_S`
     with `PANE_TTL_S = _PANE_K_LIVE_S = 660`, so such a row is ≤660s fresh anyway; AND mig-057's backfill
     (`SET pane_k_at = updated_at WHERE pane_k IS NOT NULL`) stamps every pre-existing non-null row with its
     last-publish time, so stale pre-existing rows age out correctly rather than showing LIVE. No leak.
  2. Client alert via a stale card entry: blocked by the `mode==="live"`-only bloat guard.

## Never-blank + bounded last-known — correct
- 3 states + offline: `live` (%-ring), `stale` (`~{k}k · {age}`, dimmed, visibly NOT a live %, age in tooltip),
  `label` (honest idle/low/n-a from `idle_verdict`), `off` (faint `—`, honest for an offline lane). Null display
  defaults to a `label` ("n/a"), never a bare blank. Sheet pill mirrors all four.
- Bounded: LAST_KNOWN_MAX_S = 3600 → a recycled lane ages out to a label instead of showing its pre-recycle high %
  (the stale-gauge antipattern is avoided). `_PANE_K_LIVE_S = 660` tolerates one missed 300s publish so a live lane
  never flaps to stale.

## Publish upsert (freeze-vs-bump) — matches shipped SQL
`_PANE_UPSERT_SQL`: `pane_k = COALESCE(EXCLUDED.pane_k, pane_context.pane_k)` (keeps last-known);
`pane_k_at = CASE WHEN EXCLUDED.pane_k IS NOT NULL THEN now() ELSE pane_context.pane_k_at END` (bumps only on a
non-null capture; a null/mid-turn capture never overwrites or re-stamps a good last-known — Nazim bake-in b);
`pct = EXCLUDED.pct` (never COALESCE-kept — cliff-only, always current). `updated_at = now()` every publish; an
`updated_at`-based reaper prunes old rows.

## mig-057 provenance / fail-safe
- Pure additive `ALTER TABLE pane_context ADD COLUMN IF NOT EXISTS pane_k_at timestamptz` + a safe backfill; no
  grant/policy/constraint touched (ops-cache table, reversible). Applier owns its own tx (dry-run truly rolls back).
- db.py `_pane_context_has_pane_k_at()` feature-detects the column and DEGRADES to `NULL::int AS pane_k_age_s`
  (= pre-057 behavior, every reading LIVE) if absent — a migration hiccup/rollback can never dark the feed. Row
  shape is identical either way (key always present). Same fail-safe pattern as the existing `_pane_context_has_pct`.

## Version sync + provenance-drift
- fc-v58 synced across `sw.js` VERSION, `fleet.js` APP_BUILD, `lanes.html` badge (gate version-sync step green;
  the older `fc-v5x` strings in fleet.js are non-authoritative history/comments, not the APP_BUILD marker).
- The commit folds the previously-UNCOMMITTED item-4a static (op#31750 `_sre_disposition` badges + CSS) — which
  matches the already-committed + cc-quality-PASSED app.py 992c807 — into durable history, clearing LIVE-but-
  uncommitted drift (a net deploy-provenance improvement).

## Non-blocking observations (not deploy-blockers)
1. The irsyad view (app.py:1560) intentionally stays `allow_stale=False` (fails to a `—` on no-signal) — so irsyad
   worker cards are NOT never-blank. This is SAFE (live-only never lies); flagging only in case the never-blank
   intent is meant fleet-wide (then that view would want the same treatment). Scope/completeness, not correctness.
2. Cosmetic: multiple historical `fc-v5x` literals remain in fleet.js; harmless (the gate parses the authoritative
   APP_BUILD marker, which is fc-v58).

**PASS — cleared for the console deploy.** Header/card separation is sound from both server and client; never-blank
is honest and bounded; upsert freeze-vs-bump matches the shipped SQL; mig-057 is additive + fail-safe; version triple
synced; tests green at HEAD (run independently).
