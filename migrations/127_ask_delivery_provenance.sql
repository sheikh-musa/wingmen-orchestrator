-- 127_ask_delivery_provenance.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: no_table_privilege anon public.deploy_events SELECT
-- assert: no_table_privilege authenticated public.deploy_events SELECT
-- assert: no_table_privilege anon public.deploy_events INSERT
-- assert: no_table_privilege authenticated public.deploy_events INSERT
-- assert: no_table_privilege anon public.deploy_events UPDATE
-- assert: no_table_privilege authenticated public.deploy_events UPDATE
-- assert: no_sequence_privilege anon public.deploy_events_id_seq USAGE
-- assert: no_sequence_privilege authenticated public.deploy_events_id_seq USAGE
-- assert: search_path public.operator_asks_delivery_provenance()
-- assert: no_execute anon public.operator_asks_delivery_provenance()
-- assert: no_execute authenticated public.operator_asks_delivery_provenance()
--
-- Automatic, code-enforced ask-to-deploy delivery stamping, step 1 of 7
-- (Musa op#34919; design reports/op34919-delivery-stamping-design-20261027.md
-- in cc-fleet-health's home; console rulings #78610).
--
-- WHY: operator_asks.delivered_at (mig121) is stamped only when someone
-- remembers to run `asks_triage.py --delivered`, so 12 of 3,420 ask/done rows
-- in the last 30 days carry it. Turnaround can't be measured on that.
--
-- WHAT (purely additive; nothing reads or writes these until later steps):
--  * deploy_events: one row per LIVE-VERIFIED deploy (surface + sha), written by
--    fleet-side collectors only (deploy_console.sh, the GH Actions / Vercel
--    collector). The stamper links deploy -> commits -> PRs -> op#/ask# -> asks.
--  * operator_asks provenance for delivered_at:
--      delivered_source       auto-deploy | auto-outbound | bus-report | manual
--      delivered_evidence_ref what proves it (deploy_events:<id> PR#n, an
--                             operator_messages id, a bus id ...)
--      delivered_backfilled   true when stamped by the 30-day backfill
--      no_deliverable_reason  set when an ask is closed with nothing to deliver
--                             (excluded from turnaround)
--  * Enforced in the DB, so no writer can skip it:
--      - any non-manual stamp must name its evidence;
--      - bus-report evidence is BACKFILL-ONLY (console ruling 3, #78610: bus
--        reports are lower-grade evidence, never used for new stamps);
--      - a stamp with no source (today's asks_triage.py --delivered, and any
--        hand-written UPDATE) is recorded as 'manual' by trigger, so the old
--        writer keeps working and nothing is ever stamped without provenance.
--  * The 15 rows already stamped before this migration are marked
--    'manual' with evidence 'pre-mig127'; their times are left untouched.
--
-- NOT here: the close-time gate on triage_state='done' (step 2, its own PR,
-- 48h log-only soak first per ruling 4).
--
-- Applied via scripts/apply_migration.py (direct psycopg, same-transaction
-- ledger write) -- NEVER `supabase db push` (decision 962).

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.deploy_events (
  id            bigserial   PRIMARY KEY,
  surface       text        NOT NULL,   -- e.g. fleet-console, cosem-adcda, ihsanos, ihsanos-irsyad, orchestrator
  repo          text        NOT NULL,   -- owner/name
  sha           text        NOT NULL,   -- the commit that is now live
  prev_sha      text,                   -- the previously-live commit (the stamper diffs prev..sha)
  verified_at   timestamptz NOT NULL,   -- when the deploy was verified LIVE (this becomes delivered_at)
  verify_kind   text        NOT NULL,   -- how it was verified, e.g. api-version, gh-run-success, vercel-ready
  evidence_ref  text,                   -- run url / deploy id / log line
  collected_by  text        NOT NULL,   -- which collector wrote it
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT deploy_events_surface_sha_key UNIQUE (surface, sha)
);
CREATE INDEX IF NOT EXISTS deploy_events_verified_idx ON public.deploy_events (verified_at);

ALTER TABLE public.deploy_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS deploy_events_service_only ON public.deploy_events;
CREATE POLICY deploy_events_service_only ON public.deploy_events
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS deploy_events_console_ro ON public.deploy_events;
CREATE POLICY deploy_events_console_ro ON public.deploy_events
  FOR SELECT TO console_readonly USING (true);
REVOKE ALL ON public.deploy_events FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.deploy_events_id_seq FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.deploy_events TO console_readonly;
GRANT ALL ON public.deploy_events TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.deploy_events_id_seq TO service_role;

ALTER TABLE public.operator_asks ADD COLUMN IF NOT EXISTS delivered_source       text;
ALTER TABLE public.operator_asks ADD COLUMN IF NOT EXISTS delivered_evidence_ref text;
ALTER TABLE public.operator_asks ADD COLUMN IF NOT EXISTS delivered_backfilled   boolean NOT NULL DEFAULT false;
ALTER TABLE public.operator_asks ADD COLUMN IF NOT EXISTS no_deliverable_reason  text;

-- Rows stamped before provenance existed: honest label, times untouched.
UPDATE public.operator_asks
   SET delivered_source = 'manual',
       delivered_evidence_ref = coalesce(delivered_evidence_ref, 'pre-mig127')
 WHERE delivered_at IS NOT NULL AND delivered_source IS NULL;

-- Unattributed stamps (no source given) are recorded as 'manual'.
CREATE OR REPLACE FUNCTION public.operator_asks_delivery_provenance()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF NEW.delivered_at IS NOT NULL AND NEW.delivered_source IS NULL THEN
    NEW.delivered_source := 'manual';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.operator_asks_delivery_provenance() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS operator_asks_delivery_provenance_trg ON public.operator_asks;
CREATE TRIGGER operator_asks_delivery_provenance_trg
  BEFORE INSERT OR UPDATE OF delivered_at, delivered_source ON public.operator_asks
  FOR EACH ROW EXECUTE FUNCTION public.operator_asks_delivery_provenance();

ALTER TABLE public.operator_asks DROP CONSTRAINT IF EXISTS operator_asks_delivered_source_chk;
ALTER TABLE public.operator_asks ADD CONSTRAINT operator_asks_delivered_source_chk
  CHECK (delivered_source IS NULL
         OR delivered_source IN ('auto-deploy', 'auto-outbound', 'bus-report', 'manual'));

ALTER TABLE public.operator_asks DROP CONSTRAINT IF EXISTS operator_asks_delivered_needs_source_chk;
ALTER TABLE public.operator_asks ADD CONSTRAINT operator_asks_delivered_needs_source_chk
  CHECK (delivered_at IS NULL OR delivered_source IS NOT NULL);

ALTER TABLE public.operator_asks DROP CONSTRAINT IF EXISTS operator_asks_delivered_evidence_chk;
ALTER TABLE public.operator_asks ADD CONSTRAINT operator_asks_delivered_evidence_chk
  CHECK (delivered_source IS NULL OR delivered_source = 'manual' OR delivered_evidence_ref IS NOT NULL);

ALTER TABLE public.operator_asks DROP CONSTRAINT IF EXISTS operator_asks_bus_evidence_backfill_only_chk;
ALTER TABLE public.operator_asks ADD CONSTRAINT operator_asks_bus_evidence_backfill_only_chk
  CHECK (delivered_source IS DISTINCT FROM 'bus-report' OR delivered_backfilled);

COMMENT ON TABLE public.deploy_events IS
  'One row per LIVE-verified deploy (op#34919). Fleet-side collectors write it; '
  'ask_delivery_stamper links it to operator_asks via PR op#/ask# and stamps delivered_at = verified_at.';
COMMENT ON COLUMN public.operator_asks.delivered_source IS
  'op#34919: how delivered_at was established. bus-report is backfill-only (ruling #78610).';

COMMIT;
