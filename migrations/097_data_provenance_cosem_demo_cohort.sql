-- 097_data_provenance_cosem_demo_cohort.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#27645 gov-PII gate condition 2 (orch-console gate #60195, routed via cc-cosem-convergence-1
-- #60198): the new scripts/lib/require-synthetic-org.ts guard refuses any org with no
-- data_provenance row (fail-safe default = treat_as_real()). scripts/seed-demo-cohort.ts targets a
-- genuinely separate synthetic org (cosem org fa8a5dca, slug cosem-demo-cohort, op#17736 Musa GO
-- agent_messages #36273/#36277) that was never registered -- without this row the new guard would
-- regress an already-working, already-safe script purely because nobody filed the row yet.
--
-- Evidence this org is synthetic: scripts/seed-demo-cohort.ts's own header documents it in full --
-- "94 imaginary Emirati trainees (fake names/military IDs/DOBs)... on a BRAND NEW org, fully separate
-- from the real ADCDA org (1478c9b2) -- zero shared rows, zero cross-org grant". Confirmed live:
-- organizations.id='fa8a5dca-dada-4dc8-bb4a-56219c9ee892' for slug 'cosem-demo-cohort',
-- name='COSEM Demo Cohort (Synthetic)', on project ywrpttpxwfcoodovxhsr (cosem-platform demo/dev,
-- itself already MIXED per migration 089 -- this is the per-org row that narrows it).

insert into data_provenance
  (project_ref, org_id, org_name, alias, classification, evidence, owner, created_by)
values
  ('ywrpttpxwfcoodovxhsr', 'fa8a5dca-dada-4dc8-bb4a-56219c9ee892', 'COSEM Demo Cohort (Synthetic)',
   'cosem org fa8a5dca (slug cosem-demo-cohort)',
   'SYNTHETIC',
   'scripts/seed-demo-cohort.ts (op#17736, Musa GO agent_messages #36273/#36277): brand-new org built '
   'to hold 94 imaginary Emirati trainees for Hariz''s group-split ask, script header documents it as '
   'fully separate from the real ADCDA org (1478c9b2) -- zero shared rows, zero cross-org grant',
   'cosem', 'cc-cosem-platform')
on conflict (project_ref, org_id) do nothing;
