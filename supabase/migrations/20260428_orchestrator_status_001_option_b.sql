-- ORCHESTRATOR-STATUS-001 Option B: verification worker schema
-- Per CAI-RESP-083 (CHALLENGE-1 resolution) + CAI-PIPELINE-BYPASS-001
-- (manual_override_reason fold-in) + cc-cosem #955 ownership boundary.
--
-- Sections:
--   1. bug_reports new columns (verified_at, verification_started_at,
--      verification_diagnostic, manual_override_reason, verification_escalated_at)
--   2. bug_reports.status CHECK expansion (pr_open, push_failed, pr_failed)
--   3. bug_reports manual_override_reason CHECK constraint
--      (status='deployed' → manual_override_reason IS NULL OR length≥20)
--   4. jobs columns (pr_number, branch_name, merged_commit_sha)
--   5. boot_briefing manual_override_bugs section
--   6. Post-apply assertion gate
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + DO-block guards on constraints.
-- Pattern matches Batch 2 BUG-030 idempotency restructure (per CAI-RESP-081
-- review of the schema_migrations re-apply path).
--
-- Parent decisions: ORCHESTRATOR-STATUS-001, CAI-RESP-083, CAI-PIPELINE-BYPASS-001.

BEGIN;

-- ============================================================
-- SECTION 1: bug_reports new columns (Option B verifier state)
-- ============================================================
ALTER TABLE bug_reports
  ADD COLUMN IF NOT EXISTS verified_at              TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS verification_started_at  TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS verification_diagnostic  TEXT NULL,
  ADD COLUMN IF NOT EXISTS manual_override_reason   TEXT NULL,
  ADD COLUMN IF NOT EXISTS verification_escalated_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN bug_reports.verified_at IS
  'CAI-RESP-083: timestamp when Option B verifier confirmed deploy. NULL = not yet verified or never reaches verifier (manual_override path).';
COMMENT ON COLUMN bug_reports.verification_started_at IS
  'CAI-RESP-083: timestamp of first verifier tick on this bug. Used for PR-open 24h timeout (CASE 2) and CASE 1 fallback.';
COMMENT ON COLUMN bug_reports.verification_diagnostic IS
  'CAI-RESP-083: human-readable diagnostic on escalation (e.g., "firebase-degraded", "pr-open-too-long", "no-pr-no-sha"). NULL on happy path.';
COMMENT ON COLUMN bug_reports.manual_override_reason IS
  'CAI-PIPELINE-BYPASS-001: operator-authorized bypass reason (>=20 chars trimmed). Verifier predicate excludes rows where this is set.';
COMMENT ON COLUMN bug_reports.verification_escalated_at IS
  'CAI-RESP-083 CHALLENGE-4: tombstone for already-escalated rows. Worker predicate excludes IS NOT NULL to prevent infinite P1 spam loop.';

-- ============================================================
-- SECTION 2: bug_reports.status CHECK expansion (AC-B-9)
-- Adds pr_open / push_failed / pr_failed to the allowed set so
-- agent-driven push/PR-open paths can transition status without
-- the verifier rejecting the row at write time.
-- ============================================================
ALTER TABLE bug_reports DROP CONSTRAINT IF EXISTS bug_reports_status_check;
ALTER TABLE bug_reports ADD CONSTRAINT bug_reports_status_check
  CHECK (status = ANY(ARRAY[
    'new'::text,
    'diagnosing'::text,
    'proposed'::text,
    'approved'::text,
    'deploying'::text,
    'deployed'::text,
    'verified'::text,
    'rejected'::text,
    'escalated'::text,
    'still_broken'::text,
    'pr_open'::text,
    'push_failed'::text,
    'pr_failed'::text
  ]));

-- ============================================================
-- SECTION 3: bug_reports.manual_override_reason CHECK constraint
-- (CAI-PIPELINE-BYPASS-001 AC-1)
-- Enforces: when status='deployed', manual_override_reason must
-- either be NULL (normal verified-by-worker path) OR be at least
-- 20 chars after trimming whitespace (operator-authorized bypass
-- with audit-grade reasoning). Prevents 1-char "x" bypasses.
-- ============================================================
ALTER TABLE bug_reports DROP CONSTRAINT IF EXISTS bug_reports_manual_override_reason_chk;
ALTER TABLE bug_reports ADD CONSTRAINT bug_reports_manual_override_reason_chk
  CHECK (
    status <> 'deployed'
    OR manual_override_reason IS NULL
    OR length(trim(manual_override_reason)) >= 20
  );

-- ============================================================
-- SECTION 4: jobs columns for publisher (cc-cosem write) + verifier cache
-- ============================================================
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS pr_number         INT NULL,
  ADD COLUMN IF NOT EXISTS branch_name       TEXT NULL,
  ADD COLUMN IF NOT EXISTS merged_commit_sha TEXT NULL;

COMMENT ON COLUMN jobs.pr_number IS
  'cc-cosem Option C publisher writes this from publish_job_commit. Used by Option B verifier as input to gh pr view --json mergeCommit.';
COMMENT ON COLUMN jobs.branch_name IS
  'cc-cosem Option C publisher writes this. Diagnostic + fallback for manual debugging.';
COMMENT ON COLUMN jobs.merged_commit_sha IS
  'CAI-RESP-083: Option B verifier cache. Worker fetches via gh pr view per tick on first CASE 3 observation; subsequent ticks reuse to avoid duplicate API calls.';
