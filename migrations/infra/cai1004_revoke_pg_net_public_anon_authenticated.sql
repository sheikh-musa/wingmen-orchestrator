-- cai1004_revoke_pg_net_public_anon_authenticated.sql
-- PROPOSE-ONLY. Authored by cc-fleet-health (SRE) for CAI-RESP-1004 / CAI-RESP-1003 / CAI-RESP-1334.
-- DO NOT run this from any fleet DB role. See "EXECUTION PATH" below — the fleet's postgres role
-- (and the Supabase Management API /database/query endpoint, which ALSO runs as postgres) CANNOT
-- execute these REVOKEs; they will SILENTLY NO-OP with a WARNING. Requires supabase_admin.
--
-- TARGET: the SUBSTRATE project ONLY (tscuymavysscrvoberrr.supabase.co) — NOT a client silo.
-- SEVERITY: MEDIUM defense-in-depth (de-escalated from P1 in CAI-RESP-1004). NOT externally
--   PostgREST-reachable (net not in db-schemas allow-list; PGRST106, role-independent — re-verified
--   by cc-fleet-health 2026-08-26: service-key + anon-independent config evidence, controls passed).
--
-- ============================================================================================
-- WHY (verified at source via pg_class.relacl / pg_namespace.nspacl, aclexplode — NOT info_schema,
-- which hides PUBLIC grants and misled an earlier pass):
--   net.http_request_queue / net._http_response : relacl has `=arwdDxtm/supabase_admin`
--       => PUBLIC holds FULL DML (INSERT/SELECT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER/MAINTAIN).
--   net.http_request_queue_id_seq (sequence)    : relacl has `=rwU/supabase_admin`
--       => PUBLIC holds SELECT/UPDATE/USAGE on the sequence.
--   SCHEMA net nspacl: `=U/...` (PUBLIC USAGE) *plus* explicit anon=U, authenticated=U.
--       => anon/authenticated hold net USAGE both DIRECTLY and via PUBLIC.
--   RLS is OFF on both tables. Row counts 0/0 (no evidence of use).
--   A role with net USAGE can queue arbitrary outbound HTTP via pg_net and read net._http_response
--   (other callers' response bodies, incl. any webhook secret). These are pg_net's inherited
--   Supabase extension defaults, not a grant anyone here wrote.
--
-- CORRECTION #1 to the CAI-1003/1004 fix wording (verified): the original spec
--   "REVOKE USAGE ON SCHEMA net FROM anon, authenticated" is INSUFFICIENT ALONE — because SCHEMA net
--   USAGE is also granted to PUBLIC, anon/authenticated re-inherit USAGE from PUBLIC after the
--   per-role revoke. The revoke MUST include FROM PUBLIC. (Same for the table DML: it is a PUBLIC
--   grant, so FROM PUBLIC is what actually removes it.) Statements below are corrected accordingly.
-- ============================================================================================

-- ---- THE FIX (run as supabase_admin only) ----

-- 1) Tables: remove PUBLIC's inherited full DML (anon/authenticated hold table DML only via PUBLIC).
REVOKE ALL ON TABLE net.http_request_queue FROM PUBLIC;
REVOKE ALL ON TABLE net._http_response      FROM PUBLIC;

-- 2) Sequence: remove PUBLIC's SELECT/UPDATE/USAGE.
REVOKE ALL ON SEQUENCE net.http_request_queue_id_seq FROM PUBLIC;

-- 3) Schema USAGE: remove from PUBLIC (root grant) AND the explicit anon/authenticated grants.
--    LEAVE intact the trusted explicit holders that legitimately use pg_net:
--    supabase_admin (owner), postgres, service_role, supabase_functions_admin (edge functions).
REVOKE USAGE ON SCHEMA net FROM PUBLIC;
REVOKE USAGE ON SCHEMA net FROM anon;
REVOKE USAGE ON SCHEMA net FROM authenticated;

-- ---- POST-CONDITION VERIFY (dead-man's-switch: a REVOKE by an unprivileged role SILENTLY no-ops.
--      Run these IN THE SAME supabase_admin session immediately after; ABORT/re-check if any FAIL) ----
-- EXPECT: anon=f, authenticated=f, PUBLIC=f ; service_role=t, postgres=t (trusted set retained).
--   SELECT 'anon'         AS who, has_schema_privilege('anon','net','USAGE')          AS net_usage   -- expect f
--   UNION ALL SELECT 'authenticated', has_schema_privilege('authenticated','net','USAGE')            -- expect f
--   UNION ALL SELECT 'service_role',  has_schema_privilege('service_role','net','USAGE')             -- expect t
--   UNION ALL SELECT 'postgres',      has_schema_privilege('postgres','net','USAGE');                -- expect t
-- EXPECT: no `=...` (PUBLIC) entry remains in either relacl or nspacl.
--   SELECT relname, relacl FROM pg_class
--     WHERE oid IN ('net.http_request_queue'::regclass,'net._http_response'::regclass,
--                   'net.http_request_queue_id_seq'::regclass);
--   SELECT nspacl FROM pg_namespace WHERE nspname='net';

-- ============================================================================================
-- EXECUTION PATH (CORRECTION #2 — verified by cc-fleet-health 2026-08-26, contradicts the
-- "SUPABASE_ACCESS_TOKEN → Management-API execution" premise for THIS revoke):
--   net.* and schema net are owned by supabase_admin. A REVOKE requires the object owner, a member
--   of the owning role, or a superuser.
--   - Every fleet DB DSN connects as `postgres`: rolsuper=false, NOT a member of supabase_admin.
--   - The Supabase Management API POST /v1/projects/{ref}/database/query endpoint ALSO runs as
--     `postgres` (verified: current_user=postgres, rolsuper=false, pg_has_role(_,supabase_admin,
--     USAGE)=false). It executes SQL, but as this SAME unprivileged role.
--   => Neither psql NOR the Management API query endpoint can run these REVOKEs. They no-op with
--      WARNING "no privileges could be revoked for net" and change nothing (cc-quality reproduced
--      this in CAI-1003 via direct postgres, in a guarded txn that caught the no-op and rolled back).
--   => The real path is a supabase_admin-level platform action: Supabase Support / security-advisor
--      remediation (operator-reachable), as CAI-1003 originally concluded ("no agent-reachable").
--      The Dashboard SQL editor also runs as postgres and will NOT work.
--   ACTION: do NOT "execute via API and assume done" — that manufactures false-fix confidence on a
--   swallowed no-op. Confirm the post-conditions above actually flipped before closing CAI-1003/1004.
--
-- DURABILITY (verified): event trigger `issue_pg_net_access` (ddl_command_end, owner supabase_admin,
--   fn extensions.grant_pg_net_access) is GATED on DDL that touches the pg_net extension itself
--   (JOIN pg_extension WHERE extname='pg_net') — so this revoke IS durable against ordinary
--   migrations (confirms CAI-1004). BUT that trigger re-runs
--     GRANT USAGE ON SCHEMA net TO supabase_functions_admin, postgres, anon, authenticated, service_role;
--   so a pg_net EXTENSION upgrade (ALTER EXTENSION pg_net UPDATE / Supabase platform bump) will
--   RE-GRANT schema USAGE to anon+authenticated. The fix must be RE-APPLIED after any pg_net upgrade.
--   (It does not re-grant to PUBLIC, so the PUBLIC revokes are durable even across a pg_net upgrade.)
-- ============================================================================================
