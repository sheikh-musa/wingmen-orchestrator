-- 130_data_provenance_cosem_test_db.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- op#35285 A2 (orch-console approval #80276; delivered bus #80380/#80384): cc-cosem-platform
-- provisioned a DEDICATED test-only Supabase project (ref nbsvklnxkzmqaznecmsi, ap-southeast-1,
-- Micro, +USD 10/mo) so DB-backed test traffic leaves ywrpttpxwfcoodovxhsr, the MIXED demo/dev
-- store that also holds org 1478c9b2. Synthetic-never-real by construction: schema-only full
-- migration replay, zero org rows at registration, platform TEST_ONLY_SUPABASE_REFS swapped to
-- this ref exclusively (cosem-platform PR#564).
--
-- Store-level row: org_id = '' (NOT null) per 089's convention -- unique(project_ref, org_id)
-- cannot dedupe NULLs, so '' keeps ON CONFLICT idempotent on re-run.
-- Authored by orch-console after the lane's own PR#598 was withdrawn (bus #80381/#80384).

insert into data_provenance
  (project_ref, org_id, org_name, alias, classification, evidence, owner, created_by)
values
  ('nbsvklnxkzmqaznecmsi', '', null,
   '(store-level) (cosem-platform test-only)',
   'SYNTHETIC',
   'dedicated test-only project created 2026-10-29 by cc-cosem-platform under orch-console '
   'approval bus #80276 (+USD 10/mo Micro), delivered bus #80380/#80384: schema-only full replay '
   '(148 migrations, 1 legacy tenant-seed skipped per #80302), 0 org rows at registration, '
   'TEST_ONLY_SUPABASE_REFS swapped to this ref only (sheikh-musa/cosem-platform PR#564); keys '
   'only in the lane worktree .env.local, never CI/Vercel/prod',
   'cosem', 'orch-console')
on conflict (project_ref, org_id) do nothing;
