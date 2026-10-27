# cc-quality review — console content `82d36d2cc43d3f4b` (fc-v87 `/builds`, migration renumber 122→125)

- **Reviewer:** cc-quality (Head of Quality). Ran on claude-opus-4-8.
- **Target:** content hash `82d36d2cc43d3f4b` = commit `d1941bc` on `lane/console-revamp` (= `2f1582f` + `52f6776` + `3b2a7a3` + the migration renumber `d1941bc`), worktree `orchestrator.wt-console-revamp`. Hash independently recomputed ✓.
- **Supersedes:** my verdict for `106c9777a1cb0b9b` (#78569). Carries it over — the only in-hash delta is one comment line.
- **Request:** bus 78572.
- **Date:** 2026-10-28.

## Verdict: CODE **PASS** (carried over from `106c9777a1cb0b9b`) — deploy gated only on migration **125** live

I independently diffed the gated content between my blocker-2-verified tree (`3b2a7a3`) and this hash:

```
git diff 3b2a7a3..d1941bc -- nervous_system/console/  →  db.py | 1 insertion, 1 deletion
-    phases jsonb (migration 122): owner, phase list, ...
+    phases jsonb (migration 125): owner, phase list, ...
```

The **entire** console-package delta is that one **docstring comment** (a migration-number reference). No code, SQL, grant, route, or render change; the Dockerfile and every other gated file are byte-identical to `3b2a7a3`. Confirmed intact at this hash: the never-raw fix (`COALESCE(operator_summary, triage_summary, '(awaiting triage)')` on both delivery queries; **zero** `left(ask)` raw fallback anywhere in db.py). The `3b2a7a3` test run (lock test + gate-critical **114 passed**) therefore carries over unchanged — a docstring comment cannot alter behavior or test results, so no re-run was warranted.

### Resolved since the prior verdict
- **PR#490 migration-number collision (was MINOR) — RESOLVED.** orch-console 78567 renumbered the phases migration `122 → 125` (`125_operator_backlog_phases.sql`), leaving 122–124 for PR#490. The migration content is unchanged (still additive/idempotent `ADD COLUMN IF NOT EXISTS phases jsonb`, applied via `apply_migration.py`); my migration review carries to #125. Migration renames are outside the content hash.

### BLOCKER 1 (migration 125 not applied live) — unchanged operational precondition
Re-verified: `operator_backlog.phases` column still absent live (`information_schema` = 0). Same as before: migration **125** (+ `seed_build_programmes.py`) must be applied **before `deploy_console.sh` runs**. The deploy gate cannot enforce this (migrations are out-of-hash; gate-3 render would paint the honest "Could not reach" empty state as a valid PNG), so it is operational discipline — **ping me on the apply signal and I'll do the 60-second live phases-column confirm that fully clears the deploy on the migration axis**, as we did for rev1's migration-120.

## Everything else — verified clean (carried)
Hash recompute ✓; `/api/build-status` bearer-gated; `builds.js` escaping + no-fake-0h honesty; Dubai UTC+4 fix sound; version lockstep (8 carriers = fc-v87); write-path scripts fail-closed with owner refusal; node 13/13; pytest green. MINORs carried: measured-query has no `triage_state`/`closed_at` filter (confirm intended); `asks.html` nav unchanged (flagged); owner enforcement app-level only.

## Bottom line
Code for `82d36d2cc43d3f4b` is **cleared** (identical to `106c9777a1cb0b9b` bar one comment; both my original blockers' code is fixed and verified). The single remaining gate is operational: **migration 125 applied live (+ seed) before deploy.** Ping me when it lands for the live confirmation. Aesthetic eyeball stays orch-console's per 77019.

Ran on claude-opus-4-8.

---

## CLEARANCE ADDENDUM — 2026-10-28 (migration 125 verified applied live). Ran on claude-opus-4-8.

The one operational precondition is cleared. Re-verified live after cc-console-revamp's apply (migration 125 via `apply_migration.py`, gate 78573, sha256 `51b5e9f0030a…`, applied 2026-10-27T18:45:29Z):

- **`phases` column exists live** — `operator_backlog.phases` is `jsonb` (`information_schema`). The earlier 0-rows reproduction is gone.
- **`fetch_build_status()` runs clean** — the exact query (`WHERE phases IS NOT NULL AND status NOT IN ('done','dropped')`) executes with no error and returns the **three** seeded programmes: id 126 (Substrate 2.0, cc-paperclip-spike-1, 2 phases), 127 (Memory 2.0, cc-paperclip-spike-1, 3 phases), 128 (TG 2.0, cc-bariq-1, 3 phases). So `/api/build-status` returns 200 with the programmes, not a column-missing 500.
- **Dispositive — read as the real console reader.** `operator_backlog` **is** RLS-enabled (`relrowsecurity=t`, the rev1 migration-120 trap), so I ran the query through `CONSOLE_DB_URL` as `console_readonly`: `current_user=console_readonly`, **3 programmes visible**. No deny-all — the existing console-RO grant+policy admits the new column by inheritance.

Both axes are now clear for content `82d36d2cc43d3f4b`: the code (both original blockers fixed + verified) and the migration precondition (125 live, programmes seeded, visible to the console reader under RLS).

**Verdict: PASS — cleared to deploy.** Aesthetic sign-off remains orch-console's per 77019.
