-- 093_data_provenance_org_id_full_uuid.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Fix (bus #53416, filed by cc-cosem-platform, routed via orch-console #53414):
-- migration 089 seeded data_provenance.org_id as an 8-char PREFIX
-- ('1478c9b2', 'ba98da04') for human-readable brevity, but
-- classify_data_provenance() does an EXACT string match and every real
-- caller queries with the FULL UUID -- cosem-platform's actual org_id
-- column is `UUID NOT NULL` (cosem-platform/AGENTS.md), so that is what any
-- agent reading a real org row and then calling
-- `scripts/data_truth.py classify <project_ref> <org_id>` actually has in
-- hand. A full-UUID query against either of these two registered rows
-- silently fell through to UNCLASSIFIED (fail-safe direction, but noisy --
-- not a registered-row miss) instead of matching, manufacturing a false
-- "per-org row unregistered" report today (bus #53408, corrected #53413)
-- that nearly triggered an unnecessary register write (retracted #53414).
--
-- Fix direction (cc-cosem-platform offered either; this is the chosen one):
-- canonicalize the STORED form to the full UUID, matching the real org_id
-- type everywhere else in the fleet, rather than making
-- classify_data_provenance() prefix-tolerant -- an exact match stays the
-- simplest, least-ambiguous contract for a SECURITY DEFINER classification
-- function, and prefix-tolerant matching would risk a false collision if
-- two orgs ever shared an 8-char UUID prefix. The short 8-char form stays
-- fine in PROSE (docs, bus messages, this file's own comments) for human
-- readability; it is no longer a value anything queries by.
--
-- CHECK constraint added below (enforce in code, not by promise): org_id
-- must now be '' (the store-level-default sentinel, migration 089) or a
-- full UUID -- so a future seed/insert cannot silently reintroduce an
-- 8-char-prefix row and recreate this exact false-UNCLASSIFIED trap.
--
-- IDEMPOTENCE: both UPDATEs key off the OLD short-prefix org_id, so a re-run
-- after the first successful apply matches zero rows (the value is already
-- the full UUID) and is a no-op, not an error. ADD CONSTRAINT has no
-- `IF NOT EXISTS` clause in Postgres, so it is wrapped in a pg_constraint
-- existence check (same DO-block pattern as migrations/084).

update data_provenance
set org_id = '1478c9b2-ff44-4091-a67e-a1391303c4ce',
    updated_at = now()
where project_ref = 'ywrpttpxwfcoodovxhsr' and org_id = '1478c9b2';

update data_provenance
set org_id = 'ba98da04-2a5e-46ba-97f8-387f17753bcc',
    org_name = 'Meridian Training Academy (Synthetic Demo)',
    updated_at = now()
where project_ref = 'ywrpttpxwfcoodovxhsr' and org_id = 'ba98da04';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'data_provenance'::regclass
      AND conname  = 'data_provenance_org_id_full_uuid_or_default'
  ) THEN
    ALTER TABLE data_provenance
      ADD CONSTRAINT data_provenance_org_id_full_uuid_or_default
      CHECK (org_id = '' OR org_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$');
  END IF;
END $$;
