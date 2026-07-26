-- 034_coordinator_panes_changed_at.sql — make a FROZEN pane detectable, fleet-wide.
-- CAI-RESP-632 (assigned to orch-console). Found by cc-orchestrator's pane-signal inventory.
--
-- ⚠️  UNAPPLIED. Requires a §6.6 grant naming THIS FILENAME **AND ITS CONTENT SHA**
--     (invariant 40: a filename is not an identifier). Apply via the guarded direct-psycopg
--     path with --expect-ref; NEVER `supabase db push` (decision 962). Per CAI-615 the
--     applied-state of this migration is established by PROBING THE OBJECTS
--     (to_regclass / information_schema), NEVER by reading schema_migrations.
--
-- ── THE DEFECT ──────────────────────────────────────────────────────────────
-- `nervous_system/coordinator_pane_publisher.py` upserts every 10s with:
--     ON CONFLICT (agent_id) DO UPDATE
--       SET pane_text = EXCLUDED.pane_text, captured_at = now()
-- `captured_at` is stamped UNCONDITIONALLY — whether or not `pane_text` changed. The
-- console's staleness filter (`console/db.py`, `captured_at > now() - interval ...`) reads
-- `captured_at` alone. So a FROZEN pane — one whose tmux render has stopped repainting while
-- the publisher keeps re-writing identical bytes — is recorded as FRESH, forever, BY
-- CONSTRUCTION. The ">90s = stale" guard is structurally unable to fire on the exact
-- condition it exists to catch.
--
-- Observed cost on 2026-07-26: a false IDLE_UNSENT lane escalation; a governance node
-- rendering `100% context used` for ~30 min after a reset that took it to 21%; and two
-- separate bodies asserting live state off stale pixels, one of them nearly resetting a
-- healthy singleton on the strength of it.
--
-- ── WHY A TRIGGER AND NOT A CASE IN THE UPSERT ──────────────────────────────
-- The obvious fix is a CASE in the publisher's ON CONFLICT clause. That is CALLER-SIDE, and
-- caller-side is precisely the defect class we are closing (invariant 32: the control belongs
-- in the artefact that PERFORMS the act). A CASE protects the one writer we happen to know
-- about; a trigger protects the table from EVERY writer, including future ones and ad-hoc SQL.
-- The publisher then needs no change at all — which is the point: a control nobody has to
-- remember cannot be forgotten.
--
-- ── WHAT `changed_at` MEANS ─────────────────────────────────────────────────
--   captured_at : when we last LOOKED at the pane        (liveness of the PUBLISHER)
--   changed_at  : when the pane last actually CHANGED    (liveness of the BODY)
-- Those are different questions and the fleet has been asking the first while believing it
-- asked the second. Freeze is then a plain SQL fact:
--     now() - changed_at  >  interval '<threshold>'   AND   captured_at is fresh
--       => the publisher is alive AND the pane is frozen.
-- Note the asymmetry, and state it wherever this is consumed: a STATIC pane is only evidence
-- of freeze if the render ASSERTS ACTIVITY. An idle body legitimately does not repaint, so
-- `changed_at` being old is not by itself a fault — it is the input to that judgement, not
-- the judgement.
--
-- Idempotent: IF NOT EXISTS / CREATE OR REPLACE throughout. Additive; no reads change
-- behaviour until a consumer opts in.

BEGIN;

-- 1. The column. NULLable by design: NULL means "never observed to change since the column
--    existed", which is honestly different from "changed long ago".
ALTER TABLE public.coordinator_panes
  ADD COLUMN IF NOT EXISTS changed_at TIMESTAMPTZ;

COMMENT ON COLUMN public.coordinator_panes.changed_at IS
  'When pane_text last actually CHANGED, maintained by trigger trg_coordinator_panes_changed_at. Distinct from captured_at, which is when we last LOOKED. captured_at proves the PUBLISHER is alive; changed_at proves the BODY is. A frozen pane keeps captured_at fresh forever (the publisher rewrites identical bytes every 10s) — changed_at is the only column that can reveal it. CAI-RESP-632. NOTE: a static pane is evidence of FREEZE only when the render asserts activity; an idle body legitimately does not repaint.';

-- 2. Backfill. `captured_at` is the best available lower bound and is NOT a true changed_at:
--    for an already-frozen row it will overstate freshness. Deliberate — a NULL here would be
--    indistinguishable from "no data" for consumers, and the value self-corrects on the first
--    real change. Recorded so nobody later reads backfilled rows as measured.
UPDATE public.coordinator_panes
   SET changed_at = captured_at
 WHERE changed_at IS NULL;

-- 3. The guard, in the table, so no writer can omit it.
CREATE OR REPLACE FUNCTION public.coordinator_panes_stamp_changed_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    -- A first sighting is a change by definition.
    NEW.changed_at := COALESCE(NEW.changed_at, now());
    RETURN NEW;
  END IF;

  -- IS DISTINCT FROM, not <>: NULL-safe. A pane going NULL->text or text->NULL is a change,
  -- and `<>` would evaluate NULL and silently leave changed_at untouched — the same
  -- fail-quiet shape this migration exists to remove.
  IF NEW.pane_text IS DISTINCT FROM OLD.pane_text THEN
    NEW.changed_at := now();
  ELSE
    -- THE LOAD-BEARING LINE. Identical bytes must NOT advance changed_at, even though the
    -- publisher is advancing captured_at on the same statement. Without this the new column
    -- reproduces the original defect one column to the left.
    NEW.changed_at := OLD.changed_at;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_coordinator_panes_changed_at ON public.coordinator_panes;
CREATE TRIGGER trg_coordinator_panes_changed_at
  BEFORE INSERT OR UPDATE ON public.coordinator_panes
  FOR EACH ROW EXECUTE FUNCTION public.coordinator_panes_stamp_changed_at();

COMMIT;

-- ── POSITIVE CONTROL (CAI-632 hard condition) ───────────────────────────────
-- The condition is NOT "the column exists and populates". It is "a FROZEN pane is DETECTED
-- as frozen". Run BOTH legs; a one-legged test cannot tell this fix from the defect:
--
--   BEGIN;
--   INSERT INTO coordinator_panes (agent_id, pane_text, captured_at)
--        VALUES ('__control__','AAA', now())
--     ON CONFLICT (agent_id) DO UPDATE
--        SET pane_text = EXCLUDED.pane_text, captured_at = now();
--   SELECT captured_at, changed_at FROM coordinator_panes WHERE agent_id='__control__';
--
--   -- LEG 1 (the frozen case — MUST NOT advance changed_at):
--   -- same bytes, exactly as the publisher would rewrite them
--   INSERT INTO coordinator_panes (agent_id, pane_text, captured_at)
--        VALUES ('__control__','AAA', now())
--     ON CONFLICT (agent_id) DO UPDATE
--        SET pane_text = EXCLUDED.pane_text, captured_at = now();
--   -- EXPECT: captured_at ADVANCED, changed_at UNCHANGED  <= freeze is now detectable
--
--   -- LEG 2 (the live case — MUST advance changed_at):
--   INSERT INTO coordinator_panes (agent_id, pane_text, captured_at)
--        VALUES ('__control__','BBB', now())
--     ON CONFLICT (agent_id) DO UPDATE
--        SET pane_text = EXCLUDED.pane_text, captured_at = now();
--   -- EXPECT: BOTH advanced  <= the instrument can still say "changed"
--   ROLLBACK;
--
-- Leg 2 is what makes leg 1 mean anything: a trigger that never advanced changed_at would
-- pass leg 1 and be useless. Both legs, one transaction, rolled back.
