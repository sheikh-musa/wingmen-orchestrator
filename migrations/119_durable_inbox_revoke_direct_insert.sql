-- 119_durable_inbox_revoke_direct_insert.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: no_table_privilege service_role public.agent_messages INSERT
-- assert: no_sequence_privilege service_role public.agent_messages_id_seq USAGE
--
-- Durable acked inbox, final step (backlog#124, Tier-1(b) janky-tools audit op#33149).
-- THE breaking change: makes migration 118's additive RPC the ONLY write path by
-- revoking direct INSERT (+ the id sequence) from service_role. Split out of 118
-- itself (orch-console ruling, bus #74885) because 118 physically bundling this
-- revoke with its own safe-additive half meant the only thing stopping an accidental
-- apply was a comment, not structure -- fragile against a routine apply-latest or the
-- DDL coverage watchdog. 118 is additive-only and safe to apply any time once merged;
-- THIS file is the one that breaks every writer still doing a raw
-- `INSERT INTO agent_messages` outside the sanctioned RPC path.
--
-- RELEASE GATE (orch-console owns this, not a promise in a comment): this migration
-- is released ONLY when the enforce-in-code grep gate (backlog#124 ruling part 4)
-- reports zero raw-INSERT bypasses fleet-wide, across the ~50 scripts/daemons found
-- independently writing to agent_messages outside bus_send.py (bus #74880/#74885).
-- Do not apply this ahead of that gate going green, no matter how long 118 has been
-- live without incident -- "nothing broke yet" is not the gate.
--
-- Self-defeat check (NOT re-litigated here, already proven by migration 118's own
-- design + TDD suite, bus #74738/#74741/#74860/#74863): the three posting RPCs are
-- owned by the dedicated bus_writer role, distinct from service_role, so this revoke
-- cannot kill their own internal writes the way the original, rejected 118 draft did.

REVOKE INSERT ON agent_messages FROM service_role;
REVOKE USAGE ON SEQUENCE agent_messages_id_seq FROM service_role;
