-- 129_data_provenance_cosem_test_db.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#35285 A2 (orch-console approval #80276, 2026-10-29): the cosem-platform lane provisioned a
-- DEDICATED test-only Supabase project (ref nbsvklnxkzmqaznecmsi, Micro, +$10/mo) so DB-backed
-- test traffic leaves ywrpttpxwfcoodovxhsr -- a MIXED store that also holds the real academy org
-- (1478c9b2). Store is synthetic-never-real by construction: schema is a full migration replay
-- (scripts/dev/provision-test-db.mjs, 148 files applied, the one legacy tenant-data seed
-- disclose-skipped per #80302), it held ZERO org rows at registration, and the platform's
-- TEST_ONLY_SUPABASE_REFS allowlist now points at it exclusively. Registered before any test
-- write beyond the lane's own verification suites (which mint their own synthetic orgs through
-- the org-guard).

insert into data_provenance
  (project_ref, org_id, org_name, alias, classification, evidence, owner, created_by)
values
  ('nbsvklnxkzmqaznecmsi', null, 'cosem-platform test-only store',
   '(store-level) (cosem-platform test-only)',
   'SYNTHETIC',
   'dedicated test-only project created 2026-10-29 under orch-console approval #80276 (+$10/mo '
   'Micro -- taking tests off the MIXED store that holds the real academy org); schema = '
   'provision-test-db.mjs full replay (148 migrations, 1 legacy tenant-seed skipped per #80302); '
   '0 org rows at registration; platform test allowlist (TEST_ONLY_SUPABASE_REFS) swapped to '
   'this ref only, PR sheikh-musa/cosem-platform#564',
   'cosem', 'cc-cosem-platform')
on conflict (project_ref, org_id) do nothing;
