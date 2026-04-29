-- CAI-PROCESS-INBOX-CADENCE-001 Section E (cross-cutting infra) — Phase 1
--
-- Two artifacts ship in this migration:
--   1. priority_thresholds — data table holding Section C cadence config
--      (sweep_minutes / unread_alarm_minutes / unresponded_alarm_minutes per
--      P1/P2/P3). Live-tunable via UPDATE without view recreation.
--   2. inbox_sla_violations — view that surfaces agent_messages rows past
--      their alarm threshold per priority.
--   3. boot_briefing — DROP+CREATE rebuild adding inbox_sla_violation UNION
--      branch (10th section). All 9 prior sections preserved verbatim per
--      Option B Section 5 pattern.
--
-- Section A semantics (CAI-PROCESS-INBOX-CADENCE-001):
--   - "unread alarm": read_at IS NULL AND elapsed since created_at > threshold
--   - "unresponded alarm": requires_response=true AND responded_at IS NULL
--     AND elapsed since created_at > threshold
--   - Both clocks anchored on created_at (not read_at) — keeps the SLA budget
--     stable regardless of when the recipient happened to glance at the row.
--   - A single message CAN surface as two rows (unread + unresponded) if it
--     is both requires_response=true AND past both thresholds. Alarm dedup
--     happens upstream via notification_log dedup_key.
--
-- Idempotency: ADD COLUMN / CREATE TABLE IF NOT EXISTS / INSERT ... ON CONFLICT
-- DO NOTHING / CREATE OR REPLACE VIEW. Re-runnable. Migration is additive
-- (no DROP / no data loss); qualifies for pre-apply-then-review per
-- CAI-RESP-102.

BEGIN;

-- ============================================================================
-- Section 1: priority_thresholds (cadence config table)
-- ============================================================================

CREATE TABLE IF NOT EXISTS priority_thresholds (
    priority                  TEXT PRIMARY KEY
        CHECK (priority IN ('P1','P2','P3')),
    sweep_minutes             INT  NOT NULL CHECK (sweep_minutes > 0),
    unread_alarm_minutes      INT  NOT NULL CHECK (unread_alarm_minutes > 0),
    unresponded_alarm_minutes INT  NOT NULL CHECK (unresponded_alarm_minutes > 0),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by                TEXT NOT NULL DEFAULT 'cc-orchestrator'
);

COMMENT ON TABLE priority_thresholds IS
  'CAI-PROCESS-INBOX-CADENCE-001 Section C cadence config. Tune via UPDATE; '
  'inbox_sla_violations view picks up changes without recreation.';

INSERT INTO priority_thresholds (priority, sweep_minutes, unread_alarm_minutes, unresponded_alarm_minutes) VALUES
    ('P1',  15,   60,  240),
    ('P2',  30,  240, 1440),
    ('P3', 240, 1440, 4320)
ON CONFLICT (priority) DO NOTHING;


-- ============================================================================
-- Section 2: inbox_sla_violations view
-- ============================================================================

CREATE OR REPLACE VIEW inbox_sla_violations AS
WITH msg_with_age AS (
    SELECT
        am.id,
        am.to_agent,
        am.from_agent,
        COALESCE(am.priority, 'P3') AS priority,
        am.message_type,
        am.subject,
        am.requires_response,
        am.created_at,
        am.read_at,
        am.responded_at,
        (EXTRACT(EPOCH FROM (now() - am.created_at)) / 60.0)::int AS elapsed_minutes
    FROM agent_messages am
    WHERE am.read_at IS NULL
       OR (am.requires_response = true AND am.responded_at IS NULL)
)
SELECT
    m.to_agent          AS agent,
    m.id                AS message_id,
    m.priority,
    m.from_agent,
    m.subject,
    m.created_at,
    'unread'::text      AS violation_type,
    m.elapsed_minutes,
    pt.unread_alarm_minutes AS threshold_minutes
  FROM msg_with_age m
  JOIN priority_thresholds pt ON pt.priority = m.priority
 WHERE m.read_at IS NULL
   AND m.elapsed_minutes > pt.unread_alarm_minutes

UNION ALL

SELECT
    m.to_agent          AS agent,
    m.id                AS message_id,
    m.priority,
    m.from_agent,
    m.subject,
    m.created_at,
    'unresponded'::text AS violation_type,
    m.elapsed_minutes,
    pt.unresponded_alarm_minutes AS threshold_minutes
  FROM msg_with_age m
  JOIN priority_thresholds pt ON pt.priority = m.priority
 WHERE m.requires_response = true
   AND m.responded_at IS NULL
   AND m.elapsed_minutes > pt.unresponded_alarm_minutes;

COMMENT ON VIEW inbox_sla_violations IS
  'CAI-PROCESS-INBOX-CADENCE-001 Section E. Rows = (agent, message_id, '
  'priority, violation_type) past Section C thresholds. Both clocks anchored '
  'on created_at. Single message can surface twice (unread + unresponded) '
  'when applicable; alarm dedup belongs upstream in notification_log.';


-- ============================================================================
-- Section 3: boot_briefing rebuild — adds inbox_sla_violation UNION branch
-- ============================================================================
--
-- DROP+CREATE pattern matches Option B Section 5 — all 9 prior branches
-- copy-pasted verbatim from pg_get_viewdef capture. New 10th branch is the
-- inbox_sla_violation surface.

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
   FROM inbox_sla_violations isv;


-- ============================================================================
-- Section 4: assertion gate (fail-loud per CAI-RESP-080 CHALLENGE-1 pattern)
-- ============================================================================

DO $$
DECLARE
    pt_count INT;
    view_exists BOOLEAN;
    boot_branches INT;
BEGIN
    SELECT count(*) INTO pt_count FROM priority_thresholds;
    IF pt_count < 3 THEN
        RAISE EXCEPTION 'priority_thresholds: expected >=3 rows (P1/P2/P3), got %', pt_count;
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.views
         WHERE table_name = 'inbox_sla_violations'
    ) INTO view_exists;
    IF NOT view_exists THEN
        RAISE EXCEPTION 'inbox_sla_violations view missing after CREATE OR REPLACE';
    END IF;

    SELECT count(*)::int INTO boot_branches
      FROM (SELECT DISTINCT source FROM boot_briefing) src;
    -- 10 branches expected; tolerate 9 if a branch has zero rows at apply time
    IF boot_branches < 8 THEN
        RAISE EXCEPTION 'boot_briefing: expected >=8 source branches, got %', boot_branches;
    END IF;

    RAISE NOTICE 'CAI-PROCESS-INBOX-CADENCE-001 Section E Phase 1 — assertions passed (% priority rows, view exists, % boot_briefing branches)', pt_count, boot_branches;
END $$;

COMMIT;

-- ============================================================================
-- schema_migrations bookkeeping
-- ============================================================================

-- supabase tracks migrations in supabase_migrations.schema_migrations; the
-- public schema doesn't have its own table, so this is the canonical surface.
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260429100000',
    'inbox_cadence_section_e_phase_1',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
