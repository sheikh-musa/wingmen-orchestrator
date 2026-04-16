-- ARCH-024 Phase 3: Extend boot_briefing to include repo_snapshot metadata.
-- Adds a 'repo_snapshot' source with structural metadata per repo.
-- Keeps boot_briefing under 10KB — no file content, structural shape only.

CREATE OR REPLACE VIEW boot_briefing AS

-- repo_context: phase + blockers + test_health only (ARCH-019 lightweight)
SELECT
    'repo_context'::text AS source,
    rc.repo               AS key,
    json_build_object(
        'phase',      rc.current_phase,
        'blockers',   rc.blockers,
        'test_health',rc.test_health,
        'updated_at', rc.updated_at
    ) AS context
FROM repo_context rc

UNION ALL

-- repo_snapshot: latest structural metadata per repo (ARCH-024)
SELECT
    'repo_snapshot'::text AS source,
    rs.repo_name          AS key,
    json_build_object(
        'commit_sha',       LEFT(rs.commit_sha, 8),
        'commit_timestamp', rs.commit_timestamp,
        'branch',           rs.branch,
        'file_count',       rs.file_count,
        'total_loc',        rs.total_loc,
        'test_count',       rs.test_count,
        'migration_count',  rs.migration_count,
        'route_count',      rs.route_count,
        'schema_tables',    rs.schema_tables
    ) AS context
FROM (
    SELECT DISTINCT ON (repo_name)
        repo_name, commit_sha, commit_timestamp, branch,
        file_count, total_loc, test_count, migration_count,
        route_count, schema_tables
    FROM repo_snapshot
    ORDER BY repo_name, commit_timestamp DESC
) rs

UNION ALL

-- active_decision: index only (ARCH-019 lightweight)
SELECT
    'active_decision'::text    AS source,
    sd.decision_ref            AS key,
    json_build_object(
        'title',            LEFT(sd.title, 80),
        'domain',           sd.domain,
        'category',         sd.category,
        'repos',            sd.repos_affected,
        'source',           sd.source,
        'challenge_status', sd.challenge_status,
        'execution_status', sd.execution_status,
        'decided_at',       sd.decided_at
    ) AS context
FROM strategic_decisions sd
WHERE sd.status = 'active'

UNION ALL

-- open_qa_failure: unchanged (already minimal)
SELECT
    'open_qa_failure'::text                                      AS source,
    ((((qf.repo || '/'::text) || qf.role) || '/'::text) || qf.flow) AS key,
    json_build_object(
        'role',     qf.role,
        'flow',     qf.flow,
        'error',    qf.error,
        'found_at', qf.found_at
    ) AS context
FROM qa_findings qf
WHERE qf.status = 'fail' AND qf.resolved_at IS NULL

UNION ALL

-- latest_cc_session: narrative truncated to 500 chars (ARCH-019)
SELECT
    'latest_cc_session'::text AS source,
    sub.repo_name             AS key,
    json_build_object(
        'narrative',  LEFT(sub.narrative, 500),
        'outcome',    sub.outcome,
        'commit_sha', sub.commit_sha,
        'created_at', sub.created_at
    ) AS context
FROM (
    SELECT DISTINCT ON (cws.repo_name)
        cws.repo_name, cws.narrative, cws.outcome,
        cws.commit_sha, cws.created_at
    FROM cc_work_sessions cws
    ORDER BY cws.repo_name, cws.created_at DESC
) sub

UNION ALL

-- latest_digest: summary format (unchanged)
SELECT
    'latest_digest'::text AS source,
    dig.title             AS key,
    json_build_object(
        'topics',         dig.topics_covered,
        'open_questions', dig.open_questions,
        'action_items',   dig.action_items,
        'session_date',   dig.session_date
    ) AS context
FROM (
    SELECT sd.session_date, sd.title, sd.topics_covered,
           sd.open_questions, sd.action_items
    FROM session_digests sd
    ORDER BY sd.created_at DESC
    LIMIT 1
) dig;
