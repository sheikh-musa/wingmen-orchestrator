-- ARCH-035 hotfix (CAI-RESP-046): two disclosed deviations from the base migration.
--
-- Parent: ARCH-035 / decision_ref=CAI-RESP-046.
-- Applies on top of 20260419_arch035_three_channel_taxonomy.sql.
--
-- Deviation 1 — CHECK constraint: preserve pre-existing `counter` value.
--   CAI-RESP-042 Q1 filed a 7-value ruling without first auditing pg_constraint,
--   which already allowed 8 values (the 7 + `counter`). Removing governance
--   vocabulary is asymmetric cost; `counter` may represent a legitimate
--   counter-proposal primitive distinct from `challenge`. Preserved here.
--
-- Deviation 2 — identity-violation logger: dblink_exec → RAISE NOTICE.
--   Applying the base migration via Supabase MCP threw
--   "2F003: password or GSSAPI delegated credentials required" on the first
--   dblink_exec call — MCP runs without postgres-superuser peer auth, so
--   dblink_exec cannot open a loopback connection. The spec comment's
--   "on Supabase managed postgres this is unneeded" assumption was wrong in
--   MCP context. Switched to RAISE NOTICE: violations land in Postgres server
--   logs (forensic trail preserved), but the queryable
--   `agent_status_identity_violations` table remains empty. Revisit in
--   BUG-024 Phase 1 when per-agent JWT identity arrives.

-- Deviation 1: widen CHECK to 8 values (add `counter`).
ALTER TABLE agent_messages
  DROP CONSTRAINT agent_messages_message_type_check;
ALTER TABLE agent_messages
  ADD CONSTRAINT agent_messages_message_type_check
  CHECK (message_type IN ('review_request','question','decision','agreed','challenge','update','blocker','counter'));

-- Deviation 2: replace dblink-based autonomous-tx logger with RAISE NOTICE.
--   Function body no longer references dblink_exec, so identity-trigger path
--   is dblink-free. `agent_status_identity_violations` table is retained for
--   future reuse but will not receive rows under this function.
CREATE OR REPLACE FUNCTION log_agent_status_identity_violation(
  p_claimed TEXT, p_guc TEXT, p_type TEXT, p_op TEXT
) RETURNS VOID AS $$
BEGIN
  RAISE NOTICE 'agent_status identity violation: claimed=% guc=% type=% op=%',
               p_claimed, p_guc, p_type, p_op;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
