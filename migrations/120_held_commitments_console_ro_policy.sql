-- 120_held_commitments_console_ro_policy.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- RLS companion to migration 119 (gate 77077) for the "nothing goes dark"
-- status view (bus 77019, Phase 1 — console-revamp lane).
--
-- WHY: 119's GRANT SELECT landed cleanly but exposed ZERO rows —
-- held_commitments has RLS enabled and NO policies, and a no-policy RLS
-- table is deny-all for every role that is neither owner nor BYPASSRLS
-- (console_readonly is neither). Wet-proved live 2026-10-26 right after the
-- 119 apply: the console role read 0 of 106 open commitments while the same
-- SQL via the owner DSN read all of them.
--
-- WHAT: the fleet-standard companion every other console-readable RLS table
-- already carries (agent_messages/operator_asks: <table>_console_ro;
-- body_activity_verdict 041, front_desk_latency 103): a SELECT-only, FOR
-- console_readonly, USING (true) policy. Same shape, one table.
--
-- Applied via scripts/apply_migration.py (direct psycopg, same-transaction
-- ledger write) — NEVER `supabase db push` (decision 962).

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE POLICY held_commitments_console_ro
  ON public.held_commitments
  FOR SELECT TO console_readonly
  USING (true);

COMMIT;
