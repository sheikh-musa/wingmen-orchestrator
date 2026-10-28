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
-- CONSOLE REVIEW, bus #80555 -- 2 real defects found in the first version of
-- this migration, both fixed here:
--   DEFECT 1 (mirror drift under service_role): live code UPDATEs
--   agent_messages AS service_role via the Supabase REST path
--   (agent_messages_poll.py:363,378, build_launch_context.py:192). The REVOKE
--   above removes service_role's direct grants on work_item, so the AFTER
--   UPDATE mirror trigger -- which ran with the CALLER's privileges -- would
--   hit permission denied, get swallowed by the trigger's own EXCEPTION WHEN
--   OTHERS handler, and silently stop tracking skipped/forwarded rows. Fixed
--   by making every work_item_mirror_*/work_item_mark_source_deleted function
--   SECURITY DEFINER (runs as the migration owner regardless of caller), with
--   search_path pinned (SET search_path = public, pg_temp -- the standard
--   SECURITY DEFINER hardening against search_path hijacking) and EXECUTE
--   revoked from PUBLIC/anon/authenticated (so a web-exposed role can't call
--   these AS an RPC to get the SAME elevated write the REVOKE above was
--   meant to close -- firing via a normal table trigger needs no EXECUTE
--   grant at all, only UPDATE/INSERT/DELETE on the SOURCE table, which
--   service_role still has).
--   DEFECT 2 (append-only did not bind the owner): every agent connects as
--   postgres, the table OWNER -- REVOKE from anon/authenticated/service_role
--   stops none of them, and owner sessions bypass RLS entirely regardless of
--   FORCE ROW LEVEL SECURITY (postgres has rolbypassrls=true, live-verified).
--   Only a trigger -- which fires for every role unconditionally, RLS/grants
--   notwithstanding -- can make "append-only" and "op_ref required" true for
--   the owner too. Fixed with (a) a BEFORE UPDATE OR DELETE OR TRUNCATE
--   statement trigger on governance_floor_audit AND work_item_audit that
--   RAISEs unconditionally, and (b) an AFTER INSERT OR UPDATE OR DELETE
--   trigger on governance_floor that writes its own audit row (callers no
--   longer INSERT into governance_floor_audit directly for floor-lifecycle
--   events), reading actor/op_ref from session GUCs (app.actor, app.op_ref)
--   and RAISING if app.op_ref is unset on UPDATE/DELETE -- INSERT uses the
--   row's own already-NOT-NULL op_ref column instead, so creating a floor
--   still only needs the column, not the GUC.
--
-- assert: no_table_privilege anon public.governance_floor UPDATE
-- assert: no_table_privilege anon public.governance_floor DELETE
-- assert: no_table_privilege anon public.governance_floor TRUNCATE
-- assert: no_table_privilege authenticated public.governance_floor UPDATE
-- assert: no_table_privilege authenticated public.governance_floor DELETE
-- assert: no_table_privilege authenticated public.governance_floor TRUNCATE
-- assert: no_table_privilege service_role public.governance_floor UPDATE
-- assert: no_table_privilege service_role public.governance_floor DELETE
-- assert: no_table_privilege service_role public.governance_floor TRUNCATE
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
-- assert: search_path public.work_item_mirror(text,text,bigint,work_item_derived,text)
-- assert: no_execute anon public.work_item_mirror(text,text,bigint,work_item_derived,text)
-- assert: no_execute authenticated public.work_item_mirror(text,text,bigint,work_item_derived,text)
-- assert: search_path public.work_item_mark_source_deleted(text,bigint,text)
-- assert: no_execute anon public.work_item_mark_source_deleted(text,bigint,text)
-- assert: no_execute authenticated public.work_item_mark_source_deleted(text,bigint,text)
-- assert: search_path public.work_item_mirror_ask_trg()
-- assert: no_execute anon public.work_item_mirror_ask_trg()
-- assert: no_execute authenticated public.work_item_mirror_ask_trg()
-- assert: search_path public.work_item_mirror_bus_trg()
-- assert: no_execute anon public.work_item_mirror_bus_trg()
-- assert: no_execute authenticated public.work_item_mirror_bus_trg()
-- assert: search_path public.work_item_mirror_commitment_trg()
-- assert: no_execute anon public.work_item_mirror_commitment_trg()
-- assert: no_execute authenticated public.work_item_mirror_commitment_trg()
-- assert: search_path public.work_item_mirror_ask_delete_trg()
-- assert: no_execute anon public.work_item_mirror_ask_delete_trg()
-- assert: no_execute authenticated public.work_item_mirror_ask_delete_trg()
-- assert: search_path public.work_item_mirror_bus_delete_trg()
-- assert: no_execute anon public.work_item_mirror_bus_delete_trg()
-- assert: no_execute authenticated public.work_item_mirror_bus_delete_trg()
-- assert: search_path public.work_item_mirror_commitment_delete_trg()
-- assert: no_execute anon public.work_item_mirror_commitment_delete_trg()
-- assert: no_execute authenticated public.work_item_mirror_commitment_delete_trg()

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
    retired_at          timestamptz,
    retired_op_ref      text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT governance_floor_retired_op_ref_required
        CHECK (retired_at IS NULL OR retired_op_ref IS NOT NULL)
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

