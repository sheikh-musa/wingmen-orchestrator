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

-- ============================================================
-- SECTION 5: boot_briefing manual_override_bugs section
-- ============================================================
-- Per CAI-PIPELINE-BYPASS-001 AC-3 + CAI-RESP-083 AC-B-7. View extended
-- with one new SELECT branch surfacing rows where manual_override_reason
-- IS NOT NULL — operator-visible audit trail of bypassed bugs.
--
-- DROP + CREATE: PostgreSQL doesn't support ALTER VIEW ADD UNION clause.
-- Existing 8 sections (repo_context, repo_snapshot, active_decision,
-- open_qa_failure, latest_cc_session, latest_digest, last_cai_session,
-- unverified_decisions) preserved verbatim. New section 9 = manual_override_bugs.

DROP VIEW IF EXISTS boot_briefing;

CREATE VIEW boot_briefing AS
 SELECT 'repo_context'::text AS source,
    rc.repo AS key,
    json_build_object('phase', rc.current_phase, 'blockers', rc.blockers, 'test_health', rc.test_health, 'updated_at', rc.updated_at) AS context
   FROM repo_context rc
UNION ALL
 SELECT 'repo_snapshot'::text AS source,
    rs.repo_name AS key,
    json_build_object('commit_sha', "left"(rs.commit_sha, 8), 'commit_timestamp', rs.commit_timestamp, 'branch', rs.branch, 'file_count', rs.file_count, 'total_loc', rs.total_loc, 'test_count', rs.test_count, 'migration_count', rs.migration_count, 'route_count', rs.route_count, 'schema_tables', rs.schema_tables) AS context
   FROM ( SELECT DISTINCT ON (repo_snapshot.repo_name) repo_snapshot.repo_name,
            repo_snapshot.commit_sha,
            repo_snapshot.commit_timestamp,
            repo_snapshot.branch,
            repo_snapshot.file_count,
            repo_snapshot.total_loc,
            repo_snapshot.test_count,
            repo_snapshot.migration_count,
            repo_snapshot.route_count,
            repo_snapshot.schema_tables
           FROM repo_snapshot
          ORDER BY repo_snapshot.repo_name, repo_snapshot.commit_timestamp DESC) rs
UNION ALL
 SELECT 'active_decision'::text AS source,
    sd.decision_ref AS key,
        CASE
            WHEN sd.decided_at >= (now() - '14 days'::interval) THEN json_build_object('title', "left"(sd.title, 80), 'domain', sd.domain, 'category', sd.category, 'repos', sd.repos_affected, 'source', sd.source, 'challenge_status', sd.challenge_status, 'execution_status', sd.execution_status, 'decided_at', sd.decided_at, 'cai_session_id', sd.cai_session_id, 'decision', sd.decision, 'reasoning', sd.reasoning)
            ELSE json_build_object('title', "left"(sd.title, 80), 'domain', sd.domain, 'category', sd.category, 'repos', sd.repos_affected, 'source', sd.source, 'challenge_status', sd.challenge_status, 'execution_status', sd.execution_status, 'decided_at', sd.decided_at, 'cai_session_id', sd.cai_session_id, 'stub_reason', 'older_than_14_days_fetch_full_via_decision_ref')
        END AS context
   FROM strategic_decisions sd
  WHERE sd.status = 'active'::text
UNION ALL
 SELECT 'open_qa_failure'::text AS source,
    (((qf.repo || '/'::text) || qf.role) || '/'::text) || qf.flow AS key,
    json_build_object('role', qf.role, 'flow', qf.flow, 'error', qf.error, 'found_at', qf.found_at) AS context
   FROM qa_findings qf
  WHERE qf.status = 'fail'::text AND qf.resolved_at IS NULL
UNION ALL
 SELECT 'latest_cc_session'::text AS source,
    sub.repo_name AS key,
    json_build_object('narrative', "left"(sub.narrative, 500), 'outcome', sub.outcome, 'commit_sha', sub.commit_sha, 'created_at', sub.created_at) AS context
   FROM ( SELECT DISTINCT ON (cws.repo_name) cws.repo_name,
            cws.narrative,
            cws.outcome,
            cws.commit_sha,
            cws.created_at
           FROM cc_work_sessions cws
          ORDER BY cws.repo_name, cws.created_at DESC) sub
UNION ALL
 SELECT 'latest_digest'::text AS source,
    dig.title AS key,
    json_build_object('topics', dig.topics_covered, 'open_questions', dig.open_questions, 'action_items', dig.action_items, 'session_date', dig.session_date) AS context
   FROM ( SELECT sd.session_date,
            sd.title,
            sd.topics_covered,
            sd.open_questions,
            sd.action_items
           FROM session_digests sd
          ORDER BY sd.created_at DESC
         LIMIT 1) dig
UNION ALL
 SELECT 'last_cai_session'::text AS source,
    lc.cai_session_id AS key,
    json_build_object('cai_session_id', lc.cai_session_id, 'last_decided_at', lc.last_decided_at, 'gap_days', EXTRACT(day FROM now() - lc.last_decided_at)::integer) AS context
   FROM ( SELECT strategic_decisions.cai_session_id,
            max(strategic_decisions.decided_at) AS last_decided_at
           FROM strategic_decisions
          WHERE strategic_decisions.decided_by = 'cai'::text AND strategic_decisions.cai_session_id IS NOT NULL
          GROUP BY strategic_decisions.cai_session_id
          ORDER BY (max(strategic_decisions.decided_at)) DESC
         LIMIT 1) lc
UNION ALL
 SELECT 'unverified_decisions'::text AS source,
    COALESCE(sd.decided_by, 'unknown'::text) AS key,
    json_build_object('count', count(*), 'oldest_decided', min(sd.decided_at), 'newest_decided', max(sd.decided_at)) AS context
   FROM strategic_decisions sd
  WHERE sd.decided_by_verified IS NULL AND sd.status = 'active'::text
  GROUP BY sd.decided_by
UNION ALL
 SELECT 'manual_override_bugs'::text AS source,
    br.id::text AS key,
    json_build_object(
      'repo_name', br.repo_name,
      'status', br.status,
      'override_reason_prefix', "left"(br.manual_override_reason, 80),
      'created_at', br.created_at,
      'resolved_at', br.resolved_at
    ) AS context
   FROM bug_reports br
  WHERE br.manual_override_reason IS NOT NULL;

-- ============================================================
-- SECTION 6: post-apply assertion gate + COMMIT
-- ============================================================
DO $$
BEGIN
  IF (SELECT count(*) FROM information_schema.columns
       WHERE table_name = 'bug_reports'
         AND column_name IN ('verified_at', 'verification_started_at',
                             'verification_diagnostic', 'manual_override_reason',
                             'verification_escalated_at')) <> 5 THEN
    RAISE EXCEPTION 'Option B Section 1 incomplete';
  END IF;
  IF (SELECT count(*) FROM information_schema.columns
       WHERE table_name = 'jobs'
         AND column_name IN ('pr_number', 'branch_name', 'merged_commit_sha')) <> 3 THEN
    RAISE EXCEPTION 'Option B Section 4 incomplete';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'bug_reports_manual_override_reason_chk') THEN
    RAISE EXCEPTION 'Option B Section 3 incomplete';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_views
                  WHERE viewname = 'boot_briefing'
                    AND definition LIKE '%manual_override_bugs%') THEN
    RAISE EXCEPTION 'Option B Section 5 incomplete';
  END IF;
END $$;

COMMIT;
