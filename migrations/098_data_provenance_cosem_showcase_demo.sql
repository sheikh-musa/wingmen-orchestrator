-- 098_data_provenance_cosem_showcase_demo.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- P0 containment fix (op#27754, orch-console gate #60436): seed-demo.ts's ORG constant used to
-- target slug 'demo-academy' (the REAL ADCDA org, 1478c9b2) -- a live login as admin@cosem.demo
-- resolved there and was self-reported/contained (#60427/#60430). Fixed seed-demo.ts to target a
-- genuinely new, isolated org/slug ('cosem-showcase-demo', 'COSEM Showcase Academy') instead of
-- reusing either 1478c9b2 or the existing synthetic org (ba98da04, which already has its own
-- canonical persona set and shouldn't get a duplicate one). The new require-synthetic-org.ts guard
-- (PR#344/op#27645) correctly refuses this org until it's registered -- confirmed live: running the
-- fixed script creates the empty org row then refuses before any further write, exactly the
-- fail-closed behavior intended. This migration registers that org id as SYNTHETIC so a second run
-- completes.

insert into data_provenance
  (project_ref, org_id, org_name, alias, classification, evidence, owner, created_by)
values
  ('ywrpttpxwfcoodovxhsr', 'bef77c67-434a-4bab-81cf-99389c4cda43', 'COSEM Showcase Academy',
   'cosem org bef77c67 (slug cosem-showcase-demo)',
   'SYNTHETIC',
   'scripts/seed-demo.ts (op#27754 P0 fix, orch-console gate #60436): brand-new org created by the '
   'fixed script''s own upsert, verified via a live run that it is fully isolated from 1478c9b2 and '
   'ba98da04 -- zero shared rows, zero cross-org grant, same seed-script-header SYNTHETIC-only '
   'guarantee every other entry in this table already relies on',
   'cosem', 'cc-cosem-platform')
on conflict (project_ref, org_id) do nothing;
