-- 023_migration_ledger.sql
-- Migration-immutability ledger (CAI-RESP-420, task #50, Deliverable C).
-- The recorded content-hash of each migration AS APPLIED, per silo. The guard
-- (scripts/gates/migration_immutability_guard.py) records a hash on apply and
-- HARD-FAILS if an already-applied migration's file body later differs — banning
-- the in-place amendment that diverged 061 -> 092. Per-silo because the same
-- migration applied to ceayj and goumlyne must be byte-identical (MIGRATION-1).
-- Service-role-only substrate posture. Apply via direct-psycopg (never db push).

CREATE TABLE IF NOT EXISTS migration_ledger (
  repo           TEXT NOT NULL,          -- 'orchestrator' | 'ihsanos' | 'cosem-platform' | ...
  migration_name TEXT NOT NULL,          -- file basename, e.g. '061_...sql'
  silo_ref       TEXT NOT NULL,          -- project ref the migration was applied to
  sha256         TEXT NOT NULL,          -- content hash of the migration file as applied
  applied_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_by     TEXT,
  PRIMARY KEY (repo, migration_name, silo_ref)
);

ALTER TABLE migration_ledger ENABLE ROW LEVEL SECURITY;
-- rls-policy-exempt: migration_ledger select/insert/update/delete (service-role-only substrate table)
DROP POLICY IF EXISTS deny_all_migration_ledger ON migration_ledger;
CREATE POLICY deny_all_migration_ledger ON migration_ledger FOR ALL TO public USING (false);
REVOKE ALL ON migration_ledger FROM PUBLIC, anon, authenticated;
