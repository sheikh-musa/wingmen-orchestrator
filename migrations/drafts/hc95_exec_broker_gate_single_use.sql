-- DRAFT -- hc#95 exec-broker B3 single-use gates (cutover spec bus #89814 §4.2-4.3).
-- PROPOSE-ONLY: orch-console applies via direct psycopg after gating (never `supabase db push`).
-- NOT applied. Migration NNN assigned by CORE at apply.
-- CONTRACT AMENDMENT: supersedes the v1 "no exec_ledger_id replay check" omission
-- (scripts/lib/exec_broker/protocol.py) -- console must confirm before this lands.
--
-- WHAT: bind each gate_id to ONE exec_ledger_id. The first execution whose gate
-- fully validates sets gate_consumed = true on ITS OWN exec_broker_access_log row
-- (scripts/lib/exec_broker/vault_log.py consume_gate), in the same transaction
-- that commits before the mgmt-API call. The partial UNIQUE index below makes a
-- second consumer of the same gate_id -- another exec_ledger_id, a retry, or a
-- CONCURRENT duplicate -- fail with unique_violation (23505), which the broker
-- maps to GATE_INVALID. A concurrent duplicate blocks on the index until the
-- first transaction commits (then fails) or rolls back (then may proceed) --
-- there is no check-then-write window to race.
--
-- DEPLOY ORDER: apply hc95_exec_broker_gate_ref.sql, then THIS, BEFORE deploying
-- the B3 broker code. If the code lands first, consume_gate's UPDATE fails on the
-- missing column -> INTERNAL_ERROR for every gated call, before any mgmt-API
-- call (fail closed, but an outage for gated calls until this is applied).
--
-- BACKFILL: none needed. Rows written before this have gate_consumed = false;
-- the post-deploy replay window that would leave is CLOSED in code by the
-- cutover floor (console #89841 ruling e): EXEC_BROKER_GATE_NOT_BEFORE, set to
-- the deploy instant, refuses every gate created before it (reason
-- gate_before_cutover). Marking historical rows consumed is not possible in
-- general (several historical 'ok' rows may share a gate_id, which the unique
-- index forbids).
--
-- INTEGRITY: the table-wide UPDATE grant (KNOWN LIMITATION in
-- hc95_exec_broker_access_log.sql) would let exec_broker_role un-consume a gate.
-- The trigger below forbids gate_consumed true -> false and any gate_id change on
-- a consumed row, so un-consuming needs DDL, not a broker bug.

ALTER TABLE exec_broker_access_log
  ADD COLUMN IF NOT EXISTS gate_consumed boolean NOT NULL DEFAULT false;

-- Machine-readable refusal reason (console #89841 ruling a): distinguishes e.g.
-- a replay (gate_already_consumed) from a malformed gate, both GATE_INVALID.
-- Written by vault_log.log_finalized only when a reason exists.
ALTER TABLE exec_broker_access_log
  ADD COLUMN IF NOT EXISTS error_reason text;

DO $$ BEGIN
  ALTER TABLE exec_broker_access_log
    ADD CONSTRAINT exec_broker_access_log_consumed_needs_gate
    CHECK (NOT gate_consumed OR gate_id IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS exec_broker_access_log_gate_single_use
  ON exec_broker_access_log (gate_id) WHERE gate_consumed;

CREATE OR REPLACE FUNCTION exec_broker_access_log_gate_consumed_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF OLD.gate_consumed AND NOT NEW.gate_consumed THEN
    RAISE EXCEPTION 'exec_broker_access_log: a consumed gate cannot be un-consumed (row %)', OLD.id;
  END IF;
  IF OLD.gate_consumed AND NEW.gate_id IS DISTINCT FROM OLD.gate_id THEN
    RAISE EXCEPTION 'exec_broker_access_log: gate_id of a consumed row is immutable (row %)', OLD.id;
  END IF;
  RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION exec_broker_access_log_gate_consumed_guard() FROM PUBLIC, authenticated, anon, service_role;

DROP TRIGGER IF EXISTS exec_broker_access_log_gate_consumed_guard ON exec_broker_access_log;
CREATE TRIGGER exec_broker_access_log_gate_consumed_guard
  BEFORE UPDATE ON exec_broker_access_log
  FOR EACH ROW EXECUTE FUNCTION exec_broker_access_log_gate_consumed_guard();
