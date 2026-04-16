-- ARCH-019: Three-tier memory — boot_briefing becomes lightweight index
--
-- BEFORE: boot_briefing SELECT * returned full decision + reasoning text,
--         full recent_changes/known_debt, full narrative — ~100KB+ total.
-- AFTER:  boot_briefing returns only index fields (<10KB total).
--         Full content available on-demand via get_decision() and get_repo_context().

-- ── 1. Replace boot_briefing view ────────────────────────────────────────────

CREATE OR REPLACE VIEW boot_briefing AS

-- repo_context: phase + blockers + test_health only
-- DROP: recent_changes (text), known_debt (array) — available via get_repo_context(key)
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

-- active_decision: index only — title (80 chars), no decision/reasoning body
-- DROP: decision (long text), reasoning (long text)
-- ADD:  execution_status, category (useful in index)
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

-- latest_cc_session: narrative truncated to 500 chars, drop session_prompt
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

-- latest_digest: unchanged (already a summary format)
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


-- ── 2. get_decision(ref) — full decision body on-demand ──────────────────────

CREATE OR REPLACE FUNCTION get_decision(ref TEXT)
RETURNS TABLE (
    decision_ref      TEXT,
    title             TEXT,
    decision          TEXT,
    reasoning         TEXT,
    domain            TEXT,
    category          TEXT,
    repos_affected    TEXT[],
    challenge_status  TEXT,
    execution_status  TEXT,
    source            TEXT,
    decided_at        TIMESTAMPTZ,
    decided_by        TEXT,
    parent_ref        TEXT
)
LANGUAGE sql STABLE SECURITY DEFINER
AS $$
    SELECT
        decision_ref, title, decision, reasoning,
        domain, category, repos_affected,
        challenge_status, execution_status,
        source, decided_at, decided_by, parent_ref
    FROM strategic_decisions
    WHERE decision_ref = ref
    LIMIT 1;
$$;


-- ── 3. get_repo_context(key) — full repo context on-demand ───────────────────

CREATE OR REPLACE FUNCTION get_repo_context(key TEXT)
RETURNS TABLE (
    repo               TEXT,
    current_phase      TEXT,
    blockers           TEXT[],
    recent_changes     TEXT,
    known_debt         TEXT[],
    test_health        TEXT,
    deploy_url         TEXT,
    architecture_summary TEXT,
    updated_at         TIMESTAMPTZ
)
LANGUAGE sql STABLE SECURITY DEFINER
AS $$
    SELECT
        repo, current_phase, blockers, recent_changes,
        known_debt, test_health, deploy_url,
        architecture_summary, updated_at
    FROM repo_context
    WHERE repo = key
    LIMIT 1;
$$;
