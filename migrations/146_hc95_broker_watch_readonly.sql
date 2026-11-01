-- 146_hc95_broker_watch_readonly.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- hc#95 broker_watch_readonly: SELECT-only role for the broker watchdog / reconcile (console ruling bus #89906).
-- Apply via scripts/apply_migration.py with a --gate decision row citing this
-- file's sha12 (direct psycopg, same-transaction ledger write) -- NEVER
-- `supabase db push` (decision 962). hc#95 broker apply order: 142 -> 143 ->
-- 144 -> 145 -> 146; this is 5 of 5 (LAST).
-- No REVOKE here; the asserts below verify the role cannot WRITE any of the four
-- tables it reads (SELECT-only by construction, checked post-apply).
-- assert: no_table_privilege broker_watch_readonly public.exec_broker_heartbeat INSERT
-- assert: no_table_privilege broker_watch_readonly public.exec_broker_heartbeat UPDATE
-- assert: no_table_privilege broker_watch_readonly public.exec_broker_heartbeat DELETE
-- assert: no_table_privilege broker_watch_readonly public.exec_broker_access_log INSERT
-- assert: no_table_privilege broker_watch_readonly public.exec_broker_access_log UPDATE
-- assert: no_table_privilege broker_watch_readonly public.exec_broker_access_log DELETE
-- assert: no_table_privilege broker_watch_readonly public.exec_prod_ledger INSERT
-- assert: no_table_privilege broker_watch_readonly public.exec_prod_ledger UPDATE
-- assert: no_table_privilege broker_watch_readonly public.exec_prod_ledger DELETE
-- assert: no_table_privilege broker_watch_readonly public.exec_prod_ledger_shipper_runs INSERT
-- assert: no_table_privilege broker_watch_readonly public.exec_prod_ledger_shipper_runs UPDATE
-- assert: no_table_privilege broker_watch_readonly public.exec_prod_ledger_shipper_runs DELETE
--
-- WHY (console ruling bus #89906): cc-fleet-health's broker watchdog (hc#1001)
-- and the B4 daily reconcile need to READ four tables, and nothing else:
--   exec_broker_heartbeat          (dead-man heartbeat + listener self-probe)
--   exec_broker_access_log         (the broker's own audit row per call)
--   exec_prod_ledger               (the direct-path exec_prod ledger, shipped)
--   exec_prod_ledger_shipper_runs  (the shipper's own run heartbeat, bus #89909)
-- This creates a dedicated SELECT-only role for that, in the console_readonly
-- house style (migrations/004_console_readonly.sql: LOGIN, NOINHERIT, no
-- superuser/createdb/createrole; plus NOBYPASSRLS) -- never the broker's or the
-- fleet's write roles.
--
-- APPLY ORDER: LAST -- after
--   1. migrations/142_hc95_exec_broker_gate_ref.sql
--   2. migrations/143_hc95_exec_broker_gate_single_use.sql
--   3. migrations/144_hc95_exec_broker_heartbeat.sql
--   4. migrations/145_exec_prod_ledger.sql
-- This file needs all four tables to exist (the GRANTs fail otherwise, and
-- the whole transaction rolls back); 145_exec_prod_ledger.sql creates the last two.
-- Apply via scripts/apply_migration.py on substrate tscuymavysscrvoberrr, with a
-- decision gate row citing this file + its sha12 -- NEVER `supabase db push`
-- (decision 962).
--
-- CREDENTIAL: NO password in this file. The role is created LOGIN with no
-- password; orch-console provisions the credential out of band
-- (ALTER ROLE broker_watch_readonly PASSWORD ...) and hands it to
-- cc-fleet-health -- never committed.
--
-- RLS, per table (a GRANT without a policy on an RLS-enabled table is
-- deny-all, the 119 -> 120 lesson):
--   * exec_broker_heartbeat -- RLS ENABLED by its draft -> policy below.
--   * exec_broker_access_log -- RLS OFF (checked live by console) -> no policy
--     needed; the GRANT alone suffices.
--   * exec_prod_ledger, exec_prod_ledger_shipper_runs -- their draft does NOT
--     enable RLS -> no policy needed.
-- If RLS is ever enabled on any of the latter three, add the same
-- FOR SELECT TO broker_watch_readonly USING (true) policy in that change.
--
-- SCOPE: SELECT on exactly those four tables + USAGE on schema public (needed
-- to resolve them). No sequences, no schema-wide table grants, no DML.

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'broker_watch_readonly') THEN
    CREATE ROLE broker_watch_readonly
      WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO broker_watch_readonly;

GRANT SELECT ON public.exec_broker_heartbeat TO broker_watch_readonly;
GRANT SELECT ON public.exec_broker_access_log TO broker_watch_readonly;
GRANT SELECT ON public.exec_prod_ledger TO broker_watch_readonly;
GRANT SELECT ON public.exec_prod_ledger_shipper_runs TO broker_watch_readonly;

DO $$
BEGIN
  CREATE POLICY exec_broker_heartbeat_watch_ro
    ON public.exec_broker_heartbeat
    FOR SELECT TO broker_watch_readonly
    USING (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END
$$;

COMMIT;
