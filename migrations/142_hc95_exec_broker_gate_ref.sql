-- 142_hc95_exec_broker_gate_ref.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- hc#95 exec_broker_access_log: gate_ref + executed_prologue/executed_sql_sha256 (bus #89814 §3.5; amended ruling C bus #89846).
-- Apply via scripts/apply_migration.py with a --gate decision row citing this
-- file's sha12 (direct psycopg, same-transaction ledger write) -- NEVER
-- `supabase db push` (decision 962). hc#95 broker apply order: 142 -> 143 ->
-- 144 -> 145 -> 146; this is 1 of 5.
-- PREREQUISITE: exec_broker_access_log exists (live on the substrate; created
-- by migrations/drafts/hc95_exec_broker_access_log.sql, outside the numbered chain).
-- No REVOKE / DROP FUNCTION in this file -> no `-- assert:` lines are required.
--
-- WHY: a ref-only gate (op#N/hc#N/q#N/cp#N, no migration/PR) carries no
-- schema_migrations stamp, so the broker's own audit row must record which ref
-- the approval was for, or the approval is not auditable after the fact.
--
-- DEPLOY ORDER: apply THIS before any caller sends the optional `ref` wire key.
-- scripts/lib/exec_broker/vault_log.py only references gate_ref when the
-- request carries a ref, so every existing (ref-less) call keeps the exact
-- pre-amendment INSERT and is unaffected whether or not this is applied. A
-- ref-carrying call made BEFORE this is applied fails at the INSERT ->
-- INTERNAL_ERROR, before validation or any mgmt-API call (fail closed:
-- nothing executes).
--
-- Additive, nullable, no default, no rewrite: existing rows read gate_ref = NULL.
-- Inherits the table-level grants (exec_broker_role INSERT/UPDATE/SELECT; no
-- DELETE to anyone) -- no grant change needed.

ALTER TABLE exec_broker_access_log ADD COLUMN IF NOT EXISTS gate_ref text;

-- Amended ruling C (console bus #89846): every gate-free plain read is executed
-- as 'SET TRANSACTION READ ONLY; ' || <received bytes>. sql_sha256 stays the hash
-- of the RECEIVED bytes (caller binding, unchanged); these record what actually
-- RAN, so the audit never implies the broker executed bytes it did not. Both
-- are NULL when the received bytes ran unchanged (every gated statement).
-- DEPLOY ORDER: these columns are written for EVERY gate-free read, so apply
-- this file before deploying #821 -- otherwise every read fails at that UPDATE
-- (INTERNAL_ERROR, before execution: fail closed, but an outage for reads).
ALTER TABLE exec_broker_access_log ADD COLUMN IF NOT EXISTS executed_prologue text;
ALTER TABLE exec_broker_access_log ADD COLUMN IF NOT EXISTS executed_sql_sha256 text;

COMMENT ON COLUMN exec_broker_access_log.gate_ref IS
  'hc#95 B2/--ref: the gate ref anchor (op#N/hc#N/q#N/cp#N) the caller sent, as received (validated by require_gate after this row is written). NULL when no ref was sent.';
