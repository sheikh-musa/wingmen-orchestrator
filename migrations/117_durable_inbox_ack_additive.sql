-- 117_durable_inbox_ack_additive.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: no_execute public public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text)
-- assert: no_execute public public.mark_delivered(bigint,integer)
-- assert: no_execute public public.ack_message(bigint)
-- assert: search_path public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text)
-- assert: search_path public.mark_delivered(bigint,integer)
-- assert: search_path public.ack_message(bigint)
--
-- Durable acked inbox, step 1/2 (Tier-1(b) janky-tools audit, op#33149; design:
-- reports/durable-inbox-design-2026-10-24/DESIGN.md in the paperclip-spike repo, gated
-- by orch-console bus #74716). Conversational-traffic slice only -- task-shaped traffic
-- for already-migrated lanes routes through Paperclip's existing issue/run dispatch, no
-- schema change needed for that path.
--
-- PURELY ADDITIVE, on purpose: every current writer (bus_send.py and every lane) still
-- INSERTs directly into agent_messages today. This migration adds a parallel posting
-- RPC without disturbing that path at all -- revoking direct INSERT is a SEPARATE,
-- LATER migration (118), applied only after every writer has actually cut over
-- (console's catch, bus #74716: bundling both in one migration would have broken the
-- entire fleet's bus the instant it applied).
--
-- Shape: three new timestamp columns track the ack lifecycle (delivered_at/acked_at/
-- lease_expires_at); post_agent_message / mark_delivered / ack_message become available
-- as an additional write path, callable only by service_role (every current writer
-- already connects via the service DSN, which bypasses RLS the same way migration 065's
-- header documents for vault_secrets -- new functions default-grant EXECUTE to PUBLIC in
-- Postgres, so that default is revoked here rather than left open to anon/authenticated,
-- same class of risk CAI-RESP-1397 names for table privileges). Each SECURITY DEFINER
-- function pins `search_path = public` (review catch: an unpinned search_path on a
-- definer function is the classic lookup-path escalation vector -- same pattern as
-- migrations 009/037/038/063).
--
-- priority already has a DB-level CHECK on the live substrate today (identical name and
-- values) -- this migration's own guarded ADD CONSTRAINT is therefore a safe no-op there,
-- kept only so this migration is self-contained/idempotent on a fresh schema too (review
-- correction: an earlier draft of this header wrongly implied no DB-level constraint
-- existed yet).
--
-- TDD'd against an isolated local Postgres, never this substrate -- see
-- cc-paperclip-spike's durable_inbox/ build (paperclip-spike repo, branch
-- worktree-durable-inbox-build) for the full test suite this was developed against.

ALTER TABLE agent_messages
    ADD COLUMN IF NOT EXISTS delivered_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS acked_at         TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

COMMENT ON COLUMN agent_messages.delivered_at IS
    'Set by mark_delivered() when a delivery attempt (today: the send-keys nudge; going '
    'forward: whatever actually requests the ack) fires. NULL means never attempted.';
COMMENT ON COLUMN agent_messages.acked_at IS
    'Set by ack_message() when the recipient actually acts on the row. Additive to '
    'read_at/responded_at, not a replacement of their existing semantics.';
COMMENT ON COLUMN agent_messages.lease_expires_at IS
    'Detection deadline for the reconciler, not a correctness deadline for the message -- '
    'an ack arriving after this still counts as delivered successfully (see '
    'reconciler.classify() in the paperclip-spike build).';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agent_messages_priority_check'
    ) THEN
        ALTER TABLE agent_messages
            ADD CONSTRAINT agent_messages_priority_check
            CHECK (priority IN ('P0', 'P1', 'P2', 'P3'));
    END IF;
END $$;

CREATE OR REPLACE FUNCTION post_agent_message(
    p_from_agent        TEXT,
    p_to_agent          TEXT,
    p_message_type      TEXT,
    p_subject           TEXT,
    p_body              TEXT,
    p_priority          TEXT,
    p_requires_response BOOLEAN,
    p_thread_id         UUID,
    p_sub_tag           TEXT
) RETURNS TABLE(id BIGINT, thread_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
BEGIN
    RETURN QUERY
    INSERT INTO agent_messages
        (from_agent, to_agent, message_type, subject, body,
         priority, requires_response, thread_id, sub_tag)
    VALUES
        (p_from_agent, p_to_agent, p_message_type, p_subject, p_body,
         p_priority, p_requires_response, p_thread_id, p_sub_tag)
    RETURNING agent_messages.id, agent_messages.thread_id;
END;
$fn$;

CREATE OR REPLACE FUNCTION mark_delivered(
    p_id BIGINT,
    p_lease_seconds INTEGER
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
BEGIN
    UPDATE agent_messages
    SET delivered_at = now(),
        lease_expires_at = now() + make_interval(secs => p_lease_seconds)
    WHERE id = p_id;
END;
$fn$;

CREATE OR REPLACE FUNCTION ack_message(
    p_id BIGINT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
BEGIN
    UPDATE agent_messages
    SET acked_at = now()
    WHERE id = p_id;
END;
$fn$;

COMMENT ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text) IS
    'Durable acked inbox, additive step (migration 117). Available alongside direct '
    'INSERT -- not yet the only write path. Migration 118 (the revoke that would make '
    'this the only path) is REJECTED as written and out of this PR pending a distinct-'
    'owner rework -- see its header.';

REVOKE EXECUTE ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION mark_delivered(bigint,integer) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION ack_message(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION post_agent_message(text,text,text,text,text,text,boolean,uuid,text) TO service_role;
GRANT EXECUTE ON FUNCTION mark_delivered(bigint,integer) TO service_role;
GRANT EXECUTE ON FUNCTION ack_message(bigint) TO service_role;
