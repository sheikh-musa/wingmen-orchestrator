-- 132_operator_asks_outbound.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#35246, console bus #80707/#80718/#80719/#80736.
--
-- Migration 085's operator_asks_committed_date_needs_outbound_chk CHECK only
-- proves outbound_msg_id IS NOT NULL -- it never proves the id names a REAL
-- outbound send. Lanes, in good faith, set it to their own agent_messages
-- (bus) row ids (80678, 80684, 76253), which are not operator_messages rows
-- at all; the ledger then claimed Musa was told a date he was never actually
-- sent. fleet-health found 40 of 1236 linked asks with a non-operator link
-- (6 open and dated at the time; all 6 since cleared with reversal records,
-- bus #80704/#80736 -- verified 0 remain before this migration applies).
--
-- This closes the gap with a trigger (a CHECK/FK can't express "must be a row
-- in operator_messages with direction='outbound'"): BEFORE INSERT OR UPDATE
-- OF outbound_msg_id, committed_date, firing only on an OPEN row
-- (closed_at IS NULL) with a committed_date actually set -- so the 34
-- existing closed historical rows (already closed before this migration)
-- never block the apply, and clearing BOTH columns to NULL together (how
-- fleet-health lifts a bad link, bus #80736) is always allowed since there
-- is then nothing to check.
--
-- SECURITY DEFINER + pinned search_path (same hardening as migration 131's
-- mirror functions), EXECUTE revoked from PUBLIC/anon/authenticated so this
-- can't be called directly as an RPC bypass -- firing via the normal table
-- trigger needs no EXECUTE grant at all, only ordinary DML on operator_asks.
--
-- scripts/asks_triage.py (this same PR) adds the client-side mirror of this
-- check (clearer error before even attempting the write) plus two more
-- checks console asked for (bus #80718, corrected #80719): the outbound
-- message must POSTdate the ask's own creation (a message from before the
-- ask existed cannot have committed its date), and the committed date
-- should visibly appear in the outbound text (warn, not refuse, on that
-- one -- a date can be stated in a form we don't recognize).
--
-- Apply via scripts/apply_migration.py 132 --silo tscuymavysscrvoberrr
-- (direct psycopg-apply; decision 962 -- NEVER supabase db push against this
-- substrate).

-- assert: search_path public.operator_asks_outbound_msg_trg()
-- assert: no_execute anon public.operator_asks_outbound_msg_trg()
-- assert: no_execute authenticated public.operator_asks_outbound_msg_trg()

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION operator_asks_outbound_msg_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    -- Only an OPEN row is checked; a historical closed row (34 of them,
    -- predating this migration) is never touched by it, even if later
    -- UPDATEd for an unrelated reason.
    IF NEW.closed_at IS NOT NULL THEN
        RETURN NEW;
    END IF;

    -- Clearing both columns together is how fleet-health lifts a bad link
    -- (console bus #80736) -- nothing to check when there is no commitment.
    IF NEW.committed_date IS NULL THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM operator_messages
         WHERE id = NEW.outbound_msg_id AND direction = 'outbound'
    ) THEN
        RAISE EXCEPTION 'operator_asks.outbound_msg_id % is not a real outbound '
            'operator_messages row', NEW.outbound_msg_id;
    END IF;

    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION operator_asks_outbound_msg_trg() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS operator_asks_outbound_msg_chk ON operator_asks;
CREATE TRIGGER operator_asks_outbound_msg_chk
    BEFORE INSERT OR UPDATE OF outbound_msg_id, committed_date ON operator_asks
    FOR EACH ROW EXECUTE FUNCTION operator_asks_outbound_msg_trg();

COMMIT;
