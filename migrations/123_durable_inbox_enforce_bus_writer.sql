-- 123_durable_inbox_enforce_bus_writer.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- No -- assert: line needed -- this file contains no REVOKE/DROP FUNCTION (that
-- convention trigger) now that the revoke is split out to migration 124.
--
-- RENUMBERED THREE TIMES (PR#490 CI findings, backlog#124): first 118->119, then
-- (orch-console bus #76518: base branch separately merged its own, unrelated
-- migrations/118_operator_asks_needs_musa.sql -- a logical number collision)
-- 119->120, then (orch-console bus #78234: base branch independently took
-- 119/120/121 for 3 unrelated migrations) 120->123. See
-- 122_post_agent_message_optional_params.sql's header -- this file now applies
-- AFTER that one (filename order), which is what its own content always
-- required; only the number changed.
--
-- Durable acked inbox, additive half of the final step (backlog#124, Tier-1(b) janky-
-- tools audit op#33149; design: bus #74850/#74858/#74860, orch-console approved).
-- PURELY ADDITIVE, on purpose (orch-console ruling, bus #74885): creates the dedicated
-- bus_writer role and moves the three posting RPCs onto it, but does NOT revoke
-- service_role's direct INSERT -- that is migration 124, a separate file, gated on
-- the fleet-wide enforce-in-code check reporting zero bypasses (bus #74880/#74885).
-- This migration's own prior draft bundled the revoke in THIS file; split out after
-- review found that bundling meant the only thing stopping an accidental apply of the
-- breaking half was a comment, not structure -- a routine apply-latest or the DDL
-- coverage watchdog could fire it. Splitting makes this migration safe to apply any
-- time once merged: service_role keeps direct INSERT until 124 lands.
--
-- HARD CONSTRAINT paid for by an earlier, fully-rejected draft (bus #74738/#74741):
-- a SECURITY DEFINER RPC owned by the role being revoked dies with the revoke. Here the
-- owner is a DISTINCT, dedicated NOLOGIN role (bus_writer) -- never service_role --
-- so migration 124's later revoke cannot self-defeat the RPCs.
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
-- Safe to merge and apply as soon as this is green -- it changes nothing about who can
-- already write to agent_messages, only adds a second, narrower-privileged path.
--
-- SEQUENCING (migration 122, backlog#124, bus #74939): post_agent_message's
-- signature below targets the 11-param version migration 122 creates, not the
-- original 9-param one from 117. Filename order (117, 122, 123, 124) now IS
-- dependency order -- 122 applies before this file, so the 9-param signature is
-- already gone by the time this ALTER FUNCTION line runs. If this migration is
-- ever applied BEFORE 122 for any reason, this line will fail to resolve (the
-- 9-param function still exists then) -- that failure is a correct, loud signal
-- to apply 122 first, not a bug to silently work around.

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

ALTER FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text,boolean,text) OWNER TO bus_writer;
ALTER FUNCTION mark_delivered(bigint,integer) OWNER TO bus_writer;
ALTER FUNCTION ack_message(bigint) OWNER TO bus_writer;
