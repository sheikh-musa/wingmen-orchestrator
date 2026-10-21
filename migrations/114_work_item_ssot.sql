-- 114_work_item_ssot.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- The BIG-3 work_item SSOT (bus #73169/#73176/#73179, Musa op#32730/32732, GREENLIT
-- 2026-10-21/22, orch-console approving gate). operator_asks + agent_messages (the
-- requires_response subset) + held_commitments are "where the actual drops happen" --
-- three separately-shaped tables, three separate sweeps, no single place that answers
-- "what is open right now, who owns it, and is anything stuck." This migration adds
-- ONE typed table (kind IN 'ask'|'bus'|'commitment') under ONE lifecycle, dual-written
-- from the three sources by trigger (never by promise -- "enforce in code, not by
-- promise"), reversible: the three source tables are untouched, nothing is deleted,
-- and every existing sweep keeps running until work_item_reconcile.py (next, dated
-- 2026-10-24 per bus #73176) replaces them and each reader is cut over individually.
--
-- ─────────────────────────────────────────────────────────────────────────────────
-- STATE-DERIVATION TRUTH TABLE (orch-console bus #73179 requirement a) -- every value
-- a source's own CHECK constraint allows is mapped below; nothing falls through
-- unmapped. This comment and the derive functions below are ONE definition, not two:
-- the backfill and the triggers both call the SAME work_item_derive_*() functions, so
-- this table and the executed logic cannot drift apart.
--
-- ASK (operator_asks), keyed on (triage_state, closed_at IS NULL, delegated_to IS NULL)
-- -- triage_state is CHECK-restricted to exactly 'captured'|'ask'|'not_an_ask'|'done':
--   triage_state='not_an_ask'  (any closed_at)                      -> dropped
--   triage_state='done'        (any closed_at)                      -> done
--   triage_state<>'done'       AND closed_at NOT NULL                -> done   (closed pre-triage; rare)
--   triage_state='ask'         closed_at NULL, delegated_to NOT NULL -> owned,    owner=delegated_to
--   triage_state='ask'         closed_at NULL, delegated_to NULL     -> captured (confirmed ask, unassigned)
--   triage_state='captured'    closed_at NULL                        -> captured
--
-- BUS (agent_messages WHERE requires_response), keyed on (responded_at, skipped_at,
-- claimed_by) -- scope is deliberately the requires_response SUBSET, not the whole
-- firehose (orch-console bus #73179 confirmed this scope):
--   responded_at NOT NULL                                  -> done
--   responded_at NULL, skipped_at NOT NULL                 -> dropped
--   responded_at NULL, skipped_at NULL, claimed_by NOT NULL -> owned, owner=claimed_by
--   responded_at NULL, skipped_at NULL, claimed_by NULL     -> captured
--
-- COMMITMENT (held_commitments), keyed on (status, blocked_on IS NULL) -- status is
-- CHECK-restricted to exactly 'pending'|'fired'|'discharged'|'cancelled':
--   status='discharged'                      -> done,        done_by=discharged_by (real, source-enforced NOT NULL)
--   status='cancelled'                       -> dropped,     drop_reason=cancel_reason
--   status='fired'                           -> in_progress  (surfaced; action now expected)
--   status='pending', blocked_on NOT NULL    -> in_progress  (owned but stalled)
--   status='pending', blocked_on NULL        -> owned         (held_commitments.owner_agent is
--                                                               NEVER NULL -- a commitment is
--                                                               always owned from creation, unlike
--                                                               an ask or a bus message)
--
-- FALLBACK ATTRIBUTION CONVENTION: a terminal row (done/dropped) always needs a named actor
-- per the DROP-INVARIANT below. Where the SOURCE row itself names one (discharged_by, the
-- replying agent resolved off response_ref, triaged_by), that name is mirrored verbatim -- a
-- real attribution, carried over. Where the source does not capture one (ask/bus 'dropped'
-- paths, held_commitments' cancellation, which has no cancelled-by column at all), the mirror
-- uses the literal tag 'source:<table>' -- PROVENANCE, not a claim that any agent made the
-- call. work_item_reconcile.py (next) must never treat a 'source:*' actor as a live agent's
-- own discharge.
BEGIN;

CREATE TABLE IF NOT EXISTS work_item (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    kind            text        NOT NULL,
    state           text        NOT NULL DEFAULT 'captured',

    -- Plain text, deliberately not an FK -- same reasoning as held_commitments.owner_agent
    -- (migration 051): the row must outlive a recycled/stood-down owner.
    owner_agent     text,

    title           text        NOT NULL,

    -- The origin row. UNIQUE makes every trigger upsert AND the backfill idempotent by
    -- construction -- re-running either can never create a duplicate work_item.
    source_table    text        NOT NULL,
    source_id       bigint      NOT NULL,
    source_ref      text,

    due_at          timestamptz,

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    done_at         timestamptz,
    done_by         text,

    dropped_at      timestamptz,
    dropped_by      text,
    drop_reason     text,

    CONSTRAINT work_item_source_uk UNIQUE (source_table, source_id),

    CONSTRAINT work_item_kind_check
        CHECK (kind IN ('ask', 'bus', 'commitment')),

    CONSTRAINT work_item_state_check
        CHECK (state IN ('captured', 'owned', 'in_progress', 'done', 'dropped')),

    -- DROP-INVARIANT (held_commitments_discharge_is_attributable / cancel_has_reason
    -- precedent): a terminal state must name itself. No silent drops, no un-attributed done.
    CONSTRAINT work_item_done_is_attributable
        CHECK (state <> 'done' OR (done_by IS NOT NULL AND done_at IS NOT NULL)),

    CONSTRAINT work_item_dropped_is_attributable
        CHECK (state <> 'dropped'
               OR (dropped_by IS NOT NULL AND dropped_at IS NOT NULL AND drop_reason IS NOT NULL)),

    -- Cannot be "owned" or "in_progress" with nobody on the hook.
    CONSTRAINT work_item_owned_needs_owner
        CHECK (state NOT IN ('owned', 'in_progress') OR owner_agent IS NOT NULL)
);

-- The orphan-watchdog's hot query (work_item_reconcile.py, next): what is owned/in_progress
-- and has not moved. Partial index so the table can grow a long done/dropped history without
-- slowing the one read that actually pages on staleness.
CREATE INDEX IF NOT EXISTS work_item_open_activity_idx
    ON work_item (updated_at)
    WHERE state IN ('owned', 'in_progress');

CREATE INDEX IF NOT EXISTS work_item_owner_idx
    ON work_item (owner_agent)
    WHERE state IN ('owned', 'in_progress');

CREATE INDEX IF NOT EXISTS work_item_due_idx
    ON work_item (due_at)
    WHERE state NOT IN ('done', 'dropped');

COMMENT ON TABLE work_item IS
    'BIG-3 SSOT (bus #73169/#73176/#73179): dual-written mirror of operator_asks + the '
    'requires_response subset of agent_messages + held_commitments, under one lifecycle. '
    'The source tables remain the system of record for their own domain -- this table is '
    'the cross-kind read, not a replacement for any one of them.';

COMMENT ON COLUMN work_item.owner_agent IS
    'Plain text, deliberately not an FK -- must survive its owner being recycled or stood '
    'down, same reasoning as held_commitments.owner_agent.';

COMMENT ON COLUMN work_item.done_by IS
    'Real attribution when the source row names one (discharged_by / replying agent / '
    'triaged_by); otherwise the literal provenance tag ''source:<table>'' -- never a '
    'fabricated agent name. See the fallback-attribution convention in this file''s header.';

COMMENT ON COLUMN work_item.dropped_by IS
    'Same fallback-attribution convention as done_by.';

CREATE TABLE IF NOT EXISTS work_item_audit (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_item_id    bigint      NOT NULL REFERENCES work_item (id),
    event           text        NOT NULL,
    actor           text        NOT NULL,
    at              timestamptz NOT NULL DEFAULT now(),
    note            text
);

CREATE INDEX IF NOT EXISTS work_item_audit_item_idx ON work_item_audit (work_item_id, at);

COMMENT ON TABLE work_item_audit IS
    'Append-only. One row per ACTUAL state transition (never per no-op mirror UPDATE -- '
    'see work_item_mirror()). Deliberately NOT hash-chained like audit_log -- that extra '
    'rigor is for grant/governance rows; this is an operational trail, a design choice '
    'stated here, not yet separately ratified.';

-- ── Shared derived-row shape, so the three work_item_derive_*() functions below and the
-- one work_item_mirror() upsert+audit function agree on exactly one record layout. ───────
DO $$ BEGIN
    CREATE TYPE work_item_derived AS (
        state        text,
        owner_agent  text,
        title        text,
        source_ref   text,
        due_at       timestamptz,
        done_at      timestamptz,
        done_by      text,
        dropped_at   timestamptz,
        dropped_by   text,
        drop_reason  text
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── ONE upsert+audit path, called by every trigger AND by the backfill below, so the
-- "audit only on an actual state change" rule (orch-console bus #73179 requirement b)
-- lives in exactly one place. ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION work_item_mirror(
    p_kind text, p_source_table text, p_source_id bigint, d work_item_derived, p_actor text
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    prior_state text;
    v_id        bigint;
BEGIN
    SELECT state INTO prior_state FROM work_item
     WHERE source_table = p_source_table AND source_id = p_source_id;

    INSERT INTO work_item (kind, state, owner_agent, title, source_table, source_id, source_ref,
                            due_at, done_at, done_by, dropped_at, dropped_by, drop_reason, updated_at)
    VALUES (p_kind, d.state, d.owner_agent, d.title, p_source_table, p_source_id, d.source_ref,
            d.due_at, d.done_at, d.done_by, d.dropped_at, d.dropped_by, d.drop_reason, now())
    ON CONFLICT (source_table, source_id) DO UPDATE SET
        state        = EXCLUDED.state,
        owner_agent  = EXCLUDED.owner_agent,
        title        = EXCLUDED.title,
        source_ref   = EXCLUDED.source_ref,
        due_at       = EXCLUDED.due_at,
        done_at      = EXCLUDED.done_at,
        done_by      = EXCLUDED.done_by,
        dropped_at   = EXCLUDED.dropped_at,
        dropped_by   = EXCLUDED.dropped_by,
        drop_reason  = EXCLUDED.drop_reason,
        updated_at   = now()
    RETURNING id INTO v_id;

    -- Audit ONLY on an actual state change -- a brand-new row (prior_state IS NULL) or a
    -- genuine transition. A re-upsert that re-derives the SAME state (the common case on
    -- an unrelated column UPDATE, or a re-run backfill) writes nothing here.
    IF prior_state IS NULL OR prior_state IS DISTINCT FROM d.state THEN
        INSERT INTO work_item_audit (work_item_id, event, actor, note)
        VALUES (v_id, CASE WHEN prior_state IS NULL THEN 'mirrored' ELSE 'state_changed' END,
                p_actor, COALESCE(prior_state, '(new)') || ' -> ' || d.state);
    END IF;
END;
$$;

-- ── ASK ──────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION work_item_derive_ask(
    p_id bigint, p_ask text, p_triage_state text, p_closed_at timestamptz,
    p_delegated_to text, p_committed_date timestamptz, p_triaged_by text
) RETURNS work_item_derived LANGUAGE plpgsql AS $$
DECLARE
    d work_item_derived;
BEGIN
    d.title      := left(p_ask, 200);
    d.source_ref := 'operator_asks id=' || p_id;
    d.due_at     := p_committed_date;

    IF p_triage_state = 'not_an_ask' THEN
        d.state := 'dropped';
        d.dropped_at  := COALESCE(p_closed_at, now());
        d.dropped_by  := COALESCE(p_triaged_by, 'source:operator_asks');
        d.drop_reason := 'not_an_ask (operator_asks triage)';
    ELSIF p_triage_state = 'done' OR p_closed_at IS NOT NULL THEN
        d.state   := 'done';
        d.done_at := COALESCE(p_closed_at, now());
        d.done_by := COALESCE(p_triaged_by, 'source:operator_asks');
        d.owner_agent := p_delegated_to;
    ELSIF p_triage_state = 'ask' AND p_delegated_to IS NOT NULL THEN
        d.state := 'owned';
        d.owner_agent := p_delegated_to;
    ELSE
        d.state := 'captured';
    END IF;

    RETURN d;
END;
$$;

CREATE OR REPLACE FUNCTION work_item_mirror_ask_trg() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM work_item_mirror('ask', 'operator_asks', NEW.id,
        work_item_derive_ask(NEW.id, NEW.ask, NEW.triage_state, NEW.closed_at,
                              NEW.delegated_to, NEW.committed_date, NEW.triaged_by),
        'trigger:operator_asks');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS work_item_mirror_ask_trg ON operator_asks;
CREATE TRIGGER work_item_mirror_ask_trg
    AFTER INSERT OR UPDATE ON operator_asks
    FOR EACH ROW EXECUTE FUNCTION work_item_mirror_ask_trg();

-- ── BUS (requires_response subset only) ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION work_item_derive_bus(
    p_id bigint, p_subject text, p_responded_at timestamptz, p_skipped_at timestamptz,
    p_claimed_by text, p_response_ref text
) RETURNS work_item_derived LANGUAGE plpgsql AS $$
DECLARE
    d work_item_derived;
    v_replying_agent text;
BEGIN
    d.title      := left(p_subject, 200);
    d.source_ref := 'agent_messages id=' || p_id;

    IF p_responded_at IS NOT NULL THEN
        d.state   := 'done';
        d.done_at := p_responded_at;
        v_replying_agent := NULL;
        IF p_response_ref ~ '^[0-9]+$' THEN
            SELECT from_agent INTO v_replying_agent FROM agent_messages WHERE id = p_response_ref::bigint;
        END IF;
        d.done_by := COALESCE(v_replying_agent, 'source:agent_messages');
    ELSIF p_skipped_at IS NOT NULL THEN
        d.state := 'dropped';
        d.dropped_at  := p_skipped_at;
        d.dropped_by  := 'source:agent_messages';
        d.drop_reason := 'skipped (priority_sla_watchdog)';
    ELSIF p_claimed_by IS NOT NULL THEN
        d.state := 'owned';
        d.owner_agent := p_claimed_by;
    ELSE
        d.state := 'captured';
    END IF;

    RETURN d;
END;
$$;

CREATE OR REPLACE FUNCTION work_item_mirror_bus_trg() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM work_item_mirror('bus', 'agent_messages', NEW.id,
        work_item_derive_bus(NEW.id, NEW.subject, NEW.responded_at, NEW.skipped_at,
                              NEW.claimed_by, NEW.response_ref),
        'trigger:agent_messages');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS work_item_mirror_bus_trg ON agent_messages;
CREATE TRIGGER work_item_mirror_bus_trg
    AFTER INSERT OR UPDATE ON agent_messages
    FOR EACH ROW WHEN (NEW.requires_response)
    EXECUTE FUNCTION work_item_mirror_bus_trg();

-- ── COMMITMENT ───────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION work_item_derive_commitment(
    p_id bigint, p_title text, p_status text, p_owner_agent text, p_due_at timestamptz,
    p_blocked_on text, p_discharged_at timestamptz, p_discharged_by text,
    p_cancelled_at timestamptz, p_cancel_reason text
) RETURNS work_item_derived LANGUAGE plpgsql AS $$
DECLARE
    d work_item_derived;
BEGIN
    d.title       := left(p_title, 200);
    d.source_ref  := 'held_commitments id=' || p_id;
    d.due_at      := p_due_at;
    d.owner_agent := p_owner_agent;

    IF p_status = 'discharged' THEN
        d.state   := 'done';
        d.done_at := p_discharged_at;
        d.done_by := p_discharged_by;
    ELSIF p_status = 'cancelled' THEN
        d.state := 'dropped';
        d.dropped_at  := COALESCE(p_cancelled_at, now());
        d.dropped_by  := 'source:held_commitments';
        d.drop_reason := COALESCE(p_cancel_reason, 'cancelled');
    ELSIF p_status = 'fired' THEN
        d.state := 'in_progress';
    ELSE -- 'pending'
        d.state := CASE WHEN p_blocked_on IS NOT NULL THEN 'in_progress' ELSE 'owned' END;
    END IF;

    RETURN d;
END;
$$;

CREATE OR REPLACE FUNCTION work_item_mirror_commitment_trg() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM work_item_mirror('commitment', 'held_commitments', NEW.id,
        work_item_derive_commitment(NEW.id, NEW.title, NEW.status, NEW.owner_agent, NEW.due_at,
                                     NEW.blocked_on, NEW.discharged_at, NEW.discharged_by,
                                     NEW.cancelled_at, NEW.cancel_reason),
        'trigger:held_commitments');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS work_item_mirror_commitment_trg ON held_commitments;
CREATE TRIGGER work_item_mirror_commitment_trg
    AFTER INSERT OR UPDATE ON held_commitments
    FOR EACH ROW EXECUTE FUNCTION work_item_mirror_commitment_trg();

-- ── ONE-TIME BACKFILL, currently-open rows only (plan per bus #73176: 319 asks + 1413
-- bus + 77 commitments = 1809 at plan time; closed/historical rows stay fully auditable
-- in their own tables, untouched, and simply never get a work_item row). Idempotent and
-- safely re-runnable any number of times: every row goes through the SAME
-- work_item_mirror() upsert+audit path as the triggers, keyed on the same
-- (source_table, source_id) UNIQUE constraint -- a re-run re-derives and upserts
-- identically and (per work_item_mirror's own no-op guard) writes a fresh audit row
-- only if a row's derived state actually changed since the last run, not on every rerun.
SELECT work_item_mirror('ask', 'operator_asks', oa.id,
         work_item_derive_ask(oa.id, oa.ask, oa.triage_state, oa.closed_at,
                               oa.delegated_to, oa.committed_date, oa.triaged_by),
         'backfill:114_work_item_ssot')
FROM operator_asks oa
WHERE oa.closed_at IS NULL;

SELECT work_item_mirror('bus', 'agent_messages', am.id,
         work_item_derive_bus(am.id, am.subject, am.responded_at, am.skipped_at,
                               am.claimed_by, am.response_ref),
         'backfill:114_work_item_ssot')
FROM agent_messages am
WHERE am.requires_response AND am.responded_at IS NULL;

SELECT work_item_mirror('commitment', 'held_commitments', hc.id,
         work_item_derive_commitment(hc.id, hc.title, hc.status, hc.owner_agent, hc.due_at,
                                      hc.blocked_on, hc.discharged_at, hc.discharged_by,
                                      hc.cancelled_at, hc.cancel_reason),
         'backfill:114_work_item_ssot')
FROM held_commitments hc
WHERE hc.status IN ('pending', 'fired');

COMMIT;
