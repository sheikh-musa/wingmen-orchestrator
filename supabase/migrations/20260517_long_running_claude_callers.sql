-- CC-LONG-CALLER-REGISTRY-001 Phase A per CAI-RESP-161
-- Per CAI-RESP-160: prohibit unregistered long-running claude-spawning processes.
-- Phase A is visibility-only — does NOT enforce kill behavior; Phase B wires watchdog.
-- Additive only, pre-apply per CAI-RESP-102.

BEGIN;

-- Section 1: long_running_claude_callers table
CREATE TABLE IF NOT EXISTS long_running_claude_callers (
    caller_name               TEXT PRIMARY KEY,
    cmd                       TEXT NOT NULL,
    parent_pid                INTEGER,
    started_at                TIMESTAMPTZ NOT NULL,
    expected_cadence_seconds  INTEGER NOT NULL,
    expected_tokens_per_day   INTEGER NOT NULL,
    max_tokens_per_day        INTEGER,
    ratified_by_decision_ref  TEXT NOT NULL,
    last_seen_at              TIMESTAMPTZ,
    operator_authored         BOOLEAN NOT NULL DEFAULT false,
    registered_by_identity    TEXT NOT NULL
        CHECK (registered_by_identity IN ('operator', 'cc_family', 'substrate')),
    auto_kill_policy          TEXT NOT NULL DEFAULT 'soft_alert'
        CHECK (auto_kill_policy IN ('soft_alert', 'hard_kill', 'no_kill')),
    purpose                   TEXT NOT NULL,
    revoked_at                TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_long_running_claude_callers_active
    ON long_running_claude_callers (registered_by_identity)
    WHERE revoked_at IS NULL;

COMMENT ON TABLE long_running_claude_callers IS
    'CC-LONG-CALLER-REGISTRY-001 Phase A per CAI-RESP-161. Registry of long-running '
    'claude-spawning processes. Phase A is visibility/data layer (non-enforcing); '
    'Phase B wires CAI-RESP-157 [B] watchdog-kill to consult this registry.';

-- Section 2: substrate-native seed (per CAI-RESP-160 carve-out)
INSERT INTO long_running_claude_callers (
    caller_name, cmd, started_at, expected_cadence_seconds, expected_tokens_per_day,
    ratified_by_decision_ref, registered_by_identity, auto_kill_policy, purpose
) VALUES
    ('ralphy', 'ralph_runner.py (substrate-native)', now(), 300, 0,
     'CAI-RESP-160', 'substrate', 'no_kill',
     'Ralph bug-runner — substrate-native autonomous loop per feedback_autonomous_loop_scope.md carve-out'),
    ('paused-job-retry', 'paused_jobs_retry_policy.py (substrate-native)', now(), 1800, 0,
     'CAI-RESP-160', 'substrate', 'no_kill',
     'Paused-job retry sweeper — substrate-native autonomous loop per feedback_autonomous_loop_scope.md carve-out')
ON CONFLICT (caller_name) DO NOTHING;

-- Section 3: extend boot_briefing view
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
    'current'::text AS key,
    json_build_object('state', rs.state, 'since', rs.since, 'paused_reason', rs.paused_reason, 'resume_gates', rs.resume_gates, 'last_state_change_by', rs.last_state_change_by, 'updated_at', rs.updated_at) AS context
   FROM ralph_state rs
  WHERE rs.id = 1
UNION ALL
 SELECT 'cc_session_costs'::text AS source,
    'outlier_24h'::text AS key,
    json_build_object('cc_identity', csc.cc_identity, 'sub_tag', csc.sub_tag, 'session_id', csc.session_id, 'total_tokens', csc.input_tokens + csc.output_tokens, 'input_tokens', csc.input_tokens, 'output_tokens', csc.output_tokens, 'started_at', csc.started_at, 'source', csc.source) AS context
   FROM cc_session_costs csc
  WHERE csc.started_at >= (now() - '24:00:00'::interval) AND (csc.input_tokens + csc.output_tokens) >= COALESCE(( SELECT boot_briefing_config.value_int
           FROM boot_briefing_config
          WHERE boot_briefing_config.key = 'cc_session_costs_outlier_token_threshold'::text), 50000)
UNION ALL
 SELECT 'inbox_hygiene'::text AS source,
    'stale_unresponded_12h'::text AS key,
    json_build_object('message_id', am.id, 'from_agent', am.from_agent, 'subject_prefix', "left"(am.subject, 80), 'priority', am.priority, 'created_at', am.created_at, 'age_minutes', EXTRACT(epoch FROM now() - am.created_at)::integer / 60) AS context
   FROM ( SELECT agent_messages.id,
            agent_messages.from_agent,
            agent_messages.subject,
            agent_messages.priority,
            agent_messages.created_at
           FROM agent_messages
          WHERE agent_messages.to_agent = 'cai'::text AND agent_messages.requires_response = true AND agent_messages.responded_at IS NULL AND agent_messages.read_at IS NULL AND agent_messages.created_at < (now() - '12:00:00'::interval)
          ORDER BY agent_messages.created_at
         LIMIT 10) am
UNION ALL
 SELECT 'active_autonomous_loops'::text AS source,
    aal.cc_identity AS key,
    json_build_object('last_fire_at', aal.last_fire_at, 'sessions_24h', aal.sessions_24h, 'cadence_seconds', aal.cadence_seconds, 'detected_at', aal.detected_at) AS context
   FROM active_autonomous_loops aal
UNION ALL
 SELECT 'long_running_caller'::text AS source,
        lrcc.caller_name           AS key,
        json_build_object(
          'cmd',                       lrcc.cmd,
          'last_seen_at',              lrcc.last_seen_at,
          'expected_tokens_per_day',   lrcc.expected_tokens_per_day,
          'max_tokens_per_day',        lrcc.max_tokens_per_day,
          'expected_cadence_seconds',  lrcc.expected_cadence_seconds,
          'ratified_by_decision_ref',  lrcc.ratified_by_decision_ref,
          'registered_by_identity',    lrcc.registered_by_identity,
          'auto_kill_policy',          lrcc.auto_kill_policy,
          'purpose',                   lrcc.purpose,
          'status',                    CASE
            WHEN lrcc.revoked_at IS NOT NULL THEN 'revoked'
            WHEN lrcc.last_seen_at IS NULL THEN 'never_heartbeated'
            WHEN lrcc.last_seen_at < now() - (lrcc.expected_cadence_seconds * interval '1 second' * 3) THEN 'stale_heartbeat'
            ELSE 'active'
          END
        ) AS context
   FROM long_running_claude_callers lrcc
  WHERE lrcc.revoked_at IS NULL OR lrcc.revoked_at > now() - interval '30 days';

-- Section 4: assertion gate
DO $$
DECLARE view_def TEXT;
DECLARE seed_count INT;
BEGIN
    SELECT pg_get_viewdef('boot_briefing'::regclass, true) INTO view_def;
    IF position('long_running_caller' IN view_def) = 0 THEN
        RAISE EXCEPTION 'boot_briefing view missing long_running_caller UNION arm';
    END IF;
    SELECT count(*) INTO seed_count
      FROM long_running_claude_callers
     WHERE registered_by_identity = 'substrate';
    IF seed_count < 2 THEN
        RAISE EXCEPTION 'substrate-native seed missing (expected >=2 rows, got %)', seed_count;
    END IF;
    RAISE NOTICE 'CC-LONG-CALLER-REGISTRY-001 Phase A: % substrate-native seeds + boot_briefing arm verified', seed_count;
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260517130000',
    'long_running_claude_callers',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