COMMENT ON COLUMN governance_floor.retired_at IS
    'A lifted/withdrawn floor (console bus #80595: Musa lifts and scopes floors, '
    'governance-floors.md already has lift/withdrawal entries). Floor rows are '
    'permanent history (DELETE is FK-blocked) -- retiring is how a floor stops '
    'being evaluated without erasing it. Set/cleared only via UPDATE, which the '
    'self-audit trigger below already requires app.op_ref for -- un-retiring is '
    'equally op-gated.';

COMMENT ON COLUMN governance_floor.retired_op_ref IS
    'The op# that retired (or un-retired) this floor -- distinct from op_ref, '
    'which cites the op# that CREATED it.';

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
-- Console defect 2 (bus #80555): append-only must bind the OWNER too, not
-- just the web-exposed roles the REVOKE above targets -- every agent connects
-- as postgres (the owner), and owner sessions bypass RLS entirely regardless
-- of grants. Only a trigger fires unconditionally for every role.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION reject_audit_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not permitted, even for the table owner', TG_TABLE_NAME, TG_OP;
END;
$$;

REVOKE EXECUTE ON FUNCTION reject_audit_mutation() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS governance_floor_audit_append_only ON governance_floor_audit;
CREATE TRIGGER governance_floor_audit_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON governance_floor_audit
    FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation();

-- governance_floor's own lifecycle changes self-audit -- callers no longer
-- INSERT into governance_floor_audit directly for create/update/delete; this
-- trigger does it, reading actor/op_ref from session GUCs and REQUIRING
-- app.op_ref on UPDATE/DELETE (a change with no op# cited is rejected at the
-- DB layer, for every role, including the owner). INSERT uses the row's own
-- already-NOT-NULL op_ref column instead, so creating a floor needs only the
-- column, not the GUC.
CREATE OR REPLACE FUNCTION governance_floor_audit_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_actor  text := current_setting('app.actor', true);
    v_op_ref text := current_setting('app.op_ref', true);
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, note)
        VALUES (NEW.id, 'created', COALESCE(v_actor, 'unknown'), NEW.op_ref, NEW.sentence);
        RETURN NEW;
    END IF;

    IF v_op_ref IS NULL OR v_op_ref = '' THEN
        RAISE EXCEPTION 'governance_floor % requires app.op_ref to be set first '
            '(SELECT set_config(''app.op_ref'', ''op#...'', false))', TG_OP;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF OLD.retired_at IS NULL AND NEW.retired_at IS NOT NULL THEN
            INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, note)
            VALUES (NEW.id, 'retired', COALESCE(v_actor, 'unknown'), v_op_ref, NEW.sentence);
        ELSIF OLD.retired_at IS NOT NULL AND NEW.retired_at IS NULL THEN
            INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, note)
            VALUES (NEW.id, 'unretired', COALESCE(v_actor, 'unknown'), v_op_ref, NEW.sentence);
        ELSE
            INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, note)
            VALUES (NEW.id, 'updated', COALESCE(v_actor, 'unknown'), v_op_ref, NEW.sentence);
        END IF;
        RETURN NEW;
    ELSE -- DELETE
        INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, note)
        VALUES (OLD.id, 'deleted', COALESCE(v_actor, 'unknown'), v_op_ref, OLD.sentence);
        RETURN OLD;
    END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION governance_floor_audit_trg() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS governance_floor_self_audit ON governance_floor;
