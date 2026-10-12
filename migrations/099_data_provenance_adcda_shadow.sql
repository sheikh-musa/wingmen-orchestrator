-- 099_data_provenance_adcda_shadow.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#28779 item3 (Week-1 shadow-parity push, orch-console ruling bus #62254/#62358, relayed via
-- cc-cosem-convergence-1): registers a brand-new, dedicated SYNTHETIC tenant for the ADCDA shadow
-- work -- deliberately NOT ba98da04 (clean isolation from whatever else runs there concurrently;
-- matches the real-cutover plan's own "dedicated new org" shape, op#28795) and NEVER 1478c9b2 (the
-- real client org, off-limits per AGENTS.md / cai-1340).
--
-- scripts/seed-adcda-shadow.ts upserted the org row (slug 'adcda-shadow'), then require-synthetic-
-- org.ts correctly refused before any roster write -- confirmed live, same fail-closed behavior
-- 098's own precedent describes. This migration registers that org id as SYNTHETIC so a second run
-- of the seed script completes.

insert into data_provenance
  (project_ref, org_id, org_name, alias, classification, evidence, owner, created_by)
values
  ('ywrpttpxwfcoodovxhsr', 'fdb900db-63e0-4e81-bf91-0bc952db86ff', 'ADCDA Shadow (Synthetic)',
   'cosem org fdb900db (slug adcda-shadow)',
   'SYNTHETIC',
   'scripts/seed-adcda-shadow.ts (op#28779 item3): brand-new org created by the script''s own '
   'upsert, zero pre-existing rows, deterministic synthetic roster only (no real PII, no askariyah/ '
   'login path touched) -- same SYNTHETIC-only guarantee every other entry in this table already '
   'relies on, orch-console ruling bus #62254/#62358',
   'cosem', 'cc-cosem-platform')
on conflict (project_ref, org_id) do nothing;
