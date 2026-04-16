-- ARCH-024 Phase 1: Evidence layer — repo_snapshot table + evidence columns on strategic_decisions
--
-- repo_snapshot: structural metadata per commit, written by CC post-push.
-- evidence_* columns on strategic_decisions: required by Gate 2 before flip to implemented.

-- ── repo_snapshot ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS repo_snapshot (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  repo_name        TEXT NOT NULL,
  commit_sha       TEXT NOT NULL,
  commit_timestamp TIMESTAMPTZ NOT NULL,
  branch           TEXT NOT NULL DEFAULT 'main',
  -- Structural metadata (not content)
  total_loc        INT,
  file_count       INT,
  test_count       INT,
  migration_count  INT,
  route_count      INT,          -- Next.js routes / orchestrator endpoints
  top_level_dirs   JSONB,        -- {"src": 850, "tests": 340, ...} files per dir
  recent_churn     JSONB,        -- top 20 files by changes in last 30 days
  dependencies     JSONB,        -- package.json deps + python requirements summary
  schema_tables    TEXT[],       -- tables from supabase/schema.sql
  schema_rls_policies INT,       -- RLS policy count
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE repo_snapshot ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename='repo_snapshot' AND policyname='service role full access'
  ) THEN
    CREATE POLICY "service role full access" ON repo_snapshot USING (true) WITH CHECK (true);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_repo_snapshot_repo_commit
  ON repo_snapshot(repo_name, commit_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_repo_snapshot_sha
  ON repo_snapshot(commit_sha);

-- ── Evidence columns on strategic_decisions ────────────────────────────────────
-- Required by ARCH-023 Gate 2 before flip to challenge_status=implemented.
ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS evidence_commit_sha TEXT;
ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS evidence_deploy_id TEXT;
ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS evidence_test_run_id TEXT;

-- Index for Gate 2 queries (find accepted decisions with evidence)
CREATE INDEX IF NOT EXISTS idx_sd_evidence_commit
  ON strategic_decisions(evidence_commit_sha)
  WHERE evidence_commit_sha IS NOT NULL;
