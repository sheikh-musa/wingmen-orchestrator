-- 058_close_anon_exec_postgres_owned_half.sql
-- ledger: silo=tscuymavysscrvoberrr
-- assert: dropped public.fetch_and_execute_sql(text)
-- assert: no_execute anon public.load_all_morphology_batches(text, integer, integer)
-- assert: no_execute authenticated public.load_all_morphology_batches(text, integer, integer)
-- assert: search_path public.auth_user_org_ids()
-- assert: search_path public.auth_user_org_ids_with_role(text)
-- assert: search_path public.auth_user_org_ids_with_roles(text[])
-- assert: search_path public.auth_user_hr_employee_id()
-- assert: search_path public.handle_new_user()
-- assert: search_path public.sync_org_memberships_to_jwt()
-- assert: search_path public.get_decision(text)
-- assert: search_path public.get_repo_context(text)
--
-- Closes the POSTGRES-OWNED half of the anon-exec security gap flagged in the
-- 07-01 ultracode audit and re-confirmed in the 2026-09-05 substrate audit
-- (§3-H: "fetch_and_execute_sql(url) still anon-callable 66 days after being
-- flagged"). Musa GO 2026-09-05 (op_msg 19181, "clear what we can"); cai
-- direction CAI-RESP-1396. Applied via scripts/apply_migration.py 058
-- --silo tscuymavysscrvoberrr (direct psycopg — NEVER `supabase db push`,
-- decision 962).
--
-- HONEST PARTIAL CLOSE — READ THIS BEFORE ASSUMING "anon-exec is closed":
-- This migration closes ONLY the functions owned by `postgres`. The
-- http_get/http_post/dblink_connect_u surface (owned by `supabase_admin`) is
-- KNOWINGLY OUT OF SCOPE here — a `postgres`-run REVOKE against a
-- supabase_admin-owned object is a silent no-op in Supabase (postgres is not
-- a true superuser relative to supabase_admin there), which is the exact F2
-- defect this migration's own assertions exist to catch. Verified at source
-- (2026-09-05): the only OTHER unpinned SECDEF functions in public, besides
-- the 8 below, are `dblink_connect_u(text)` and `dblink_connect_u(text,text)`,
-- both owned by supabase_admin — untouched here. That residual is tracked
-- separately by orch-console via a credential-path / PostgREST-de-exposure
-- effort. Do not read a clean apply of 058 as "the anon-exec gap is closed."
--
-- WHAT THIS MIGRATION DOES, AND WHY EACH IS SAFE TO DO:
--
-- 1. DROP fetch_and_execute_sql(text) — an anon+authenticated-EXECUTABLE
--    function whose entire body is `SELECT ... FROM http_get(url); ...;
--    EXECUTE sql_content` — a remote-fetch-then-EXECUTE-arbitrary-SQL
--    primitive (SSRF + full anon-triggerable SQL injection). No tracked
--    migration ever created it (verified: zero hits for its name across
--    migrations/*.sql). Zero legitimate use case exists for ANY caller
--    (verified: zero references anywhere in this repo; the function's own
--    behavior — fetch a URL, execute its bytes as SQL — is not a shape any
--    legitimate anon/authenticated/service_role caller needs). DROP, not
--    just REVOKE, so it cannot be re-exposed by a future GRANT mistake.
--
-- 2. REVOKE EXECUTE on load_all_morphology_batches(text,integer,integer)
--    FROM PUBLIC, anon, authenticated — a batch data-loader whose body calls
--    fetch_and_execute_sql() internally (verified via pg_get_functiondef)
--    to fetch and run 155 numbered SQL batch files. Zero references in this
--    repo; this is one-time Zahidah-morphology bulk-ingest tooling, not an
--    ongoing API surface — nothing anon/authenticated legitimately needs
--    from it. NOTE (surfaced, not silently fixed here): after step 1 drops
--    fetch_and_execute_sql, THIS function becomes non-functional for EVERY
--    caller, including service_role — its only historical actor was that
--    function's SSRF-execute chain. Left as REVOKE-not-DROP per the exact
--    instruction that scoped this migration; flagged to orch-console
--    separately as a DROP candidate.
--
-- 3. ALTER ... SET search_path = public on 8 SECURITY DEFINER functions
--    that run as `postgres` regardless of caller, with no search_path
--    pinned — a classic SECDEF search_path hijack surface (an attacker who
--    can create/replace an unqualified-name object earlier in an unpinned
--    search_path can have their code run with the function owner's
--    privileges). Verified COMPLETE (not sampled): a direct query for every
--    prosecdef=true, owner=postgres, public-schema function with no
--    'search_path=' entry in proconfig returns exactly these 8 rows, no
--    more, no fewer. `public` (not `pg_catalog, public`) matches this
--    repo's existing pinned-SECDEF convention (10/10 already-pinned
--    functions checked use `search_path=public` or `search_path=public,
--    pg_temp`). Pinning changes no behavior for a correctly-written
--    function — it only removes the ambient-authority hijack path.
--
-- CAUGHT BY THE TOOL'S OWN ASSERTION, ON THE FIRST --dry-run AGAINST REAL
-- DATA: a REVOKE naming only `anon, authenticated` (the literal scope as
-- first given) is ITSELF the F2 silent-no-op — `information_schema.
-- role_routine_grants` shows PUBLIC holds its own separate EXECUTE grant on
-- load_all_morphology_batches (granted by postgres), so revoking anon and
-- authenticated's entries leaves the function fully executable via PUBLIC.
-- Fixed by adding PUBLIC to the REVOKE list before this file was submitted
-- for review — exactly the class of defect CAI-RESP-1397 #5 exists to catch,
-- caught on a REAL migration before it shipped, not after.
--
-- CALLER-SAFETY CONFIRMATION (per hard requirement #2):
--   - service_role and postgres each hold their OWN separate EXECUTE grant
--     (confirmed via information_schema.role_routine_grants: 5 distinct
--     grantee rows, not one shared PUBLIC-derived entry) — revoking
--     PUBLIC/anon/authenticated's entries does not touch theirs.
--   - Zero references to fetch_and_execute_sql or load_all_morphology_batches
--     anywhere in this repo (grep, excl .venv/reports/logs).
--   - track_functions is 'none' on this instance, so pg_stat_user_functions
--     has no call-history evidence either way — noted as a real limit of
--     this confirmation, not glossed over. The architectural argument
--     (SSRF-execute primitive; one-time batch loader) stands independent of
--     call-history, which this instance simply does not record.
--   - This orchestrator checkout cannot see whether a separate frontend/
--     PostgREST-facing repo calls these RPC names directly; that check is
--     outside what this checkout can verify and is called out here rather
--     than assumed.
--
-- ANOTHER FINDING, NOT ACTED ON HERE: bulk_insert_morphology(jsonb) has the
-- identical anon+authenticated EXECUTE exposure as load_all_morphology_batches
-- (owner postgres, not SECDEF, not called out in the original scope) but does
-- NOT call fetch_and_execute_sql — it inserts directly into word_morphology
-- from jsonb. Left untouched: not in the scoped list, and changing it wasn't
-- asked for. Flagged to orch-console for a decision, not included here.

DROP FUNCTION public.fetch_and_execute_sql(text);

REVOKE EXECUTE ON FUNCTION public.load_all_morphology_batches(text, integer, integer)
  FROM PUBLIC, anon, authenticated;

ALTER FUNCTION public.auth_user_org_ids() SET search_path = public;
ALTER FUNCTION public.auth_user_org_ids_with_role(text) SET search_path = public;
ALTER FUNCTION public.auth_user_org_ids_with_roles(text[]) SET search_path = public;
ALTER FUNCTION public.auth_user_hr_employee_id() SET search_path = public;
ALTER FUNCTION public.handle_new_user() SET search_path = public;
ALTER FUNCTION public.sync_org_memberships_to_jwt() SET search_path = public;
ALTER FUNCTION public.get_decision(text) SET search_path = public;
ALTER FUNCTION public.get_repo_context(text) SET search_path = public;
