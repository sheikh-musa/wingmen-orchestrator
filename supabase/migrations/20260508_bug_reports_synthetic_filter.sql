-- BUG-PIPELINE-SYNTHETIC-FILTER-001 + CAI-RESP-141
-- Dispatch-time auto-reject filter for synthetic E2E test bug reports.
-- Adds audit columns, backfills historical synthetic rows to status='rejected',
-- extends boot_briefing view with two 24h counter arms.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, UPDATE WHERE excludes already-rejected,
-- CREATE OR REPLACE VIEW. Additive only; qualifies for pre-apply per CAI-RESP-102.

BEGIN;

-- Section 1: audit columns (mirrors resolved_at + verified_at pattern)
ALTER TABLE bug_reports
  ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS rejected_by TEXT;

COMMENT ON COLUMN bug_reports.rejected_at IS
  'When the row was set status=rejected by the synthetic-filter or operator. '
  'Per BUG-PIPELINE-SYNTHETIC-FILTER-001 (2026-05-08).';
COMMENT ON COLUMN bug_reports.rejected_by IS
  'Identity that set status=rejected (e.g. cc-orchestrator-filter, '
  'cc-orchestrator-filter-backfill, or operator). '
  'Per BUG-PIPELINE-SYNTHETIC-FILTER-001 (2026-05-08).';

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260508120000',
    'bug_reports_synthetic_filter',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
