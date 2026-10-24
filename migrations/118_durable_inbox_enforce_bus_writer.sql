-- 118_durable_inbox_enforce_bus_writer.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: no_table_privilege service_role public.agent_messages INSERT
-- assert: no_sequence_privilege service_role public.agent_messages_id_seq USAGE
--
-- Durable acked inbox, step 2/2 (backlog#124, Tier-1(b) janky-tools audit op#33149;
-- design: bus #74850/#74858/#74860, orch-console approved). Makes the additive step
-- 1/2 (migration 117) the ONLY write path: direct INSERT on agent_messages is revoked
-- from service_role, so every writer must call post_agent_message.
--
-- HARD CONSTRAINT paid for by 118's own rejected first draft (bus #74738/#74741): a
-- SECURITY DEFINER RPC owned by the role being revoked dies with the revoke. Here the
-- owner is a DISTINCT, dedicated NOLOGIN role (bus_writer) -- never service_role --
-- so the revoke cannot self-defeat the RPCs.
--
-- Grant footprint beyond the obvious INSERT, each one caught by TDD against an
-- isolated local Postgres (none of these would have been obvious from reasoning about
-- the grant model alone):
--   * SELECT (id, thread_id) ON agent_messages -- post_agent_message's
--     `RETURNING agent_messages.id, agent_messages.thread_id` requires SELECT on the
--     returned columns, NOT just INSERT on the table -- a real Postgres RETURNING
--     rule, not something either review pass caught; only a real INSERT-then-fetch
--     test run as the actual role surfaces it (has_table_privilege(..., 'INSERT')
--     alone reports true and is NOT sufficient proof).
--   * SELECT, INSERT, UPDATE ON work_item + INSERT ON work_item_audit -- the
--     AFTER-trigger work_item_mirror_bus_trg (and work_item_mirror() itself) are
--     SECURITY INVOKER, not DEFINER, so they execute AS bus_writer once the posting
--     RPCs are owned by it; service_role has these only via Supabase's blanket
--     default-ACL (migrations 039/049), which does not extend to a brand-new role
--     (bus #74860/#74863 design review finding).
--   * UPDATE (delivered_at, lease_expires_at, acked_at) ON agent_messages -- a direct
--     consequence of putting ALL THREE RPCs under bus_writer (bus #74863 answer to
--     Q1, "consistency over minimalism"): mark_delivered/ack_message UPDATE those
--     columns, so their new owner needs UPDATE on them too, not just the INSERT this
--     migration's headline is about. Caught by TDD (test_mark_delivered_and_ack_
--     unaffected_by_enforcement), not by reasoning about the ownership decision alone.
--
-- NOT APPLIED to the substrate until the fleet-wide bus_send.py cutover (backlog#124
-- step 2 of 4: RPC-signature audit done, bus_send.py cutover, fleet-wide deploy+verify,
-- THEN this revoke) -- orch-console's coordination gate, not this migration's.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bus_writer') THEN
        CREATE ROLE bus_writer NOLOGIN;
    END IF;
END $$;

GRANT INSERT ON agent_messages TO bus_writer;
GRANT SELECT (id, thread_id) ON agent_messages TO bus_writer;
GRANT UPDATE (delivered_at, lease_expires_at, acked_at) ON agent_messages TO bus_writer;
GRANT USAGE ON SEQUENCE agent_messages_id_seq TO bus_writer;
GRANT SELECT, INSERT, UPDATE ON work_item TO bus_writer;
GRANT INSERT ON work_item_audit TO bus_writer;

ALTER FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text) OWNER TO bus_writer;
ALTER FUNCTION mark_delivered(bigint,integer) OWNER TO bus_writer;
ALTER FUNCTION ack_message(bigint) OWNER TO bus_writer;

REVOKE INSERT ON agent_messages FROM service_role;
REVOKE USAGE ON SEQUENCE agent_messages_id_seq FROM service_role;
