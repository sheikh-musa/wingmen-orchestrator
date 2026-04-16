-- TASK-031: Archive tables for completed jobs and terminal decisions
-- Apply via: supabase db push (or psql with the connection string from Supabase dashboard)

-- jobs_archive: mirrors jobs schema + archived_at
-- Retention: 90 days (execution history, not durable record)
CREATE TABLE IF NOT EXISTS jobs_archive (
  id BIGINT PRIMARY KEY,
  repo_name TEXT NOT NULL,
  description TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INT NOT NULL DEFAULT 5,
  fail_count INT NOT NULL DEFAULT 0,
  session_prompt TEXT,
  result_summary TEXT,
  triggered_by TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE jobs_archive ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename='jobs_archive' AND policyname='service role full access'
  ) THEN
    CREATE POLICY "service role full access" ON jobs_archive
      USING (true) WITH CHECK (true);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_jobs_archive_archived ON jobs_archive(archived_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_archive_created  ON jobs_archive(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_archive_repo     ON jobs_archive(repo_name);
CREATE INDEX IF NOT EXISTS idx_jobs_archive_status   ON jobs_archive(status);

-- strategic_decisions_archive: mirrors strategic_decisions schema + archived_at
-- Retention: 365 days minimum (institutional memory — never delete without explicit approval)
CREATE TABLE IF NOT EXISTS strategic_decisions_archive (
  id BIGINT PRIMARY KEY,
  decision_ref TEXT NOT NULL,
  title TEXT NOT NULL,
  decision TEXT,
  reasoning TEXT,
  repos_affected TEXT[],
  source TEXT,
  challenge_status TEXT,
  bypass_review BOOLEAN,
  notified_at TIMESTAMPTZ,
  execution_status TEXT,
  completed_job_id BIGINT,
  completed_at TIMESTAMPTZ,
  category TEXT,
  parent_ref TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE strategic_decisions_archive ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename='strategic_decisions_archive'
      AND policyname='service role full access'
  ) THEN
    CREATE POLICY "service role full access" ON strategic_decisions_archive
      USING (true) WITH CHECK (true);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_sda_ref      ON strategic_decisions_archive(decision_ref);
CREATE INDEX IF NOT EXISTS idx_sda_archived ON strategic_decisions_archive(archived_at DESC);
CREATE INDEX IF NOT EXISTS idx_sda_created  ON strategic_decisions_archive(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sda_category ON strategic_decisions_archive(category)
  WHERE category IS NOT NULL;
