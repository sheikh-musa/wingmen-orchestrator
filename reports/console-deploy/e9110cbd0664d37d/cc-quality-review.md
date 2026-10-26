# cc-quality review — console content `e9110cbd0664d37d` (fc-v85, /status board)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** content hash `e9110cbd0664d37d` = commit `bf4eddb` on `lane/console-revamp` (feature `6a8e459` + pre-ship tokenless fix `c5fc42a`). Reviewed in worktree `orchestrator.wt-console-revamp`.
- **Scope:** engineering correctness, security/token-gating, derivation correctness, NO-DROP/deployment completeness. **Not** the aesthetic eyeball (that is orch-console's per bus 77019; the GLM-routed lane did not read the PNGs).
- **Request:** bus 77050 (supersedes 77048).
- **Date:** 2026-10-26.

## Verdict: CODE **PASS** — DEPLOY **BLOCKED** on two verified preconditions

The code at the hashed target is correct and safe. It must NOT be deployed until the two blockers below are cleared, or the live board silently drops its entire commitments half (including every breach) — the exact failure the board exists to prevent.

---

## What I verified (evidence)

**Endpoint auth — PASS.** `/api/status-board` sits below the `_authed()` bearer gate (app.py:2125 → 401 tokenless); only the static SPA shell `/status`, `/static/*` and the PWA files (`manifest.json`, `sw.js`) are pre-auth, all non-sensitive — same tier as `/asks`. The data endpoint is bearer-gated, header-only, never URL.

**Tokenless auto-refresh fix (`c5fc42a`) — PASS.** `load()` now early-returns `if (!token) return;` before the loading paint + fetch, so the 60s auto-refresh cannot overwrite the enter-token prompt with a 401 paint. Locked by `tests/console/status_board.test.js` ("401 + tokenless flows": no fetch fires tokenless, prompt stays, timer armed, manual interval tick still fetches nothing). **Ran it: green (exit 0).**

**Derivation (db.py) — PASS.** `fired`-is-always-dark bypasses `derive_overdue` (a fresh touch never un-darks a breach); cadences operator 24h / client 12h in one place (`STATUS_CADENCE_S`); `past_due` wins over quiet; missing last-update = quiet (darkest); never-raw-captured-text honoured on the asks side via `COALESCE(operator_summary, triage_summary, '(awaiting triage)')`; commitments correctly exclude `discharged_at`/`cancelled_at`. Locked by `tests/console/test_status_board.py`. **Ran it: 11 passed.**

(I did not re-run the full 109-pytest + `test_app.py` suite — it needs a local/ephemeral Postgres and the prod guard correctly refuses a prod-DSN run. I ran the two gate-critical suites above; the full-suite-green claim is consistent but not independently re-verified here.)

---

## BLOCKER 1 — migration 120 is NOT applied on the live substrate → commitments half silently blank

Verified live (prod substrate, read-only, counts/catalog only — no PII rows):

- `has_table_privilege('console_readonly','public.held_commitments','SELECT')` = **t** → migration **119 (GRANT) is applied**.
- `held_commitments`: `relrowsecurity = t` (RLS enabled), **zero rows in `pg_policies`** → migration **120 (RLS policy) is NOT applied**.
- `console_readonly`: `rolsuper = f`, `rolbypassrls = f`.

RLS-enabled + no policy + non-owner/non-bypass role = **deny-all**. The console connects as `console_readonly` via `CONSOLE_DB_URL` (db.py — a SELECT-only role; the very existence of migration 119 proves it is not the owner). Therefore **right now the live board reads 0 of ~110 open commitments.** This is exactly the state migration 120's own commit message wet-proved ("read 0 of 106").

Migration **120** (`dba0ba4`, `120_held_commitments_console_ro_policy.sql`) is the fix, but it is **committed AFTER the hashed review target** (`bf4eddb`) and was not part of this review request. Both migrations must be gated + applied via `apply_migration.py` (same path/gate as 119) **BEFORE** the console deploy.

## BLOCKER 2 — render evidence does not represent the deployed board

The frozen `_status-payload.json` behind the PNGs + DOM assertion contains **471 items = 106 commitments + 365 asks, 359 overdue, 6 fired** — the full board (owner/both-migrations view). But the live deploy state (119 only, no 120) yields **asks-only + 0 commitments**. All 106 commitments **and all 6 fired breach pills** (fired is commitment-only) would be absent from the real board. So `status-desktop.png` / `status-mobile.png` and the "471 items / 359 dark / 6 breach pills" counts are **not what operators will see on deploy** unless 120 is applied first. Regenerate/validate the render against a DB with BOTH migrations applied before sign-off.

---

## MEDIUM (latent, not blocking at today's volume)

**`LIMIT 600` truncates by the wrong key.** The union is ordered `last_update_at NULLS FIRST LIMIT 600`, then re-sorted darkest-first in Python. Two gaps: (a) any item beyond 600 is silently dropped from an anti-drop board with no "N more not shown" indicator; (b) a `past_due` item with a recent touch sorts late by `last_update_at` and can be truncated while fresher-but-quiet items survive — i.e. truncation is not darkness-ordered. Live total today = **485** (374 asks + 111 commitments) — under 600 but thin and growing. Recommend: order the SQL by the same darkness rank before the limit, or raise/remove it (bounded domain), and surface an overflow count.

## MINOR (confirm)

- **Commitment title.** Asks apply the never-raw-captured-text `COALESCE`; the commitments side uses `h.title` directly. Confirm `held_commitments.title` is a curated title, not raw captured client text, or it bypasses the board's never-raw rule.
- **`is_test` scope.** Excluded only on the `agent_messages` `latest` CTE, not on `operator_asks` / `held_commitments`. Confirm those tables cannot carry drill/test rows (consistency with `fetch_fleet_asks`), else test items surface on the board.

---

## Bottom line

Clear the code (`e9110cbd0664d37d`) on correctness/security/token-gating/derivation. Hold the deploy until: (1) migration 120 is gated + applied live alongside 119, and (2) the render evidence is regenerated against both-migrations-applied. The MEDIUM `LIMIT` item should be tracked before volume approaches 600. Aesthetic sign-off remains with orch-console.

---

## CLEARANCE ADDENDUM — 2026-10-26 (both blockers verified cleared). Ran on claude-opus-4-8.

Re-verified live after cc-console-revamp's fix (bus 77133 → my 77124). **Both deploy blockers are cleared — the PASS now clears to deploy.**

- **BLOCKER 1 cleared.** `pg_policies` on `held_commitments` now shows `held_commitments_console_ro` — `cmd=SELECT`, `roles={console_readonly}`, `qual=true`, no `with_check`. With the 119 GRANT + this SELECT policy, `console_readonly` reads all open commitments; my earlier 0-of-110 reproduction is gone. Migration 120 gated (77113) + applied/ledgered ~14:27Z.
- **BLOCKER 2 cleared.** Evidence refreshed through the console role against the both-migrations DB and committed at `aa9a363`: payload now **484 items = 110 commitment + 374 ask**, 354 overdue — the commitments half and all breaches are present. Verified `aa9a363` touches **only** `reports/` (payload + both re-rendered PNGs + this file), so the console content hash is byte-identical and the PASS still covers exactly what ships.
- **MINOR 2 resolved.** Confirmed via `information_schema`: neither `operator_asks` nor `held_commitments` has an `is_test` column, so the filter correctly lives only on the `agent_messages` CTE (matches `fetch_fleet_asks`).
- **MINOR 1 accepted.** `held_commitments.title` is agent-authored/curated by construction (committing agent/sweeper writes the title, not raw captured channel text) — low never-raw risk; accepted on that basis.
- **MEDIUM (`LIMIT 600`) — accepted, NOT fixed this cycle, TRACKED.** Non-blocking at live total 484; any `db.py` edit would move the content hash against this PASS (freeze discipline). Must be carried into the next console cycle before volume approaches 600.

**Verdict: PASS — cleared to deploy.** Aesthetic sign-off remains orch-console's.
