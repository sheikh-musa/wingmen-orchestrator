-- Add is_test flag on bug_reports + skip job spawn for test-shaped intake
--
-- Per Musa 2026-05-07: e2e test pipelines (BAPA Admin Test client +
-- cc-cosem-e2e + cc-ihsanos-e2e) file real-shaped bug_reports against
-- real repos, creating real queued jobs. With ralphy paused these
-- accumulate and queue_stall_detector fires per-job alerts —
-- 5+ noise alarms within an hour (sample 2026-05-07 02:19+).
--
-- Mirror of the strategic_decisions.is_test pattern (PR #27): add the
-- column, backfill known test rows, gate downstream pipeline on it.
-- bug_reports_poll skips creating jobs when is_test=true; queue_stall
-- gets free fix as a side effect (no test jobs queued).
--
-- Three artifacts:
--   1. ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT false
--   2. Backfill: pattern-match reporter_name + description on existing rows
--   3. Cancel currently-queued/paused/pending_review jobs whose bug_report
--      backfilled to is_test=true
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + UPDATE WHERE on patterns.
-- Migration is additive (no DROP / no data loss); qualifies for
-- pre-apply per CAI-RESP-102.

BEGIN;

-- Section 1: add column
ALTER TABLE bug_reports
  ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN bug_reports.is_test IS
  'PR #28 (2026-05-07): true when the bug_report is a synthetic test '
  'fixture (e2e suites, test clients, "please ignore" descriptions). '
  'bug_reports_poll skips job spawn for is_test=true rows so test '
  'pipelines exercise intake without polluting the orchestrator job queue.';


-- Section 2: backfill existing rows
-- Three patterns mark a row as test:
--   - reporter_name ends in "(Test)" → manually-flagged test client
--     (e.g. "BAPA Admin (Test)" — clients.id=6)
--   - reporter_name matches "cc-*-e2e" → automated CC-family e2e suite
--     (e.g. cc-cosem-e2e visual regression, cc-ihsanos-e2e)
--   - description ILIKE "%please ignore%" → explicit operator hint in
--     the bug body (covers cases where reporter is a real human running
--     a one-off test against the pipeline)
UPDATE bug_reports
   SET is_test = true
 WHERE is_test = false  -- only flip false → true; idempotent
   AND (
     reporter_name LIKE '% (Test)'
     OR reporter_name LIKE 'cc-%-e2e'
     OR description ILIKE '%please ignore%'
   );


-- Section 3: cancel currently-active jobs whose bug_report just backfilled
-- to is_test=true. Includes queued / paused / pending_review (the three
-- statuses that imply "ralphy will / would have processed this"). Skips
-- 'running' (in-flight; would orphan the actual ralph subprocess) and
-- 'completed' / 'failed' (terminal — no point cancelling).
UPDATE jobs j
   SET status = 'cancelled',
       result_summary = COALESCE(j.result_summary, '') ||
           ' [auto-cancelled 2026-05-07 — bug_report.is_test=true backfilled '
           'per PR #28; test-pipeline-shaped intake should not consume ralphy slots]',
       updated_at = now()
  FROM bug_reports br
 WHERE br.job_id = j.id
   AND br.is_test = true
   AND j.status IN ('queued', 'paused', 'pending_review');


-- Section 4: assertion gate (fail-loud per CAI-RESP-080 CHALLENGE-1 pattern)
DO $$
DECLARE
    col_exists BOOLEAN;
    test_count INT;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name='bug_reports' AND column_name='is_test'
    ) INTO col_exists;
    IF NOT col_exists THEN
        RAISE EXCEPTION 'is_test column missing after ADD COLUMN';
    END IF;

    SELECT count(*) INTO test_count
      FROM bug_reports WHERE is_test = true;
    -- Sanity: backfill should have flipped at least one historical row
    -- (the cc-cosem-e2e + BAPA Admin Test rows we know exist).
    IF test_count = 0 THEN
        RAISE WARNING 'is_test backfill matched 0 rows — patterns may be wrong (or this is a fresh DB)';
    ELSE
        RAISE NOTICE 'is_test backfill: % rows flagged true', test_count;
    END IF;
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260507110000',
    'bug_reports_is_test',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
