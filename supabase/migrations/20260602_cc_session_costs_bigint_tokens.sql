-- 2026-06-02 cc_session_costs token columns INTEGER → BIGINT
--
-- Auto-writer observed failing 2026-06-01:
--   orch.ERROR cc_session_costs auto-writer failed: integer out of range
-- Diagnosis: cc-cosem session cache_read_input_tokens reached 1.66B,
-- approaching INTEGER max (2147483647). Long-running interactive cc-* sessions
-- accumulate cache reads in the billions over multi-day lifetimes.
--
-- Migration sequence (linter recognises DROP VIEW IF EXISTS + CREATE VIEW pattern):
--   1. DROP VIEW IF EXISTS boot_briefing — required because the view's
--      cc_session_costs arm references csc.input_tokens + csc.output_tokens,
--      and PostgreSQL rejects ALTER COLUMN TYPE while a view depends on it.
--   2. ALTER COLUMN × 4 from INTEGER to BIGINT. PostgreSQL widens in-place;
--      no data loss, no table rewrite.
--   3. CREATE VIEW boot_briefing AS <full body, captured 2026-06-02>.
--      Body unchanged — BIGINT + BIGINT produces BIGINT, downstream consumers
--      see widened numbers without code change.

BEGIN;

DROP VIEW IF EXISTS boot_briefing;

ALTER TABLE cc_session_costs ALTER COLUMN input_tokens                TYPE BIGINT;
ALTER TABLE cc_session_costs ALTER COLUMN output_tokens               TYPE BIGINT;
ALTER TABLE cc_session_costs ALTER COLUMN cache_creation_input_tokens TYPE BIGINT;
ALTER TABLE cc_session_costs ALTER COLUMN cache_read_input_tokens     TYPE BIGINT;

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
    json_build_object('last_fire_at', aal.last_fire_at, 'sessions_24h', aal.sessions_24h, 'cadence_seconds', aal.cadence_seconds, 'detected_at', aal.detected_at, 'parent_pid', aal.parent_pid) AS context
   FROM active_autonomous_loops aal
UNION ALL
 SELECT 'long_running_caller'::text AS source,
    lrcc.caller_name AS key,
    json_build_object('cmd', lrcc.cmd, 'last_seen_at', lrcc.last_seen_at, 'expected_tokens_per_day', lrcc.expected_tokens_per_day, 'max_tokens_per_day', lrcc.max_tokens_per_day, 'expected_cadence_seconds', lrcc.expected_cadence_seconds, 'ratified_by_decision_ref', lrcc.ratified_by_decision_ref, 'registered_by_identity', lrcc.registered_by_identity, 'auto_kill_policy', lrcc.auto_kill_policy, 'purpose', lrcc.purpose, 'status',
        CASE
            WHEN lrcc.revoked_at IS NOT NULL THEN 'revoked'::text
            WHEN lrcc.last_seen_at IS NULL THEN 'never_heartbeated'::text
            WHEN lrcc.last_seen_at < (now() - lrcc.expected_cadence_seconds::double precision * '00:00:01'::interval * 3::double precision) THEN 'stale_heartbeat'::text
            ELSE 'active'::text
        END) AS context
   FROM long_running_claude_callers lrcc
  WHERE lrcc.revoked_at IS NULL OR lrcc.revoked_at > (now() - '30 days'::interval)
UNION ALL
 SELECT 'watchdog_monitored_callers'::text AS source,
    wmc.caller_name AS key,
    json_build_object('first_observed_at', wmc.first_observed_at, 'expires_at', wmc.expires_at, 'sessions_24h', wmc.sessions_24h, 'cadence_seconds', wmc.cadence_seconds, 'signal_a_match', wmc.signal_a_match, 'signal_b_match', wmc.signal_b_match, 'signal_c_match', wmc.signal_c_match, 'escalated', wmc.escalated_at IS NOT NULL) AS context
   FROM watchdog_monitored_callers wmc
  WHERE wmc.expires_at > now() OR wmc.escalated_at IS NOT NULL;

DO $$
DECLARE
    bad_col TEXT;
    arm_count INTEGER;
BEGIN
    FOR bad_col IN
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'cc_session_costs'
           AND column_name IN ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens')
           AND data_type <> 'bigint'
    LOOP
        RAISE EXCEPTION 'cc_session_costs.% did not promote to bigint', bad_col;
    END LOOP;
    -- Regression guard: same arm-set check as M_DETECTIVE expects.
    SELECT count(*) INTO arm_count
      FROM regexp_matches(pg_get_viewdef('boot_briefing'::regclass, true), '::text AS source', 'g');
    IF arm_count < 20 THEN
        RAISE EXCEPTION 'boot_briefing arm count = % (expected >= 20)', arm_count;
    END IF;
    RAISE NOTICE 'cc_session_costs BIGINT promotion + boot_briefing % arms verified', arm_count;
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260602120000',
    'cc_session_costs_bigint_tokens',
    ARRAY[
        $stmt$DROP VIEW IF EXISTS boot_briefing$stmt$,
        $stmt$ALTER TABLE cc_session_costs ALTER COLUMN input_tokens TYPE BIGINT$stmt$,
        $stmt$ALTER TABLE cc_session_costs ALTER COLUMN output_tokens TYPE BIGINT$stmt$,
        $stmt$ALTER TABLE cc_session_costs ALTER COLUMN cache_creation_input_tokens TYPE BIGINT$stmt$,
        $stmt$ALTER TABLE cc_session_costs ALTER COLUMN cache_read_input_tokens TYPE BIGINT$stmt$,
        $stmt$CREATE VIEW boot_briefing AS (full body — see migration source for the verbatim CREATE)$stmt$
    ]::text[]
)
ON CONFLICT (version) DO NOTHING;
