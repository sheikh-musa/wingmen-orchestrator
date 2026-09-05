-- 058_close_anon_exec_postgres_owned_half.sql
-- ledger: silo=tscuymavysscrvoberrr
-- assert: dropped public.fetch_and_execute_sql(text)
-- assert: dropped public.load_all_morphology_batches(text, integer, integer)
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
-- 2. DROP load_all_morphology_batches(text,integer,integer) — a batch
--    data-loader whose body calls fetch_and_execute_sql() internally
--    (verified via pg_get_functiondef) to fetch and run 155 numbered SQL
--    batch files. postgres owns it; DROP works cleanly (a plpgsql body
--    calling another function by name is not a pg_depend dependency, so
--    dropping fetch_and_execute_sql first does not block or complicate
--    this DROP). Zero references anywhere in this repo; one-time
--    Zahidah-morphology bulk-ingest tooling, not an ongoing API surface.
--    orch-console's call (msg 37820), correctly: since step 1 already
--    leaves this function non-functional for EVERY caller (its only
--    actor was that SSRF-execute chain), a REVOKE would leave a dead,
--    still-anon-facing fetch-execute-chain function present as a latent
--    re-exposure vector if EXECUTE were ever mistakenly re-granted — DROP
--    removes the whole chain instead of just gating it. If the morphology
--    dataset ever needs re-ingest, it gets rebuilt safely, not via anon
--    fetch-execute.
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
-- DATA (before this file was ever submitted for review, and before the DROP
-- decision above): the originally-scoped `REVOKE ... FROM anon,
-- authenticated` was ITSELF the F2 silent-no-op — `information_schema.
-- role_routine_grants` showed PUBLIC holds its own separate EXECUTE grant on
-- load_all_morphology_batches (granted by postgres, 5 distinct grantee rows:
-- PUBLIC/postgres/anon/authenticated/service_role), so revoking only anon
-- and authenticated's entries left the function fully executable via
-- PUBLIC — exactly the class of defect CAI-RESP-1397 #5 exists to catch,
-- caught on a real migration before it shipped, not after. (orch-console's
-- review note: the DROP above sidesteps this class of gotcha entirely —
-- there's no partial-grant surface left to get wrong once the function
-- itself is gone.)
--
-- CALLER-SAFETY CONFIRMATION (per hard requirement #2):
--   - service_role and postgres each hold their OWN separate EXECUTE grant
--     on both dropped functions (confirmed via information_schema.
--     role_routine_grants: 5 distinct grantee rows, not one shared
--     PUBLIC-derived entry) — irrelevant post-DROP (the grant rows go with
--     the function), noted for the historical record of what was checked.
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
-- bulk_insert_morphology(jsonb) has the identical PUBLIC+anon+authenticated
-- EXECUTE exposure but is SECURITY INVOKER (not SECDEF) and doesn't call
-- fetch_and_execute_sql — orch-console's ruling (msg 37820): out of 058's
-- SECDEF/anon-exec-primitive scope, routed to migration 059 (the
-- INVOKER-anon-EXECUTE default-DML sweep, per cc-quality #320/#321). Not
-- touched here.

DROP FUNCTION public.fetch_and_execute_sql(text);

DROP FUNCTION public.load_all_morphology_batches(text, integer, integer);

ALTER FUNCTION public.auth_user_org_ids() SET search_path = public;
ALTER FUNCTION public.auth_user_org_ids_with_role(text) SET search_path = public;
ALTER FUNCTION public.auth_user_org_ids_with_roles(text[]) SET search_path = public;
ALTER FUNCTION public.auth_user_hr_employee_id() SET search_path = public;
ALTER FUNCTION public.handle_new_user() SET search_path = public;
ALTER FUNCTION public.sync_org_memberships_to_jwt() SET search_path = public;
ALTER FUNCTION public.get_decision(text) SET search_path = public;
ALTER FUNCTION public.get_repo_context(text) SET search_path = public;
