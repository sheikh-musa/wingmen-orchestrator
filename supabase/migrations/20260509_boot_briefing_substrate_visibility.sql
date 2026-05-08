-- CAI-RESP-154: boot_briefing substrate visibility
-- Bundles three ratified signals (CAI-RESP-153 P2 cc_session_costs +
-- RALPH-STATE-VISIBILITY-001 ralph_state + CAI-RESP-151 P3
-- stale_unresponded_count_12h) into one atomic boot_briefing migration.
--
-- This file ships sections 1-4 (tables + seed). Sections 5 (view extension)
-- and 6 (assertion gate) are appended in a subsequent commit on the same
-- branch.
--
-- Idempotent: ADD TABLE IF NOT EXISTS, INSERT ON CONFLICT DO NOTHING.
-- Pre-apply per CAI-RESP-102.

BEGIN;

-- ============================================================================
-- Section 1: boot_briefing_config — small key-value config table
-- Per CAI-RESP-154 Q3 amendment: outlier threshold lives here so future
-- tuning is one UPDATE, not a migration.
-- ============================================================================
CREATE TABLE IF NOT EXISTS boot_briefing_config (
    key         TEXT PRIMARY KEY,
    value_int   INTEGER,
    value_text  TEXT,
    notes       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO boot_briefing_config (key, value_int, notes)
VALUES (
    'cc_session_costs_outlier_token_threshold',
    50000,
    'cc_session_costs sessions exceeding (input_tokens + output_tokens) over this in 24h surface as boot_briefing outlier rows. Tune via UPDATE.'
)
ON CONFLICT (key) DO NOTHING;

-- ============================================================================
-- Section 2: cc_session_costs — per-session token-spend visibility
-- Per CAI-RESP-153 P2 (visibility-only-30-day window) + CAI-RESP-154 Q1.
-- ============================================================================
CREATE TABLE IF NOT EXISTS cc_session_costs (
    id                       BIGSERIAL PRIMARY KEY,
    cc_identity              TEXT NOT NULL,
    sub_tag                  TEXT,
    session_id               TEXT,
    started_at               TIMESTAMPTZ NOT NULL,
    ended_at                 TIMESTAMPTZ,
    input_tokens             INTEGER NOT NULL DEFAULT 0,
    output_tokens            INTEGER NOT NULL DEFAULT 0,
    source                   TEXT NOT NULL,
    has_per_message_detail   BOOLEAN NOT NULL DEFAULT false,
    notes                    TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cc_session_costs_identity_started
    ON cc_session_costs (cc_identity, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_cc_session_costs_recent
    ON cc_session_costs (started_at DESC);

COMMENT ON TABLE cc_session_costs IS
    'Per-CC-session token-spend visibility per CAI-RESP-153 P2. '
    'Visibility-only window through 2026-06-08 (30d from CAI-RESP-153 ratification); '
    'revisit policy after.';

COMMENT ON COLUMN cc_session_costs.has_per_message_detail IS
    'true → cc_session_messages rows present for this session_cost_id. '
    'false (default) → only top-level totals; common for cai sessions where '
    'per-message visibility is estimation-tier per CAI-RESP-154 Q7.';

-- ============================================================================
-- Section 3: cc_session_messages — per-message detail child table
-- Dormant when has_per_message_detail=false on parent. Per CAI-RESP-154 Q1.
-- ============================================================================
CREATE TABLE IF NOT EXISTS cc_session_messages (
    id                BIGSERIAL PRIMARY KEY,
    session_cost_id   BIGINT NOT NULL REFERENCES cc_session_costs(id) ON DELETE CASCADE,
    sequence_no       INTEGER NOT NULL,
    role              TEXT NOT NULL,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    occurred_at       TIMESTAMPTZ NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cc_session_messages_parent
    ON cc_session_messages (session_cost_id, sequence_no);

-- ============================================================================
-- Section 4: ralph_state — single-row operational state
-- Per RALPH-STATE-VISIBILITY-001 + CAI-RESP-154 Q4 with operator-confirmed
-- seed values (since=2026-04-29 SGT, FILTER-002 dropped from gates because
-- it shipped 2026-05-08).
-- ============================================================================
CREATE TABLE IF NOT EXISTS ralph_state (
    id                      INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    state                   TEXT NOT NULL CHECK (state IN ('active', 'paused')),
    since                   TIMESTAMPTZ NOT NULL,
    paused_reason           TEXT,
    resume_gates            TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    last_state_change_by    TEXT NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trigger: paused_reason required when state='paused'
CREATE OR REPLACE FUNCTION ralph_state_validate()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.state = 'paused' AND (NEW.paused_reason IS NULL OR length(trim(NEW.paused_reason)) = 0) THEN
        RAISE EXCEPTION 'ralph_state.paused_reason required when state=''paused''';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- linter-allow: DROP TRIGGER immediately followed by CREATE TRIGGER same name — idempotent recreate pattern per PR #30 spec
DROP TRIGGER IF EXISTS ralph_state_validate_trigger ON ralph_state;
CREATE TRIGGER ralph_state_validate_trigger
    BEFORE INSERT OR UPDATE ON ralph_state
    FOR EACH ROW EXECUTE FUNCTION ralph_state_validate();

-- Seed initial paused state
INSERT INTO ralph_state (id, state, since, paused_reason, resume_gates, last_state_change_by)
VALUES (
    1,
    'paused',
    '2026-04-29 00:00:00+08'::timestamptz,
    'bugs raised through apps pipeline are not automatically butchered by ralph; pipeline still maturing',
    ARRAY[
        'BUG-PIPELINE-SYNTHETIC-FILTER-001-enforce-cutover',
        'CAI-PROCESS-AUTO-ANNOUNCE-FIX-001',
        'OPTION-B-VERIFICATION-WORKER-READINESS',
        'DOMAIN-SPECIFIC-CC-CONCERNS'
    ],
    'operator-musa'
)
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE ralph_state IS
    'Single-row operational state per RALPH-STATE-VISIBILITY-001. CAI consults '
    'state before urgency framing. Operator-only state changes (cc-orchestrator '
    'does NOT auto-resume).';

-- ============================================================================
-- Section 5: extend boot_briefing view with 3 new UNION arms
-- ============================================================================
CREATE OR REPLACE VIEW boot_briefing AS
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
 SELECT 'paused_job_review_needed'::text AS source,
    j.id::text AS key,
    json_build_object('repo_name', j.repo_name, 'description', "left"(COALESCE(j.description, ''::text), 100), 'fail_count', j.fail_count, 'result_summary_prefix', "left"(COALESCE(j.result_summary, ''::text), 200), 'updated_at', j.updated_at, 'classification',
        CASE
            WHEN j.result_summary ~~ '%[paused_jobs_policy auto-retry%'::text THEN 'allowlist_re_paused'::text
            ELSE 'other'::text
        END) AS context
   FROM jobs j
  WHERE j.status = 'paused'::text AND j.fail_count >= 3 AND j.updated_at < (now() - '01:00:00'::interval) AND COALESCE(j.result_summary, ''::text) !~~ '%ghost success prevented%'::text
UNION ALL
 SELECT 'paused_job_permanent_review'::text AS source,
    j.id::text AS key,
    json_build_object('repo_name', j.repo_name, 'description', "left"(COALESCE(j.description, ''::text), 100), 'fail_count', j.fail_count, 'result_summary_prefix', "left"(COALESCE(j.result_summary, ''::text), 200), 'updated_at', j.updated_at, 'note', 'ghost-success-prevented; retry won''t help — needs spec rewrite or manual decision') AS context
   FROM jobs j
  WHERE j.status = 'paused'::text AND j.fail_count >= 3 AND j.updated_at < (now() - '24:00:00'::interval) AND COALESCE(j.result_summary, ''::text) ~~ '%ghost success prevented%'::text
UNION ALL
 SELECT 'synthetic_filter'::text AS source,
    'filtered_24h'::text AS key,
    json_build_object('count', count(*), 'last_at', max(notification_log.created_at), 'mode', 'enforce') AS context
   FROM notification_log
  WHERE notification_log.source = 'synthetic_filter'::text AND (notification_log.message_text::jsonb ->> 'mode'::text) = 'enforce'::text AND notification_log.created_at >= (now() - '24:00:00'::interval)
 HAVING count(*) > 0
UNION ALL
 SELECT 'synthetic_filter'::text AS source,
    'shadow_24h'::text AS key,
    json_build_object('count', count(*), 'last_at', max(notification_log.created_at), 'mode', 'shadow') AS context
   FROM notification_log
  WHERE notification_log.source = 'synthetic_filter'::text AND (notification_log.message_text::jsonb ->> 'mode'::text) = 'shadow'::text AND notification_log.created_at >= (now() - '24:00:00'::interval)
 HAVING count(*) > 0
UNION ALL
 SELECT 'ralph_state'::text AS source,
        'current'::text     AS key,
        json_build_object(
          'state',                rs.state,
          'since',                rs.since,
          'paused_reason',        rs.paused_reason,
          'resume_gates',         rs.resume_gates,
          'last_state_change_by', rs.last_state_change_by,
          'updated_at',           rs.updated_at
        ) AS context
   FROM ralph_state rs
  WHERE rs.id = 1
UNION ALL
 SELECT 'cc_session_costs'::text AS source,
        'outlier_24h'::text      AS key,
        json_build_object(
          'cc_identity',   csc.cc_identity,
          'sub_tag',       csc.sub_tag,
          'session_id',    csc.session_id,
          'total_tokens',  (csc.input_tokens + csc.output_tokens),
          'input_tokens',  csc.input_tokens,
          'output_tokens', csc.output_tokens,
          'started_at',    csc.started_at,
          'source',        csc.source
        ) AS context
   FROM cc_session_costs csc
  WHERE csc.started_at >= now() - interval '24 hours'
    AND (csc.input_tokens + csc.output_tokens) >= COALESCE(
          (SELECT value_int FROM boot_briefing_config
            WHERE key = 'cc_session_costs_outlier_token_threshold'),
          50000
        )
UNION ALL
 SELECT 'inbox_hygiene'::text          AS source,
        'stale_unresponded_12h'::text   AS key,
        json_build_object(
          'message_id',     am.id,
          'from_agent',     am.from_agent,
          'subject_prefix', "left"(am.subject, 80),
          'priority',       am.priority,
          'created_at',     am.created_at,
          'age_minutes',    EXTRACT(EPOCH FROM (now() - am.created_at))::int / 60
        ) AS context
   FROM ( SELECT id, from_agent, subject, priority, created_at
            FROM agent_messages
           WHERE to_agent = 'cai'
             AND requires_response = true
             AND responded_at IS NULL
             AND read_at IS NULL
             AND created_at < now() - interval '12 hours'
           ORDER BY created_at ASC
          LIMIT 10) am;

-- ============================================================================
-- Section 6: assertion gate — fail loud per CAI-RESP-080 CHALLENGE-1
-- ============================================================================
DO $$
DECLARE
    view_def    TEXT;
    arm_count   INT;
    config_count INT;
    seed_count  INT;
BEGIN
    SELECT pg_get_viewdef('boot_briefing'::regclass, true) INTO view_def;

    -- Assert 3 new substrate-visibility arms in the view
    arm_count := 0;
    IF position('ralph_state' IN view_def) > 0 THEN arm_count := arm_count + 1; END IF;
    IF position('cc_session_costs' IN view_def) > 0 THEN arm_count := arm_count + 1; END IF;
    IF position('inbox_hygiene' IN view_def) > 0 THEN arm_count := arm_count + 1; END IF;
    IF arm_count < 3 THEN
        RAISE EXCEPTION 'boot_briefing view missing substrate-visibility arms — found % of 3', arm_count;
    END IF;

    -- Assert config seed row present
    SELECT count(*) INTO config_count
      FROM boot_briefing_config
     WHERE key = 'cc_session_costs_outlier_token_threshold';
    IF config_count = 0 THEN
        RAISE EXCEPTION 'boot_briefing_config missing outlier threshold seed';
    END IF;

    -- Assert ralph_state seed present
    SELECT count(*) INTO seed_count FROM ralph_state WHERE id = 1;
    IF seed_count = 0 THEN
        RAISE EXCEPTION 'ralph_state seed row (id=1) missing';
    END IF;

    RAISE NOTICE 'substrate-visibility migration: % view arms verified, config seeded, ralph_state seeded', arm_count;
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260509120000',
    'boot_briefing_substrate_visibility',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
