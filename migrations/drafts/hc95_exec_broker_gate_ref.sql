-- DRAFT -- hc#95 exec_broker_access_log.gate_ref (cutover spec bus #89814 §3.5, B2/--ref).
-- PROPOSE-ONLY: orch-console applies via direct psycopg after gating (never `supabase db push`).
-- NOT applied. Migration NNN assigned by CORE at apply.
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

COMMENT ON COLUMN exec_broker_access_log.gate_ref IS
  'hc#95 B2/--ref: the gate ref anchor (op#N/hc#N/q#N/cp#N) the caller sent, as received (validated by require_gate after this row is written). NULL when no ref was sent.';
