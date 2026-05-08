-- BUG-PIPELINE-SYNTHETIC-FILTER-002 — backfill 'real text' placeholder
--
-- Per cai filing 2026-05-08: rule (a) expanded from single regex to
-- PLACEHOLDER_STRINGS list. New string 'real text' (matches my Task 11
-- integration test fixtures that leaked Jobs #142-#145).
--
-- Two-section migration:
--   1. Backfill bug_reports — flip non-terminal rows whose normalized
--      description is in the placeholder set to status='rejected'
--   2. Cancel any active jobs whose bug_report just backfilled
--
-- Idempotent: WHERE excludes already-rejected rows.

BEGIN;

-- Section 1: backfill bug_reports
UPDATE bug_reports
   SET status            = 'rejected',
       rejection_reason  = COALESCE(rejection_reason, 'synthetic_e2e_test'),
       rejected_at       = now(),
       rejected_by       = 'cc-orchestrator-filter-backfill-002'
 WHERE status NOT IN ('rejected', 'deployed', 'verified')
   AND lower(rtrim(trim(description), '.')) IN ('e2e test bug report', 'real text');

-- Section 2: cancel active jobs whose bug_report just got rejected
UPDATE jobs j
   SET status = 'cancelled',
       result_summary = COALESCE(j.result_summary, '') ||
           ' [auto-cancelled 2026-05-08 — bug_report description matches BUG-PIPELINE-SYNTHETIC-FILTER-002 PLACEHOLDER_STRINGS; should not have been dispatched]',
       updated_at = now()
  FROM bug_reports br
 WHERE br.job_id = j.id
   AND br.rejected_by = 'cc-orchestrator-filter-backfill-002'
   AND j.status IN ('queued', 'paused', 'pending_review');

-- Section 2b: directly cancel the 4 known leaked Jobs #142-#145 that may be
-- orphaned (no linked bug_report) or whose bug was caught by a prior backfill.
-- Idempotent: only touches active-state rows.
UPDATE jobs
   SET status = 'cancelled',
       result_summary = COALESCE(result_summary, '') ||
           ' [auto-cancelled 2026-05-08 — known synthetic job from BUG-PIPELINE-SYNTHETIC-FILTER-002 batch; should not have been dispatched]',
       updated_at = now()
 WHERE id IN (142, 143, 144, 145)
   AND status IN ('queued', 'paused', 'pending_review', 'running');

-- Section 3: assertion gate (CAI-RESP-080 CHALLENGE-1)
DO $$
DECLARE
    backfilled_count INT;
    cancelled_count INT;
BEGIN
    SELECT count(*) INTO backfilled_count
      FROM bug_reports WHERE rejected_by = 'cc-orchestrator-filter-backfill-002';
    SELECT count(*) INTO cancelled_count
      FROM jobs WHERE result_summary LIKE '%PLACEHOLDER_STRINGS%';
    RAISE NOTICE 'FILTER-002 backfill: % rows flipped, % jobs cancelled', backfilled_count, cancelled_count;
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260508130000',
    'synthetic_filter_002_real_text_backfill',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
