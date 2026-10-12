-- 105_data_provenance_tdu_tools.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- hc#359 / fable-audit Phase-0 item 4a (bus #64729, #58432, #58448): applies
-- the two remaining rows from the draft merged in PR #347
-- (reports/data-provenance-draft-20261007/DRAFT.md) -- the draft itself was
-- never applied, only committed as a proposal. The third row it proposed,
-- cosem-adcda-cb6d9, does NOT need a duplicate insert here: fleet-health
-- independently registered it on 2026-10-06 with the same REAL
-- classification (see existing row, created_by='cc-fleet-health').
--
-- Classification CONFIRMED by orch-console, bus #58448 (2026-10-07):
-- "tdu-tools-prod (Firebase): REAL." / "tdu-tools-staging (Firebase): REAL,
-- fail-safe until contents are actually checked."

insert into data_provenance
  (project_ref, org_id, org_name, alias, classification, evidence, owner, created_by)
values
  ('tdu-tools-prod', '', null, 'cosem-tdu app, prod (Firebase)',
   'REAL',
   'docs/data-store-registry.md: "cosem-tdu app (prod, default) — TDU — production", region confirmed op#22426 (2026-09-26); REPOS.json firebase_project=tdu-tools-prod for the cosem-tdu repo; staff/trainee records (faceDescriptor, phoneNumber, idNumber fields per firestore.rules); classification confirmed orch-console bus #58448',
   'cosem', 'cc-substrate'),

  ('tdu-tools-staging', '', null, 'cosem-tdu app, staging (Firebase)',
   'REAL',
   'docs/data-store-registry.md flags this project''s region as "unverified — PERMISSION_DENIED checking region (op#22426, 2026-09-26); do not retry or attempt to bypass, escalate instead." No evidence it holds only synthetic data; classify() defaults unregistered stores to REAL, never to "safe to treat as synthetic". Classification confirmed orch-console bus #58448 ("REAL, fail-safe until contents are actually checked")',
   'cosem', 'cc-substrate')
on conflict (project_ref, org_id) do nothing;
