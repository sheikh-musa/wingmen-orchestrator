-- 134_work_item_ask_chase_by_due_at.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#35246, console bus #81038/#81040. The governance_floor reconciler's
-- first-ever scheduled run (op#32730: no work_item sits abandoned past 72h
-- without a future due_at) found 4 false positives: operator_asks.chase_by
-- (a deliberate "come back to this later" deferral date) was never
-- propagated into work_item.due_at by the mirror -- only committed_date was
-- (work_item_derive_ask(), migration 114). An ask with a real future
-- chase_by and no committed_date yet (the normal case: committed_date
-- requires a real outbound send per migration 132) mirrored with due_at
-- NULL, so the floor saw it as unbounded and flagged it as abandoned even
-- though it was correctly deferred.
--
-- Fix is in work_item_mirror_ask_trg()'s BODY only -- the call site now
-- passes GREATEST(NEW.committed_date, NEW.chase_by) where it used to pass
-- NEW.committed_date alone (Postgres GREATEST ignores NULL arguments unless
-- all are NULL, so this is NULL only when both source dates are). This
-- deliberately does NOT change work_item_derive_ask()'s signature: adding a
-- parameter there would create a second overload alongside the old one
-- (exactly the work_item_derive_bus 6-arg/7-arg dead-overload bug console
-- flagged on migration 131, bus #80630) -- a pure body-only change on the
-- trigger function avoids that class of bug entirely.
--
-- Re-asserts the SECURITY DEFINER + pinned search_path migration 131 set on
-- this function via ALTER FUNCTION (bus #80555): CREATE OR REPLACE
-- redefines a function's properties from what THIS statement says, not from
-- what an earlier ALTER FUNCTION set, so the clause must be restated here
-- or the hardening silently reverts to SECURITY INVOKER with no search_path
-- pin. Ownership and grants (the EXECUTE revoke) do survive a REPLACE
-- unchanged, but the REVOKE is restated anyway for this migration to be
-- fully self-contained and independently assert-checkable.
--
-- Also backfills due_at for every ALREADY-mirrored 'ask' work_item row so
-- the fix takes effect immediately, not just on the next operator_asks
-- write: ONLY due_at changes, updated_at is deliberately left untouched so
-- a row that is genuinely part of the 138-row backlog (console bus #81038)
-- does not look freshly-touched -- this is a correction of a wrong value,
-- not a mass edit to make the floor pass.
--
-- Apply via scripts/apply_migration.py 134 --silo tscuymavysscrvoberrr
-- (direct psycopg-apply; decision 962 -- NEVER supabase db push).

-- assert: search_path public.work_item_mirror_ask_trg()
-- assert: no_execute anon public.work_item_mirror_ask_trg()
-- assert: no_execute authenticated public.work_item_mirror_ask_trg()

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION work_item_mirror_ask_trg() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    -- The mirror must NEVER be able to block the system of record (orch-console review,
    -- bus #73195): an exception in here degrades to "work_item missing/stale for this one
    -- row, surfaced loudly", never an aborted operator_asks write. The 2026-10-24
    -- equivalence proof is the drift backstop that catches anything skipped this way.
    BEGIN
        -- GREATEST(committed_date, chase_by): due_at is "don't flag me as
        -- abandoned before this", and either date can independently express
        -- that (op#35246, bus #81038/#81040). GREATEST ignores a NULL
        -- argument unless both are NULL.
        PERFORM work_item_mirror('ask', 'operator_asks', NEW.id,
            work_item_derive_ask(NEW.id, NEW.ask, NEW.triage_state, NEW.closed_at,
                                  NEW.delegated_to, GREATEST(NEW.committed_date, NEW.chase_by),
                                  NEW.triaged_by),
            'trigger:operator_asks');
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'work_item mirror failed for operator_asks id=%: %', NEW.id, SQLERRM;
    END;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION work_item_mirror_ask_trg() FROM PUBLIC, anon, authenticated;

-- Backfill: due_at only, updated_at untouched, and only where the corrected
-- value actually differs (idempotent re-apply is then a true no-op).
UPDATE work_item wi
   SET due_at = GREATEST(oa.committed_date, oa.chase_by)
  FROM operator_asks oa
 WHERE wi.source_table = 'operator_asks'
   AND wi.source_id = oa.id
   AND wi.due_at IS DISTINCT FROM GREATEST(oa.committed_date, oa.chase_by);

COMMIT;
