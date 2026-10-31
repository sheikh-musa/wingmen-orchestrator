-- 138_host_metrics_console_ro_policy.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- RLS companion to migration 137 for the machines-resources panel (op#36664,
-- console-revamp lane).
--
-- WHY: 137's GRANT SELECT alone would expose ZERO rows — host_metrics
-- (migration 096) has RLS enabled with only a service_role policy
-- (host_metrics_service_only), and a role with no matching policy is
-- deny-all for everyone except owner/BYPASSRLS (console_readonly is
-- neither). This is the exact 119/120 trap (held_commitments) repeating —
-- not skipping it this time.
--
-- WHAT: the fleet-standard companion every other console-readable RLS table
-- already carries (held_commitments_console_ro 120, body_activity_verdict
-- 041, front_desk_latency 103): a SELECT-only, FOR console_readonly,
-- USING (true) policy. Same shape, one table.
--
-- Applied via scripts/apply_migration.py (direct psycopg, same-transaction
-- ledger write) — NEVER `supabase db push` (decision 962).

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE POLICY host_metrics_console_ro
  ON public.host_metrics
  FOR SELECT TO console_readonly
  USING (true);

COMMIT;