CREATE TRIGGER governance_floor_self_audit
    AFTER INSERT OR UPDATE OR DELETE ON governance_floor
    FOR EACH ROW EXECUTE FUNCTION governance_floor_audit_trg();

DROP TRIGGER IF EXISTS work_item_audit_append_only ON work_item_audit;
CREATE TRIGGER work_item_audit_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON work_item_audit
    FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation();

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
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
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

CREATE OR REPLACE FUNCTION work_item_mirror_ask_delete_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
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

CREATE OR REPLACE FUNCTION work_item_mirror_bus_delete_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
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

CREATE OR REPLACE FUNCTION work_item_mirror_commitment_delete_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
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

CREATE OR REPLACE FUNCTION work_item_mirror_bus_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
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

-- ═══════════════════════════════════════════════════════════════════════════
-- Console defect 1 (bus #80555), continued: work_item_mirror() itself, and the
-- two 114-defined trigger functions this migration does not otherwise touch
-- (ask_trg, commitment_trg), get the same SECURITY DEFINER + search_path pin
-- via ALTER FUNCTION (no body change, so CREATE OR REPLACE is unnecessary).
-- EXECUTE revoked from PUBLIC/anon/authenticated on every one of the 8
-- mirror/delete functions -- firing via a normal table trigger needs no
-- EXECUTE grant at all (only DML on the SOURCE table, untouched here), so
-- this closes the direct-RPC path without affecting the trigger path.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER FUNCTION work_item_mirror(text, text, bigint, work_item_derived, text)
    SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION work_item_mirror_ask_trg()
    SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION work_item_mirror_commitment_trg()
    SECURITY DEFINER SET search_path = public, pg_temp;

REVOKE EXECUTE ON FUNCTION work_item_mirror(text, text, bigint, work_item_derived, text)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION work_item_mark_source_deleted(text, bigint, text)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION work_item_mirror_ask_trg()          FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION work_item_mirror_bus_trg()          FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION work_item_mirror_commitment_trg()   FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION work_item_mirror_ask_delete_trg()        FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION work_item_mirror_bus_delete_trg()        FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION work_item_mirror_commitment_delete_trg() FROM PUBLIC, anon, authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
-- Floor row #1: the 72h-ABANDONED check (already proven, 247-row dry-run,
-- console's ruling bus #78827: stays dry-run, seeds page-state, the 247 go
-- out as one triage list). Query lifted verbatim from work_item_reconcile.py's
-- _abandoned() (draft branch fable/substrate2-build-20261027, PR#548), inverted
-- to a boolean "floor passing" form. The due_at clause is load-bearing: a row
-- parked against a future due date is waiting, not abandoned.
-- ═══════════════════════════════════════════════════════════════════════════

INSERT INTO governance_floor (sentence, op_ref, predicate_sql, enforced_in_code)
SELECT
    'No work_item in owned/in_progress sits untouched past 72 hours unless it carries a future due_at.',
    'op#32730',
    $pred$NOT EXISTS (
        SELECT 1 FROM work_item
         WHERE state IN ('owned','in_progress') AND updated_at < now() - interval '72 hours'
           AND (due_at IS NULL OR due_at <= now())
    )$pred$,
    true
WHERE NOT EXISTS (
    SELECT 1 FROM governance_floor WHERE op_ref = 'op#32730' AND sentence LIKE 'No work_item in owned/in_progress%'
);

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
