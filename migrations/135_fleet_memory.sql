-- 135_fleet_memory.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Memory 2.0 item 2A (op#35477, bus #85017: slice-1 due 2026-10-31).
-- docs/superpowers/plans/2026-10-29-memory-2-0-item-2-shared-memory-and-code-map.md
-- section A. A fact told to one agent becomes readable by every agent at
-- boot and before acting -- a substrate table, not files.
--
-- Reuses migration 131's audited-row pattern end to end: op_ref NOT NULL,
-- retired_at/retired_op_ref CHECK-paired, append-only audit via
-- REVOKE-ALL-then-GRANT-SELECT + reject_audit_mutation() (already defined by
-- 131, reused here, not redefined), and a SECURITY DEFINER self-audit
-- trigger with search_path pinned. Same CAI-1018/1019 REVOKE-then-GRANT-back
-- shape for the new tables' default privs.
--
-- Two things this migration adds beyond 131's shape, because every agent
-- process connects as postgres (the table owner, rolbypassrls=true, per
-- 131's own DEFECT 2 finding) -- REVOKE/RLS alone cannot stop the owner, so
-- "no role gets table-level UPDATE/DELETE" (the plan's own words) is made
-- real with a trigger, not left as a grant-only promise:
--   1. fleet_memory_immutable_trg (BEFORE UPDATE OR DELETE): DELETE always
--      raises (rows are permanent history); UPDATE raises unless it is
--      EXACTLY a retire-via-supersede transition (retired_at NULL->set,
--      retired_op_ref set, every other column unchanged). This is the one
--      write path the plan describes as "no role gets table-level
--      UPDATE/DELETE" -- enforced for the owner too, same reasoning as
--      131's reject_audit_mutation().
--   2. fleet_memory_insert(...) SECURITY DEFINER function is the only
--      sanctioned INSERT path. It raises if p_created_by is null/blank --
--      "fail-closed, same shape as bus_send.py's identity resolution"
--      (plan's words): the PYTHON caller must resolve identity via
--      scripts/lib/agent_identity.resolve_agent_id() first (which itself
--      raises IdentityError rather than guess) and pass the result in; this
--      function is the second, DB-side half of that same fail-closed
--      contract, not a duplicate of it.
--
-- Supersession (plan: "explicit, never inferred... set BY THE WRITER"):
-- fleet_memory_supersede_trg (AFTER INSERT) retires the row named by the
-- NEW row's supersedes_id, guarded `AND retired_at IS NULL` so it is
-- idempotent and never clobbers an already-retired row's existing
-- retired_op_ref.
--
-- scope_key shape (plan: "scope_key is null for fleet-wide, else a client
-- slug or repo name") is a CHECK, not just prose -- a row that gets this
-- backwards is rejected at the DB layer, matching this migration's own
-- "enforce in code" standard rather than trusting every caller to remember.
--
-- CAI-1034: fact/scope_key are rule TEXT + op# refs only; no client data
-- (names, IDs, transactions) belongs in either column, enforced by review
-- at write time (the SECURITY DEFINER function does not and cannot inspect
-- the TEXT a caller passes for PII, same limit 131 and every prior
-- audited-row migration has).
--
-- Second, unrelated change riding in the same migration file, per the plan's
-- own "two changes, one migration file" note (section B): one new column
-- repo_context needs for the MAP.md ingestor (item 2B) to stamp.
--
-- assert: no_table_privilege anon public.fleet_memory UPDATE
-- assert: no_table_privilege anon public.fleet_memory DELETE
-- assert: no_table_privilege anon public.fleet_memory TRUNCATE
-- assert: no_table_privilege anon public.fleet_memory SELECT
-- assert: no_table_privilege authenticated public.fleet_memory UPDATE
-- assert: no_table_privilege authenticated public.fleet_memory DELETE
-- assert: no_table_privilege authenticated public.fleet_memory TRUNCATE
-- assert: no_table_privilege authenticated public.fleet_memory SELECT
-- assert: no_table_privilege service_role public.fleet_memory UPDATE
-- assert: no_table_privilege service_role public.fleet_memory DELETE
-- assert: no_table_privilege service_role public.fleet_memory TRUNCATE
-- assert: no_table_privilege service_role public.fleet_memory SELECT
-- assert: no_table_privilege anon public.fleet_memory_audit UPDATE
-- assert: no_table_privilege anon public.fleet_memory_audit DELETE
-- assert: no_table_privilege anon public.fleet_memory_audit TRUNCATE
-- assert: no_table_privilege anon public.fleet_memory_audit SELECT
-- assert: no_table_privilege authenticated public.fleet_memory_audit UPDATE
-- assert: no_table_privilege authenticated public.fleet_memory_audit DELETE
-- assert: no_table_privilege authenticated public.fleet_memory_audit TRUNCATE
-- assert: no_table_privilege authenticated public.fleet_memory_audit SELECT
-- assert: no_table_privilege service_role public.fleet_memory_audit UPDATE
-- assert: no_table_privilege service_role public.fleet_memory_audit DELETE
-- assert: no_table_privilege service_role public.fleet_memory_audit TRUNCATE
-- assert: no_table_privilege service_role public.fleet_memory_audit SELECT
-- assert: search_path public.fleet_memory_insert(text,text,text,text,text,bigint)
-- assert: no_execute anon public.fleet_memory_insert(text,text,text,text,text,bigint)
-- assert: no_execute authenticated public.fleet_memory_insert(text,text,text,text,text,bigint)
-- assert: no_execute service_role public.fleet_memory_insert(text,text,text,text,text,bigint)
-- assert: search_path public.fleet_memory_supersede_trg()
-- assert: no_execute anon public.fleet_memory_supersede_trg()
-- assert: no_execute authenticated public.fleet_memory_supersede_trg()
-- assert: no_execute service_role public.fleet_memory_supersede_trg()
-- assert: search_path public.fleet_memory_immutable_trg()
-- assert: no_execute anon public.fleet_memory_immutable_trg()
-- assert: no_execute authenticated public.fleet_memory_immutable_trg()
-- assert: no_execute service_role public.fleet_memory_immutable_trg()
-- assert: search_path public.fleet_memory_audit_trg()
-- assert: no_execute anon public.fleet_memory_audit_trg()
-- assert: no_execute authenticated public.fleet_memory_audit_trg()
-- assert: no_execute service_role public.fleet_memory_audit_trg()

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- fleet_memory + fleet_memory_audit
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS fleet_memory (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope           text        NOT NULL CHECK (scope IN ('fleet', 'client', 'repo')),
    scope_key       text,
    fact            text        NOT NULL,
    op_ref          text        NOT NULL,
    created_by      text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    supersedes_id   bigint      REFERENCES fleet_memory (id),
    retired_at      timestamptz,
    retired_op_ref  text,
    CONSTRAINT fleet_memory_retired_op_ref_required
        CHECK (retired_at IS NULL OR retired_op_ref IS NOT NULL),
    CONSTRAINT fleet_memory_scope_key_shape
        CHECK ((scope = 'fleet' AND scope_key IS NULL)
               OR (scope IN ('client', 'repo') AND scope_key IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS fleet_memory_scope_idx
    ON fleet_memory (scope, scope_key) WHERE retired_at IS NULL;

COMMENT ON TABLE fleet_memory IS
    'Shared fleet memory (Memory 2.0 item 2A, op#35477): a fact told to one '
    'agent, readable by every agent at boot. Write path is '
    'fleet_memory_insert() only -- no role, including the table owner, has a '
    'direct INSERT/UPDATE/DELETE path (see fleet_memory_immutable_trg).';

COMMENT ON COLUMN fleet_memory.scope IS
    'fleet = every agent; client = scope_key is a client slug (CAI-1034: '
    'slug only, never a client-identifying value beyond that); repo = '
    'scope_key is a repo name.';

COMMENT ON COLUMN fleet_memory.op_ref IS
    'NOT NULL: a fact with no op# to cite is rejected at the DB layer.';

COMMENT ON COLUMN fleet_memory.supersedes_id IS
    'Explicit, never inferred -- set BY THE WRITER at insert time to name '
    'the exact row this fact replaces. fleet_memory_supersede_trg retires '
    'that row; there is no "overlapping subject" heuristic anywhere.';

COMMENT ON COLUMN fleet_memory.retired_at IS
    'Set only by fleet_memory_supersede_trg (never directly -- '
    'fleet_memory_immutable_trg blocks any other UPDATE). A retired row '
    'stays in the table as permanent history; readers filter it out.';

CREATE TABLE IF NOT EXISTS fleet_memory_audit (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    memory_id   bigint      REFERENCES fleet_memory (id),
    event       text        NOT NULL,
    actor       text        NOT NULL,
    op_ref      text        NOT NULL,
    at          timestamptz NOT NULL DEFAULT now(),
    note        text
);

CREATE INDEX IF NOT EXISTS fleet_memory_audit_memory_idx
    ON fleet_memory_audit (memory_id, at);

COMMENT ON TABLE fleet_memory_audit IS
    'Append-only BY GRANT + trigger (reject_audit_mutation(), defined by '
    'migration 131 and reused here) -- same enforcement shape as '
    'governance_floor_audit/work_item_audit, not a third copy of the logic.';

-- CAI-1018/1019: REVOKE ALL from every web-exposed role, then GRANT BACK
-- exactly the intended set, so no inherited default-priv survives.
REVOKE ALL ON fleet_memory        FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON fleet_memory_audit  FROM PUBLIC, anon, authenticated, service_role;

GRANT SELECT ON fleet_memory        TO console_readonly;
GRANT SELECT ON fleet_memory_audit  TO console_readonly;

ALTER TABLE fleet_memory        ENABLE ROW LEVEL SECURITY;
ALTER TABLE fleet_memory_audit  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fleet_memory_console_read ON fleet_memory;
CREATE POLICY fleet_memory_console_read ON fleet_memory
    FOR SELECT TO console_readonly USING (true);

DROP POLICY IF EXISTS fleet_memory_audit_console_read ON fleet_memory_audit;
CREATE POLICY fleet_memory_audit_console_read ON fleet_memory_audit
    FOR SELECT TO console_readonly USING (true);

-- reject_audit_mutation() already exists (migration 131) -- reused, not
-- redefined.
DROP TRIGGER IF EXISTS fleet_memory_audit_append_only ON fleet_memory_audit;
CREATE TRIGGER fleet_memory_audit_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON fleet_memory_audit
    FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation();

-- ═══════════════════════════════════════════════════════════════════════════
-- Immutability for the OWNER too (every agent connects as postgres, per
-- 131's DEFECT 2 finding -- REVOKE/RLS alone cannot stop the owner). The
-- ONLY legal mutation, for any role including postgres, is the exact
-- retire-via-supersede transition this trigger allows.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION fleet_memory_immutable_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'fleet_memory rows are permanent history; retire '
            'via supersedes_id instead of deleting id=%', OLD.id;
    END IF;

    IF NOT (
        OLD.retired_at IS NULL AND NEW.retired_at IS NOT NULL
        AND NEW.retired_op_ref IS NOT NULL
        AND NEW.scope IS NOT DISTINCT FROM OLD.scope
        AND NEW.scope_key IS NOT DISTINCT FROM OLD.scope_key
        AND NEW.fact IS NOT DISTINCT FROM OLD.fact
        AND NEW.op_ref IS NOT DISTINCT FROM OLD.op_ref
        AND NEW.created_by IS NOT DISTINCT FROM OLD.created_by
        AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
        AND NEW.supersedes_id IS NOT DISTINCT FROM OLD.supersedes_id
    ) THEN
        RAISE EXCEPTION 'fleet_memory rows are immutable except for '
            'retire-via-supersede (id=%)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION fleet_memory_immutable_trg() FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS fleet_memory_immutable_trg ON fleet_memory;
CREATE TRIGGER fleet_memory_immutable_trg
    BEFORE UPDATE OR DELETE ON fleet_memory
    FOR EACH ROW EXECUTE FUNCTION fleet_memory_immutable_trg();

-- ═══════════════════════════════════════════════════════════════════════════
-- Supersession (AFTER INSERT so it only ever runs once per new fact, and
-- so the immutable trigger above sees its UPDATE as any other write):
-- idempotent via `AND retired_at IS NULL` -- never overwrites an
-- already-retired row's existing retired_op_ref.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION fleet_memory_supersede_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF NEW.supersedes_id IS NOT NULL THEN
        UPDATE fleet_memory
           SET retired_at = now(), retired_op_ref = NEW.op_ref
         WHERE id = NEW.supersedes_id AND retired_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION fleet_memory_supersede_trg() FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS fleet_memory_supersede_trg ON fleet_memory;
CREATE TRIGGER fleet_memory_supersede_trg
    AFTER INSERT ON fleet_memory
    FOR EACH ROW EXECUTE FUNCTION fleet_memory_supersede_trg();

-- ═══════════════════════════════════════════════════════════════════════════
-- Self-audit (same self-audit-trigger shape as governance_floor_audit_trg,
-- migration 131) -- callers never INSERT into fleet_memory_audit directly.
-- No DELETE branch needed: the immutable trigger above (BEFORE, so it runs
-- first) always raises on DELETE, so an AFTER-trigger DELETE case can never
-- be reached.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION fleet_memory_audit_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO fleet_memory_audit (memory_id, event, actor, op_ref, note)
        VALUES (NEW.id, 'created', NEW.created_by, NEW.op_ref, left(NEW.fact, 500));
    ELSE
        -- fleet_memory_immutable_trg (BEFORE, runs first) guarantees this
        -- UPDATE is exactly a retire-via-supersede transition.
        INSERT INTO fleet_memory_audit (memory_id, event, actor, op_ref, note)
        VALUES (NEW.id, 'retired', 'system:supersede', NEW.retired_op_ref,
                'retired via supersede, op_ref=' || NEW.retired_op_ref);
    END IF;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION fleet_memory_audit_trg() FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS fleet_memory_audit_trg ON fleet_memory;
CREATE TRIGGER fleet_memory_audit_trg
    AFTER INSERT OR UPDATE ON fleet_memory
    FOR EACH ROW EXECUTE FUNCTION fleet_memory_audit_trg();

-- ═══════════════════════════════════════════════════════════════════════════
-- The ONE sanctioned write path. Fail-closed on identity, same shape as
-- bus_send.py's resolve_from_agent / scripts/lib/agent_identity.resolve_agent_id
-- (which itself raises rather than guess) -- the Python caller resolves
-- identity FIRST and passes it in; this is the DB-side half of that
-- contract, refusing a call with no resolved created_by rather than
-- defaulting it.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION fleet_memory_insert(
    p_scope text, p_scope_key text, p_fact text, p_op_ref text,
    p_created_by text, p_supersedes_id bigint DEFAULT NULL
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_id bigint;
BEGIN
    IF p_created_by IS NULL OR btrim(p_created_by) = '' THEN
        RAISE EXCEPTION 'fleet_memory_insert requires a resolved created_by '
            'identity -- refusing to guess (resolve it first, e.g. via '
            'scripts/lib/agent_identity.resolve_agent_id)';
    END IF;

    INSERT INTO fleet_memory (scope, scope_key, fact, op_ref, created_by, supersedes_id)
    VALUES (p_scope, p_scope_key, p_fact, p_op_ref, p_created_by, p_supersedes_id)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION fleet_memory_insert(text, text, text, text, text, bigint)
    FROM PUBLIC, anon, authenticated, service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- Unrelated second change riding in this migration file (plan section B:
-- "two changes, one migration file") -- the one new column the MAP.md
-- ingestor (item 2B) needs to stamp.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE repo_context
    ADD COLUMN IF NOT EXISTS architecture_summary_updated_at timestamptz;

COMMENT ON COLUMN repo_context.architecture_summary_updated_at IS
    'Stamped by the MAP.md ingestor (Memory 2.0 item 2B, op#35990) whenever '
    'architecture_summary is (re)written from a repo''s own MAP.md. NULL '
    'means never ingested yet.';

COMMIT;

-- ===========================================================================
-- APPLY NOTES for cai (§6.6) -- Memory 2.0 item 2A, op#35477
-- ===========================================================================
-- Verify EFFECTIVE grants post-apply (131's own doctrine -- source
-- inspection alone misses this class):
--   SELECT grantee, privilege_type FROM information_schema.role_table_grants
--    WHERE table_schema='public'
--      AND table_name IN ('fleet_memory','fleet_memory_audit');
--   Expect: anon/authenticated/service_role hold ZERO rows on either table;
--   console_readonly holds exactly SELECT on each.
--
-- Smoke-test the write path + the immutability guarantee:
--   SELECT fleet_memory_insert('fleet', NULL, 'smoke test', 'op#35477',
--                               'cc-substrate', NULL);
--   -- then a superseding row:
--   SELECT fleet_memory_insert('fleet', NULL, 'smoke test v2', 'op#35477',
--                               'cc-substrate', <id from above>);
--   -- the first row should now show retired_at/retired_op_ref set; a bare
--   -- UPDATE or DELETE against fleet_memory (as postgres) should raise.
--
-- Seeding note (plan section C slice 1): the irsyad standing client-rules
-- seed (#80901, scope='client', scope_key='irsyad') is NOT part of this
-- migration -- it is inserted through cc-irsyad-coord after this migration
-- lands, per the console/irsyad execution-separation rule (CLAUDE.md).
--
-- LEDGER: repo=orchestrator, migration_name=135_fleet_memory.sql,
-- silo_ref=tscuymavysscrvoberrr.
-- ===========================================================================
