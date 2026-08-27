# cc-quality review — console fc-v55 (Your-Asks live board + lane dedup)

**Verdict: ✅ PASS.** The new write path (`/api/ask-close` + the in-tx ask-link) is auth-gated, validated, atomic, and read-only-console-safe; the live-derive status SQL is correct and structurally non-stale; the lane dedup is display-only and doesn't over-collapse distinct live instances; migration 044 is applied with correct grants (independently verified); 155 tests green; version synced. A few minor, non-blocking observations. Clear to ship.

- **Reviewer:** cc-quality (Head of Quality) — content-hash GATE-4 (P1).
- **Request:** bus id 22003 (orch-console, op#12457).
- **Content hash:** `36a2c32c0e6fbc30` (fc-v55). Commit `922ff63` (parent 7378631 = fc-v54).
- **diff SHA-256:** `c73c3cb3d4aee07ae12788e705782e9f327f3819b88a9b8d86835f7f4bf1c8e7`
- **Reviewed (UTC):** 2026-08-15T09:44:43Z
- **Method:** verify-not-assert — read mig 044 + db.py/app.py/console_assign.py/asks_close.py, traced the tx + auth + SQL derivation, ran the suite, **queried the DB directly** for the table + grants + index, and eyeballed the render.

---

## Focus points

**(a) Live-derive status SQL — correct + structurally non-stale ✅**
`build_asks_query`: a `latest` CTE picks each thread's newest `agent_messages` row (`DISTINCT ON (thread_id) ORDER BY id DESC`, is_test excluded), then a CASE derives status LIVE: `on_nazim` (thread_id NULL) / `needs_you` (latest is a lane→orch-console `requires_response` + `responded_at IS NULL`) / `delegate_done` (`responded_at` set) / `in_progress` (`read_at` set) / `pending` (else). **Status is never stored** in `operator_asks` — derived every poll — so it structurally cannot go stale (the exact operator_backlog bug it replaces). Ordered needs_you-first, LIMIT 100, read-only SELECT. `agent_messages.thread_id` is indexed (`idx_agent_messages_thread`), so the CTE is index-backed.

**(b) Assign tx atomicity ✅**
`console_assign.py` now inserts the directive (with a fresh `gen_random_uuid()` thread_id) AND the `operator_asks` link (same thread_id) in ONE implicit transaction, committing only after both. psycopg3's `with conn` rolls back on any exception, so if `operator_asks` is absent the link INSERT raises → **the whole tx rolls back → no orphan directive bus row**. All bound-param; the `SELECT 1 FROM agents` existence guard is retained (unknown agent → exit 2 → 400).

**(c) /api/ask-close auth + input validation ✅**
Added to the POST allowlist and behind the same `if not self._authed(): 401` gate as every mutation. Validates **before** spawning: `isinstance(item_id, int)` AND `action in ('confirm','drop')` → 400 otherwise. Shells out `subprocess.run([interp, asks_close.py, str(id), action], timeout=30)` — a LIST (no shell). `asks_close.py` re-validates (`int()` + action allowlist), uses a bound param, and is **idempotent** (`WHERE closed_at IS NULL` — a double-tap can't reopen/rewrite), with a rowcount check (exactly-one → ok, else exit 2). Same vetted-script, read-only-console pattern as /api/assign.

**(d) Lane dedup does not over-collapse ✅ (bounded, display-only)**
`_dedupe_lanes_by_family` groups by the `lane` label (falling back to session/base/agent_id/`id(l)` so a label-less row is never over-collapsed). **Keeps ALL rows with a live local pane** — so distinct *live* instances are never merged; only no-live-pane phantom twins in a live group are dropped, and a fully-dark group collapses to ONE card (host-match then freshest heartbeat). Display-only — never mutates `agent_status` (SRE owns reaping the real ghost). A kept running row inherits its LIVE activity over a stale stored string.

**(e) UI + version ✅**
GATE-1 in sync (sw.js=fleet.js=lanes.html=fc-v55, irsyad too). Render (fleet.png): fc-v55, honest bloat header + pace/runway carry through, 15 dedup'd lane cards (one per lane), the YOUR-ASKS board as a primary surface with "+ ask", drain board demoted. Phone-first, no h-overflow.

## Independent verification
- `pytest tests/console/` → **155 passed** (≥ the claimed 76 subset; 0 fail).
- DB (mig 044 applied): `operator_asks` table **exists**; grants exactly right — **console_readonly = SELECT only**, service_role = full, anon/authenticated revoked. The read-only-console invariant holds for the new table.

## Observations (all non-blocking)
1. **Status derivation assumes the standard drain flow** — a lane marks "done" by stamping `responded_at` on the `requires_response` directive-thread (which the directive body explicitly instructs), giving `delegate_done`. A lane that instead posts a *separate, non-responded* "done" message that becomes the thread's latest would read as `in_progress`/`pending`. That's contrary to the directive's own instruction and to normal bus-drain behavior, so it's an edge, not a defect — worth a note only.
2. **Perf-watch:** the `latest` CTE computes per-thread newest across `agent_messages` each poll. Index-backed + LIMIT 100 + few open asks + the `_FLEET_BUDGET_MS` guard make this fine now; keep an eye on it as thread volume grows.
3. **Dedup residual:** two *distinct offline* instances that happened to share a `lane` label would collapse to one card. Protected for live instances (keep-all-live) + the `id(l)` fallback + display-only. Depends on the convention that distinct instances carry distinct lane labels — worth confirming that holds fleet-wide, but it can never mutate real state.
4. **Render coverage:** the board's populated status badges (needs_you/delegate_done/…) aren't visually exercised (no open asks in the render); they're code- and test-verified instead.
5. **Carried minor:** /api/ask-close (like /api/assign) returns `stderr[-160:]` on a 500 — authed-only, no secret; optional to sanitize.

## Bottom line
A well-built, security-clean change. The new write endpoint follows the vetted read-only-console → service-role-script pattern with proper auth + validation + idempotency; the assign write is genuinely atomic (no orphan on a missing table); the "Your asks" status is derived live in SQL so it cannot go stale (the whole point); and the lane dedup fixes the phantom-dark twin without ever touching real state. Migration applied, grants correct, tests green, render clean. **PASS — ship it.**

— cc-quality
