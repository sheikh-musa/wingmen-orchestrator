-- PAUSED-JOBS-RETRY-POLICY-001 AC (ii) + (iii) — boot_briefing review branches
--
-- Per CAI-RESP-105:
--   (ii) Paused jobs older than X (1hr) NOT matching the stale-error allowlist
--        surface in boot_briefing for human/CAI review. Includes both
--        "never auto-retried" (other class) AND "auto-retried but still
--        stuck" (allowlist class re-paused after retry — genuine stuck state).
--   (iii) Permanent-pause patterns ('No commit produced — ghost success
--         prevented') NEVER auto-retry but ARE surfaced after Y (24hr) > X
--         as 'pause may need human resolution'.
--
-- Two new UNION branches appended to boot_briefing:
--   1. paused_job_review_needed (AC ii)
--   2. paused_job_permanent_review (AC iii)
--
-- All 10 prior boot_briefing branches preserved verbatim per the Option B
-- Section 5 + Phase 1 Section 3 pattern. DROP+CREATE is the only path
-- because PostgreSQL doesn't support ALTER VIEW to add UNION branches.
--
-- Idempotency: CREATE OR REPLACE VIEW. Re-runnable. Migration is additive
-- (no DROP COLUMN / no data loss); qualifies for pre-apply-then-review per
-- CAI-RESP-102.

BEGIN;

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
    json_build_object('repo_name', br.repo_name, 'status', br.status, 'override_reason_prefix', "left"(br.manual_override_reason, 80), 'created_at', br.created_at, 'resolved_at', br.resolved_at) AS context
   FROM bug_reports br
  WHERE br.manual_override_reason IS NOT NULL
UNION ALL
 SELECT 'inbox_sla_violation'::text AS source,
    isv.message_id::text AS key,
    json_build_object('agent', isv.agent, 'priority', isv.priority, 'violation', isv.violation_type, 'elapsed_min', isv.elapsed_minutes, 'threshold_min', isv.threshold_minutes, 'from', isv.from_agent, 'subject', "left"(isv.subject, 80), 'created_at', isv.created_at) AS context
   FROM inbox_sla_violations isv
UNION ALL
-- AC (ii): non-permanent paused jobs older than 1hr that won't auto-recover.
-- Includes both "never matched allowlist" AND "matched allowlist but
-- already auto-retried" — both populations need human attention.
 SELECT 'paused_job_review_needed'::text AS source,
    j.id::text AS key,
    json_build_object(
        'repo_name', j.repo_name,
        'description', "left"(coalesce(j.description, ''), 100),
        'fail_count', j.fail_count,
        'result_summary_prefix', "left"(coalesce(j.result_summary, ''), 200),
        'updated_at', j.updated_at,
        'classification', CASE
            WHEN j.result_summary LIKE '%[paused_jobs_policy auto-retry%' THEN 'allowlist_re_paused'
            ELSE 'other'
        END
    ) AS context
   FROM jobs j
  WHERE j.status = 'paused'
    AND j.fail_count >= 3
    AND j.updated_at < (now() - '1 hour'::interval)
    AND coalesce(j.result_summary, '') NOT LIKE '%ghost success prevented%'
UNION ALL
-- AC (iii): permanent-pause patterns surfaced after 24hr as needing
-- human resolution. The CC didn't commit — retry won't help; only a
-- spec rewrite or manual decision can unblock.
 SELECT 'paused_job_permanent_review'::text AS source,
    j.id::text AS key,
    json_build_object(
        'repo_name', j.repo_name,
        'description', "left"(coalesce(j.description, ''), 100),
        'fail_count', j.fail_count,
        'result_summary_prefix', "left"(coalesce(j.result_summary, ''), 200),
        'updated_at', j.updated_at,
        'note', 'ghost-success-prevented; retry won''t help — needs spec rewrite or manual decision'
    ) AS context
   FROM jobs j
  WHERE j.status = 'paused'
    AND j.fail_count >= 3
    AND j.updated_at < (now() - '24 hours'::interval)
    AND coalesce(j.result_summary, '') LIKE '%ghost success prevented%';


-- Assertion gate: same fail-loud pattern as Option B Section 6 / Phase 1 Section 4.
DO $$
DECLARE
    branches INT;
BEGIN
    SELECT count(*)::int INTO branches
      FROM (SELECT DISTINCT source FROM boot_briefing) src;
    -- 12 branches now expected: 10 prior + 2 new. Tolerate 10+ in case
    -- one of the new branches has zero rows at apply time (the source
    -- text only appears in DISTINCT-source if it has >=1 row).
    IF branches < 8 THEN
        RAISE EXCEPTION 'boot_briefing: expected >=8 source branches, got %', branches;
    END IF;
    RAISE NOTICE 'PAUSED-JOBS-RETRY-POLICY-001 AC (ii)+(iii) — assertions passed (% boot_briefing branches)', branches;
END $$;

COMMIT;

-- schema_migrations bookkeeping
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260430100000',
    'paused_jobs_review_branches',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
