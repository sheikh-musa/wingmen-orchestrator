-- 139_fleet_memory_facts.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Memory 2.0 item 2A Part 2 (op#36768, backlog #127; console approval
-- bus #87062/#87068). Migration 135 shipped fleet_memory with fact + op_ref
-- + created_by/at and supersede/retire, but NOT the two fields the operator
-- asked for -- verified_at and expires_at -- so a fact had no way to say
-- when it was last confirmed, or when it should stop being believed.
--
-- Second, and the reason this migration is not a bare ALTER: 135's
-- fleet_memory_immutable_trg pins an explicit column list
-- (NEW.x IS NOT DISTINCT FROM OLD.x for each named column). Any column
-- added later is therefore NOT covered by the pin, and a bare UPDATE could
-- silently rewrite it -- the "one legal write path" guarantee would keep
-- holding for the old columns and quietly stop holding for the new ones.
-- This file re-creates that trigger function with verified_at and
-- expires_at added to the pinned list, so re-verification stays a supersede
-- (new row, explicit supersedes_id) rather than an in-place edit.
--
-- Backfill: existing rows get verified_at = created_at, not now(). Their
-- true verification time is unknown; created_at is the only honest value we
-- hold. The trigger is disabled for exactly that one UPDATE and re-enabled
-- in the same transaction -- without it the immutability trigger (correctly)
-- refuses the backfill.
--
-- op_ref stays the single source column you asked for ("source op/bus id"):
-- it carries either form as text -- 'op#35246' or a bus row id -- and 135's
-- NOT NULL already makes an uncited fact impossible.
--
-- owner is NOT a new column: created_by IS the owner (console ruling,
-- bus #87068 decision 2). The comment below records that so nobody adds a
-- second column later to mean the same thing.
--
-- assert: dropped public.fleet_memory_insert(text,text,text,text,text,bigint)
-- assert: search_path public.fleet_memory_insert(text,text,text,text,text,bigint,timestamp with time zone,timestamp with time zone)
-- assert: no_execute anon public.fleet_memory_insert(text,text,text,text,text,bigint,timestamp with time zone,timestamp with time zone)
-- assert: no_execute authenticated public.fleet_memory_insert(text,text,text,text,text,bigint,timestamp with time zone,timestamp with time zone)
-- assert: no_execute service_role public.fleet_memory_insert(text,text,text,text,text,bigint,timestamp with time zone,timestamp with time zone)
-- assert: search_path public.fleet_memory_immutable_trg()
-- assert: no_execute anon public.fleet_memory_immutable_trg()
-- assert: no_execute authenticated public.fleet_memory_immutable_trg()
-- assert: no_execute service_role public.fleet_memory_immutable_trg()

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- The two missing fields
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE fleet_memory ADD COLUMN IF NOT EXISTS verified_at timestamptz;
ALTER TABLE fleet_memory ADD COLUMN IF NOT EXISTS expires_at  timestamptz;

COMMENT ON COLUMN fleet_memory.verified_at IS
    'When this fact was last confirmed true. Set at insert; changed only by '
    'superseding the row (a re-verification is a new fact, not an edit).';
COMMENT ON COLUMN fleet_memory.expires_at IS
    'Optional. Readers exclude a row once now() > expires_at. NULL = no '
    'expiry; the fact stands until it is explicitly superseded.';
COMMENT ON COLUMN fleet_memory.created_by IS
    'Who wrote the fact. This is ALSO the owner of the fact (console ruling, '
    'bus #87068) -- there is no separate owner column and should not be one.';

-- Backfill before the NOT NULL lands: created_at is the only honest value
-- we hold for rows written before this column existed.
--
-- BOTH triggers must stand down for this one statement, not just the
-- immutability one (found on the live dry run 2026-11-01, console bus #87228;
-- the tests here had only ever migrated an EMPTY table, so they never fired
-- it). fleet_memory_audit_trg is AFTER INSERT OR UPDATE and treats every
-- UPDATE as a retire: it writes NEW.retired_op_ref into
-- fleet_memory_audit.op_ref, which is NOT NULL. 135 made that assumption safe
-- by having fleet_memory_immutable_trg reject any UPDATE that is not a
-- retire, so the audit trigger could never see a non-retire update. This
-- backfill is the first such update, and with rows present it raised
-- NotNullViolation and rolled the whole migration back.
--
-- The backfill deliberately leaves NO audit row: the audit table records fact
-- lifecycle (created/retired), and a metadata backfill is neither. Logging it
-- as 'retired' would be a falsehood in an append-only table. DDL is
-- transactional, so a failure between these statements rolls the DISABLE back
-- too -- the triggers cannot be left off.
ALTER TABLE fleet_memory DISABLE TRIGGER fleet_memory_immutable_trg;
ALTER TABLE fleet_memory DISABLE TRIGGER fleet_memory_audit_trg;
UPDATE fleet_memory SET verified_at = created_at WHERE verified_at IS NULL;
ALTER TABLE fleet_memory ENABLE TRIGGER fleet_memory_immutable_trg;
ALTER TABLE fleet_memory ENABLE TRIGGER fleet_memory_audit_trg;

ALTER TABLE fleet_memory
    ALTER COLUMN verified_at SET NOT NULL,
    ALTER COLUMN verified_at SET DEFAULT now();

-- ═══════════════════════════════════════════════════════════════════════════
-- Re-pin the immutability guarantee over the new columns (see header)
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
        AND NEW.verified_at IS NOT DISTINCT FROM OLD.verified_at
        AND NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at
    ) THEN
        RAISE EXCEPTION 'fleet_memory rows are immutable except for '
            'retire-via-supersede (id=%)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION fleet_memory_immutable_trg() FROM PUBLIC, anon, authenticated, service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- Still the ONE sanctioned write path, now carrying the two new fields.
-- The 6-arg overload is DROPPED, not kept beside the new one: two overloads
-- whose defaults let a 6-positional call match either is an ambiguity trap.
-- The 8-arg form keeps the old two defaults, so existing 6-arg callers are
-- unchanged.
-- ═══════════════════════════════════════════════════════════════════════════

DROP FUNCTION fleet_memory_insert(text, text, text, text, text, bigint);

CREATE FUNCTION fleet_memory_insert(
    p_scope text, p_scope_key text, p_fact text, p_op_ref text,
    p_created_by text, p_supersedes_id bigint DEFAULT NULL,
    p_verified_at timestamptz DEFAULT NULL, p_expires_at timestamptz DEFAULT NULL
) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_id bigint;
BEGIN
    IF p_created_by IS NULL OR btrim(p_created_by) = '' THEN
        RAISE EXCEPTION 'fleet_memory_insert requires a resolved created_by '
            'identity -- refusing to guess (resolve it first, e.g. via '
            'scripts/lib/agent_identity.resolve_agent_id)';
    END IF;

    INSERT INTO fleet_memory (scope, scope_key, fact, op_ref, created_by,
                              supersedes_id, verified_at, expires_at)
    VALUES (p_scope, p_scope_key, p_fact, p_op_ref, p_created_by,
            p_supersedes_id, COALESCE(p_verified_at, now()), p_expires_at)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION
    fleet_memory_insert(text, text, text, text, text, bigint,
                        timestamptz, timestamptz)
    FROM PUBLIC, anon, authenticated, service_role;

COMMIT;

-- ===========================================================================
-- APPLY NOTES — Memory 2.0 item 2A Part 2, op#36768
-- ===========================================================================
-- Verify post-apply:
--   SELECT id, fact, verified_at, expires_at FROM fleet_memory ORDER BY id;
--   -- every pre-existing row must show verified_at = created_at, not now().
--
--   SELECT count(*) FROM fleet_memory_audit;
--   -- on the 2 smoke rows this stays 2 ('created' only). The backfill is
--   -- metadata, not a lifecycle event, and must not masquerade as a retire.
--
-- The hole this closes, proven live-shaped:
--   UPDATE fleet_memory SET expires_at = now() WHERE id = <any>;
--   -- must RAISE (immutable except retire-via-supersede).
--
-- LEDGER: repo=orchestrator, migration_name=139_fleet_memory_facts.sql,
-- silo_ref=tscuymavysscrvoberrr.
-- ===========================================================================
