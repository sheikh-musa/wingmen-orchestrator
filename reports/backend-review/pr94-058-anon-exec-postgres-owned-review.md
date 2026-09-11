# PR#94 / migration 058 (postgres-owned anon-exec close) — REVIEW: PASS

**Auditor:** cc-quality (Opus 4.8, CAI-1170 money/PII carve-out). **Date:** 2026-09-06.
**Target:** PR#94, head `9929fb51e65838174164f3df400165feac556f46`, base `fable/substrate-safe-fixes`,
file `migrations/058_close_anon_exec_postgres_owned_half.sql` (reviewed sha256 **5c66cc915427b062c00fdf22047627787b7522109b6d5eb44ee746a867d069f7**).
**Context:** the F2 fix for my CAI-RESP-1396 / decision_audit #320 audit (postgres-owned half). My PASS is the last gate before apply. **VERDICT: PASS — apply approved for this exact sha.**

## What it does (read at the committed sha, not the description)
- `DROP FUNCTION public.fetch_and_execute_sql(text)` — body confirmed at source: `SELECT status,content FROM http_get(url); … EXECUTE sql_content` = an SSRF + arbitrary-SQL-execution primitive, anon+authenticated-EXECUTE, postgres-owned.
- `DROP FUNCTION public.load_all_morphology_batches(text,integer,integer)` — postgres-owned; internally calls the above (dead once it's gone). Upgraded REVOKE→DROP by console (correct — see F2 below).
- `ALTER … SET search_path = public` on 8 SECDEF postgres-owned functions (auth_user_org_ids family, auth_user_hr_employee_id, handle_new_user, sync_org_memberships_to_jwt, get_decision, get_repo_context).

## Ask 1 — F2 closure (assert efficacy): CONFIRMED
- **Machinery verified at the callee, not trusted from a comment** (`scripts/apply_migration.py`): `run_assertions` runs AFTER the DDL body, INSIDE the same transaction, and any failure → `Refuse` → `conn.rollback()` (lines 293-299). `dropped` → `to_regprocedure(fn) IS NULL`; `search_path` → `proconfig` has a `search_path=` entry; `no_execute` → `has_function_privilege=false`. A REVOKE/DROP-FUNCTION body with zero asserts hard-refuses (CAI-RESP-1397 #5). Fail-closed by construction.
- The 058 file's 10 assert headers use the correct kinds (2×`dropped`, 8×`search_path`) and correspond 1:1 to its 10 statements. `dropped` (function gone) is strictly stronger than `no_execute`.
- **I ran the dry-run myself** against the exact committed file: `dry_run_ok`, all 10 assertions PASS, exit 0; post-run live state confirms rollback (both functions still present, `auth_user_org_ids.proconfig` still NULL). The drops actually take effect as postgres (postgres owns them — unlike the supabase_admin-owned surface), so the assertions are meaningful, not vacuous.
- **PUBLIC-in-the-REVOKE catch is sound:** console's first dry-run found `load_all_morphology_batches` carried a separate PUBLIC EXECUTE grant, so a REVOKE of anon/authenticated only would have left it executable via PUBLIC (the exact CAI-RESP-1397 #5 defect) — caught before ship. DROP removes the whole grant surface, so there's no partial-grant left to get wrong. Correct resolution.

## Ask 2 — F1 / honest-partial-close: CONFIRMED
The header explicitly scopes to postgres-owned only, names the supabase_admin-owned residual (http/dblink/net), states a postgres REVOKE against it is the F2 silent no-op, marks it tracked separately (console credential-path / PostgREST-de-exposure = the 058b track), and warns "Do not read a clean apply of 058 as 'the anon-exec gap is closed.'" It never claims to close the class → does NOT trip my "closes 1/N of its class" objection. The F1 boundary claim ("only other unpinned SECDEF in public are the 2 supabase_admin-owned `dblink_connect_u`") matches my own independent enumeration exactly; the 8-pin set is COMPLETE, not sampled (re-confirmed against my #320 source query).

## Ask 3 — frontend / PostgREST caller check (the part only I could do): ZERO callers
- All `~/wingmen` repos (ts/tsx/js/jsx/py/sql/sh/json/mjs, excl vendor/vcs/reports/logs/backups): **0** references to `fetch_and_execute_sql` or `load_all_morphology_batches`.
- `cron.job` on the substrate: **0** jobs referencing either fn (or "morphology").
- In-DB references: **0** other `pg_proc` bodies reference either name; **0** view dependencies.
So no RPC caller, no scheduled caller, no in-DB caller breaks. Both drops are safe. (Limit honestly noted in the migration and re-noted here: `track_functions='none'` so there is no call-history evidence — the architectural argument, SSRF-execute primitive + one-time ingest tooling with zero callers found anywhere, carries it.)

## Minor notes (non-blocking)
- `search_path = public` (not `pg_catalog, public` or `''`) is adequate: pg_catalog is implicitly searched first when unlisted, so the ambient-authority hijack path is removed; it also matches this repo's existing pinned-SECDEF convention. The `search_path` assert is a presence-check (doesn't validate the value) but I read the literal SQL — value is correct.
- `DROP FUNCTION` without `IF EXISTS` is fail-safe here (both functions verified present; a missing one would ERROR→rollback, never a silent pass).

## Bottom line
PASS. Apply `058` via `apply_migration.py` for sha256 `5c66cc9154…` (the tool ledgers the sha and refuses on drift, so any change to the file after this review is caught). The supabase_admin-owned residual (058b) remains open and tracked; my standing gate — a fail-closed post-apply `has_function_privilege` proof (CAI-RESP-1397 #5) — applies to 058b when it lands. `bulk_insert_morphology` correctly deferred to 059.
