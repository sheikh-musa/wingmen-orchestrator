-- 131_governance_floor.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#35246 Phase 1a/1b (PR#555, reports/joint-plan-20261028/PLAN.md section 3;
-- scope confirmed by console bus #80466; live-grant finding reported bus #80523).
--
-- PHASE 1a: governance_floor + governance_floor_audit. Floors as executable rows
-- (PLAN.md's "floors as rows" idea), append-only by GRANT, not convention
-- (CAI-1018/1019 doctrine, migrations 039/049): every NEW table on this schema
-- inherits Supabase's standing ALTER DEFAULT PRIVILEGES grant of full DML to
-- anon/authenticated/service_role. The robust fix is the general form used in
-- 049: REVOKE ALL from every non-owner web-exposed role, then GRANT BACK exactly
-- the intended set, so no default-priv survives for a present OR future role.
--
-- decision_ref is console's scope ruling (bus #80466): no new decision_log
-- table -- decisions already have a place of record (strategic_decisions, read
-- via boot_briefing/get_decision). A floor row may optionally cite one.
--
-- PHASE 1b: closes 3 live gaps on work_item/work_item_audit (migration 114),
-- found by this migration's own author via EFFECTIVE-grant inspection
-- (information_schema.role_table_grants), not by trusting 114's source comments
-- (reported bus #80523):
--   (1) no AFTER DELETE trigger -- a deleted source row orphaned its mirror.
--   (2) no re-sync when agent_messages.requires_response flips true->false --
--       the mirror trigger's own `WHEN (NEW.requires_response)` clause meant it
--       never fired again once a row stopped requiring a response.
--   (3) work_item/work_item_audit carry the SAME CAI-1016/1018 leak class as
--       039/049 found and fixed elsewhere, never applied here: live-verified,
--       authenticated held DELETE/INSERT/UPDATE and service_role held
--       DELETE/UPDATE/TRUNCATE on BOTH tables; console_readonly had ZERO
--       grants on either (console's own "no read-grant" framing, confirmed).
--       service_role has rolbypassrls=true (same as postgres) -- RLS cannot
--       restrict it at all, so the table-grant REVOKE is the only thing that
--       actually closes this for service_role; RLS below is what restricts
--       anon/authenticated/console_readonly/bus_writer (all rolbypassrls=false).
--
-- assert: no_table_privilege anon public.governance_floor_audit UPDATE
-- assert: no_table_privilege anon public.governance_floor_audit DELETE
-- assert: no_table_privilege anon public.governance_floor_audit TRUNCATE
-- assert: no_table_privilege authenticated public.governance_floor_audit UPDATE
-- assert: no_table_privilege authenticated public.governance_floor_audit DELETE
-- assert: no_table_privilege authenticated public.governance_floor_audit TRUNCATE
-- assert: no_table_privilege service_role public.governance_floor_audit UPDATE
-- assert: no_table_privilege service_role public.governance_floor_audit DELETE
-- assert: no_table_privilege service_role public.governance_floor_audit TRUNCATE
-- assert: no_table_privilege anon public.work_item UPDATE
-- assert: no_table_privilege anon public.work_item DELETE
-- assert: no_table_privilege anon public.work_item TRUNCATE
-- assert: no_table_privilege authenticated public.work_item UPDATE
-- assert: no_table_privilege authenticated public.work_item DELETE
-- assert: no_table_privilege authenticated public.work_item TRUNCATE
-- assert: no_table_privilege service_role public.work_item UPDATE
-- assert: no_table_privilege service_role public.work_item DELETE
-- assert: no_table_privilege service_role public.work_item TRUNCATE
-- assert: no_table_privilege anon public.work_item_audit UPDATE
-- assert: no_table_privilege anon public.work_item_audit DELETE
-- assert: no_table_privilege anon public.work_item_audit TRUNCATE
-- assert: no_table_privilege authenticated public.work_item_audit UPDATE
-- assert: no_table_privilege authenticated public.work_item_audit DELETE
-- assert: no_table_privilege authenticated public.work_item_audit TRUNCATE
-- assert: no_table_privilege service_role public.work_item_audit UPDATE
-- assert: no_table_privilege service_role public.work_item_audit DELETE
-- assert: no_table_privilege service_role public.work_item_audit TRUNCATE

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- PHASE 1a: governance_floor + governance_floor_audit
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS governance_floor (
    id                  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sentence            text        NOT NULL,
    op_ref              text        NOT NULL,
    predicate_sql       text        NOT NULL,
    enforced_in_code    boolean     NOT NULL DEFAULT false,
    decision_ref        text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE governance_floor IS
    'Floors as executable rows (op#35246 Phase 1a, PR#555). predicate_sql is a '
    'boolean-returning SQL expression ("floor passing" = true), evaluated by the '
    'reconciler on a schedule. Governance-floors.md''s generated block renders '
    'this table; the surrounding prose stays hand-authored (richer than any row '
    'can express yet).';

COMMENT ON COLUMN governance_floor.op_ref IS
    'NOT NULL: a floor with no op# to cite is rejected at the DB layer, not just '
    'linted (PLAN.md section 3 exit test).';

COMMENT ON COLUMN governance_floor.decision_ref IS
    'Optional pointer into strategic_decisions (console scope ruling, bus #80466: '
    'decisions already have a place of record -- this just lets a rule cite the '
    'decision behind it). Deliberately not an FK: a decision may predate the '
    'floor it later justifies, or live in a different table generation.';

CREATE TABLE IF NOT EXISTS governance_floor_audit (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    floor_id    bigint      REFERENCES governance_floor (id),
    event       text        NOT NULL,
    actor       text        NOT NULL,
    op_ref      text        NOT NULL,
    at          timestamptz NOT NULL DEFAULT now(),
    note        text
);

CREATE INDEX IF NOT EXISTS governance_floor_audit_floor_idx
    ON governance_floor_audit (floor_id, at);

COMMENT ON TABLE governance_floor_audit IS
    'Append-only BY GRANT (see REVOKE below), not by convention -- the gap '
    'work_item_audit shipped with (migration 114) and this migration also '
    'closes. op_ref NOT NULL: a floor change with no op# to cite is rejected at '
    'the DB layer.';

-- CAI-1018/1019: REVOKE ALL from every web-exposed role, then GRANT BACK
-- exactly the intended set, so no inherited default-priv survives.
REVOKE ALL ON governance_floor        FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON governance_floor_audit  FROM PUBLIC, anon, authenticated, service_role;

GRANT SELECT               ON governance_floor        TO console_readonly;
GRANT SELECT               ON governance_floor_audit  TO console_readonly;

ALTER TABLE governance_floor         ENABLE ROW LEVEL SECURITY;
ALTER TABLE governance_floor_audit   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS governance_floor_console_read ON governance_floor;
CREATE POLICY governance_floor_console_read ON governance_floor
    FOR SELECT TO console_readonly USING (true);

DROP POLICY IF EXISTS governance_floor_audit_console_read ON governance_floor_audit;
CREATE POLICY governance_floor_audit_console_read ON governance_floor_audit
    FOR SELECT TO console_readonly USING (true);

-- ═══════════════════════════════════════════════════════════════════════════
-- PHASE 1b, gap 3: close the same leak on work_item / work_item_audit
-- ═══════════════════════════════════════════════════════════════════════════

REVOKE ALL ON work_item        FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON work_item_audit  FROM PUBLIC, anon, authenticated, service_role;

-- bus_writer's existing scoped grants (migration 114/123) are untouched by the
-- REVOKE above -- REVOKE ALL FROM <role list> only touches the named roles.
GRANT SELECT  ON work_item        TO console_readonly;
GRANT SELECT  ON work_item_audit  TO console_readonly;

ALTER TABLE work_item        ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_item_audit  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS work_item_console_read ON work_item;
CREATE POLICY work_item_console_read ON work_item
    FOR SELECT TO console_readonly USING (true);

DROP POLICY IF EXISTS work_item_bus_writer_rw ON work_item;
CREATE POLICY work_item_bus_writer_rw ON work_item
    FOR ALL TO bus_writer USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS work_item_audit_console_read ON work_item_audit;
CREATE POLICY work_item_audit_console_read ON work_item_audit
    FOR SELECT TO console_readonly USING (true);

DROP POLICY IF EXISTS work_item_audit_bus_writer_insert ON work_item_audit;
CREATE POLICY work_item_audit_bus_writer_insert ON work_item_audit
    FOR INSERT TO bus_writer WITH CHECK (true);

-- ═══════════════════════════════════════════════════════════════════════════
-- PHASE 1b, gap 1: AFTER DELETE must not orphan the mirror
-- ═══════════════════════════════════════════════════════════════════════════

-- Deliberately NOT a reuse of work_item_mirror() -- that function recomputes
-- every field from a full NEW row, which does not exist on DELETE. This is a
-- narrow, separate path that touches only (state, dropped_*, updated_at),
-- preserving title/owner_agent/source_ref/due_at untouched, and no-ops if the
-- mirror is already terminal (done/dropped) so a source row archived AFTER
-- completion can never retcon a done item back to dropped.
CREATE OR REPLACE FUNCTION work_item_mark_source_deleted(
    p_source_table text, p_source_id bigint, p_actor text
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_id bigint;
    prior_state text;
BEGIN
    SELECT id, state INTO v_id, prior_state FROM work_item
     WHERE source_table = p_source_table AND source_id = p_source_id;

    IF v_id IS NULL OR prior_state IN ('done', 'dropped') THEN
        RETURN;
    END IF;

    UPDATE work_item
       SET state = 'dropped', dropped_at = now(), dropped_by = 'source:deleted',
           drop_reason = 'source row deleted', updated_at = now()
     WHERE id = v_id;

    INSERT INTO work_item_audit (work_item_id, event, actor, note)
    VALUES (v_id, 'state_changed', p_actor, prior_state || ' -> dropped (source deleted)');
END;
$$;

CREATE OR REPLACE FUNCTION work_item_mirror_ask_delete_trg() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        PERFORM work_item_mark_source_deleted('operator_asks', OLD.id, 'trigger:operator_asks:deleted');
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'work_item drop-mirror failed for operator_asks id=%: %', OLD.id, SQLERRM;
    END;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS work_item_mirror_ask_delete_trg ON operator_asks;
CREATE TRIGGER work_item_mirror_ask_delete_trg
    AFTER DELETE ON operator_asks
    FOR EACH ROW EXECUTE FUNCTION work_item_mirror_ask_delete_trg();

CREATE OR REPLACE FUNCTION work_item_mirror_bus_delete_trg() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        PERFORM work_item_mark_source_deleted('agent_messages', OLD.id, 'trigger:agent_messages:deleted');
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'work_item drop-mirror failed for agent_messages id=%: %', OLD.id, SQLERRM;
    END;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS work_item_mirror_bus_delete_trg ON agent_messages;
CREATE TRIGGER work_item_mirror_bus_delete_trg
    AFTER DELETE ON agent_messages
    FOR EACH ROW EXECUTE FUNCTION work_item_mirror_bus_delete_trg();

CREATE OR REPLACE FUNCTION work_item_mirror_commitment_delete_trg() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        PERFORM work_item_mark_source_deleted('held_commitments', OLD.id, 'trigger:held_commitments:deleted');
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'work_item drop-mirror failed for held_commitments id=%: %', OLD.id, SQLERRM;
    END;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS work_item_mirror_commitment_delete_trg ON held_commitments;
CREATE TRIGGER work_item_mirror_commitment_delete_trg
    AFTER DELETE ON held_commitments
    FOR EACH ROW EXECUTE FUNCTION work_item_mirror_commitment_delete_trg();

-- ═══════════════════════════════════════════════════════════════════════════
-- PHASE 1b, gap 2: requires_response true->false must re-sync, not go stale
-- ═══════════════════════════════════════════════════════════════════════════

-- work_item_derive_bus() gains a requires_response param: once responded_at/
-- skipped_at (more specific, already-authoritative signals) are ruled out, a
-- row that no longer requires a response is dropped -- but only from a
-- non-terminal state; the caller trigger's own re-fire is what makes this
-- reachable at all (see the WHEN clause fix below).
CREATE OR REPLACE FUNCTION work_item_derive_bus(
    p_id bigint, p_subject text, p_responded_at timestamptz, p_skipped_at timestamptz,
    p_claimed_by text, p_response_ref text, p_requires_response boolean
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
    ELSIF NOT p_requires_response THEN
        d.state := 'dropped';
        d.dropped_at  := now();
        d.dropped_by  := 'source:agent_messages';
        d.drop_reason := 'requires_response cleared';
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
    BEGIN
        PERFORM work_item_mirror('bus', 'agent_messages', NEW.id,
            work_item_derive_bus(NEW.id, NEW.subject, NEW.responded_at, NEW.skipped_at,
                                  NEW.claimed_by, NEW.response_ref, NEW.requires_response),
            'trigger:agent_messages');
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'work_item mirror failed for agent_messages id=%: %', NEW.id, SQLERRM;
    END;
    RETURN NEW;
END;
$$;

-- The actual gap: the old single INSERT-OR-UPDATE trigger's
-- WHEN (NEW.requires_response) clause meant it never fired again once a row
-- stopped requiring a response, so a stale mirror could never be re-synced.
-- Split into two triggers because an INSERT trigger's WHEN cannot reference
-- OLD at all (there is no prior row). The UPDATE trigger fires on EITHER the
-- old or new value, so the derive function above sees the flip and drops the
-- mirror, while NOT firing on rows that never required a response either side
-- of the UPDATE (no mirror created for the first time on an unrelated column
-- change to an always-false row).
DROP TRIGGER IF EXISTS work_item_mirror_bus_trg ON agent_messages;

CREATE TRIGGER work_item_mirror_bus_insert_trg
    AFTER INSERT ON agent_messages
    FOR EACH ROW WHEN (NEW.requires_response)
    EXECUTE FUNCTION work_item_mirror_bus_trg();

CREATE TRIGGER work_item_mirror_bus_update_trg
    AFTER UPDATE ON agent_messages
    FOR EACH ROW WHEN (NEW.requires_response OR OLD.requires_response IS TRUE)
    EXECUTE FUNCTION work_item_mirror_bus_trg();

COMMIT;

-- ===========================================================================
-- APPLY NOTES for cai (§6.6) — op#35246 Phase 1a/1b
-- ===========================================================================
-- Verify EFFECTIVE grants post-apply (the method this migration's own finding
-- used, per 049's doctrine -- source inspection alone misses this class):
--   SELECT grantee, privilege_type FROM information_schema.role_table_grants
--    WHERE table_schema='public'
--      AND table_name IN ('governance_floor','governance_floor_audit',
--                          'work_item','work_item_audit');
--   Expect: anon/authenticated/service_role hold ZERO rows on any of the 4
--   tables (service_role's rolbypassrls=true makes the REVOKE, not RLS, the
--   thing that actually closes it for that role); console_readonly holds
--   exactly SELECT on each; bus_writer's pre-existing grants on
--   work_item/work_item_audit are unchanged.
-- LEDGER: repo=orchestrator, migration_name=131_governance_floor.sql,
-- silo_ref=tscuymavysscrvoberrr.
-- ===========================================================================
