-- §6.6 tracked capture of the auditor_ro read-only auditor role + its grants.
-- Refs: CAI-RESP-1225 (auditor_ro cutover), CAI-RESP-1231 pt4 (§6.6 grant), CAI-RESP-1324/1326/1327.
-- Owner: orch-console. Apply target: BOTH client silos (goumlyne + ceayj), via the console
--        direct-psycopg §6.6 pattern (NEVER `supabase db push`). This is console INFRA, not an
--        app-feature migration — hence it lives in the orchestrator repo, not the app migrations/.
--
-- PURPOSE: auditor_ro already exists LIVE on both silos but was created AD-HOC (CAI-1224 fork-1) and
--          never captured in a tracked migration. This file makes that state reproducible (fresh silo /
--          DR rebuild) and closes the "untracked grant" gap. On a silo where auditor_ro already exists
--          it is a NO-OP (the DO-guard skips CREATE; GRANT of an existing membership is idempotent).
--
-- CAPTURED LIVE ATTRS (identical on goumlyne + ceayj, verified at source 2026-08-25):
--   LOGIN, INHERIT, NOSUPERUSER, NOCREATEROLE, NOCREATEDB, NOREPLICATION, BYPASSRLS, CONNECTION LIMIT -1,
--   no VALID UNTIL, MEMBER OF pg_read_all_data, ZERO explicit per-table grants (all reads flow through
--   pg_read_all_data).
--
-- SECURITY NOTE (for the grant review): auditor_ro has BYPASSRLS=true + pg_read_all_data — it reads ALL
--   rows on the silo regardless of RLS (this is intentional: a FULL-tier auditor verifies ground truth,
--   not the RLS-filtered view). It has NO write/DDL capability. This migration preserves exactly that.
--
-- PASSWORD: intentionally NOT set here (it is a genuine secret, managed out-of-band; it is what the
--   RO DSN — GOUMLYNE_RO_DATABASE_URL / IHSANOS_PROD_RO_DATABASE_URL — authenticates with). On the live
--   silos the role already has its password and the DO-guard leaves it untouched. On a FRESH silo, after
--   this migration runs, set the password separately (ALTER ROLE auditor_ro PASSWORD '<secret>') as a
--   secure step — do NOT commit a password to this file.
--
-- §6.6 apply note: this file is applied by the console apply-script with its own outer transaction; it
--   deliberately contains NO BEGIN/COMMIT of its own (CAI-756-safe — the applier wraps it, the wet-prove
--   wraps it in BEGIN..ROLLBACK).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auditor_ro') THEN
    CREATE ROLE auditor_ro
      LOGIN
      INHERIT
      NOSUPERUSER
      NOCREATEROLE
      NOCREATEDB
      NOREPLICATION
      BYPASSRLS
      CONNECTION LIMIT -1;
  END IF;
END
$$;

-- Read-only capability: SELECT on all tables + USAGE on schemas, via the built-in predefined role.
-- Idempotent (granting an already-held membership is a no-op).
GRANT pg_read_all_data TO auditor_ro;
