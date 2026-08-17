-- 054: SLA 'unresponded' fires only when the recipient is NOT observably active
-- since the request (RED-on-absence fix; orch-console #24803, concurred #24817).
--
-- THE BUG IT FIXES: inbox_sla_violations flagged 'unresponded' on
-- `requires_response AND responded_at IS NULL`, but the fleet answers by posting a
-- NEW bus row and essentially never stamps responded_at. So every answered rr=true
-- message stayed "unresponded" forever and priority_sla_watchdog paged the OPERATOR'S
-- PHONE P0 on healthy collaboration (06:15Z: #24759, answered in 69s). "same thread"
-- was measured broken (thread_id is unique per message, 12/12); responded_at is
-- unreliable. The honest signal is OBSERVED ACTIVITY: did the recipient do anything
-- after the message arrived? A live recipient working its bus is not "nobody's home".
--
-- UNIFICATION: `agent_observed_activity` is the ONE observed-liveness signal that both
-- this view AND the CAI-1029 commitment sweeper consume (the sweeper reads owner-
-- liveness from agent_status row+not-offline+fresh-hb, which is PERMANENTLY false for
-- a no-heartbeat-by-design body like cc-quality — same wrong signal). Sweeper moves
-- onto `last_observed_at > now() - <window>`; this view checks `<= created_at`.
--
-- Reversible: revert = CREATE OR REPLACE VIEW inbox_sla_violations back to the pre-054
-- body (drop the LEFT JOIN + the AND on the 'unresponded' branch) and DROP VIEW
-- agent_observed_activity. Additive + fail-safe: an absent last_observed_at (NULL,
-- COALESCE to -infinity) NEVER suppresses -> a genuinely silent recipient still flags.

CREATE OR REPLACE VIEW agent_observed_activity AS
  SELECT am.from_agent AS agent_id,
         max(am.created_at) AS last_observed_at
    FROM agent_messages am
   WHERE am.from_agent IS NOT NULL
     AND am.is_test IS NOT TRUE
   GROUP BY am.from_agent;

COMMENT ON VIEW agent_observed_activity IS
  'Shared observed-liveness signal (#24803/#24817): each agent''s most recent bus '
  'activity. Consumed by inbox_sla_violations (unresponded suppression) and the '
  'CAI-1029 commitment sweeper (owner-liveness), so a no-heartbeat-by-design body '
  '(cc-quality, CAI-729) reads live from what it DID, not a heartbeat it never writes.';

CREATE OR REPLACE VIEW inbox_sla_violations AS
 WITH msg_with_age AS (
         SELECT am.id,
            am.to_agent,
            am.from_agent,
            COALESCE(am.priority, 'P3'::text) AS priority,
            am.message_type,
            am.subject,
            am.requires_response,
            am.created_at,
            am.read_at,
            am.responded_at,
            (EXTRACT(epoch FROM now() - am.created_at) / 60.0)::integer AS elapsed_minutes
           FROM agent_messages am
          WHERE (am.read_at IS NULL OR am.requires_response = true AND am.responded_at IS NULL) AND am.is_test IS NOT TRUE
        )
 SELECT m.to_agent AS agent,
    m.id AS message_id,
    m.priority,
    m.from_agent,
    m.subject,
    m.created_at,
    'unread'::text AS violation_type,
    m.elapsed_minutes,
    pt.unread_alarm_minutes AS threshold_minutes
   FROM msg_with_age m
     JOIN priority_thresholds pt ON pt.priority = m.priority
  WHERE m.read_at IS NULL AND m.elapsed_minutes > pt.unread_alarm_minutes
UNION ALL
 SELECT m.to_agent AS agent,
    m.id AS message_id,
    m.priority,
    m.from_agent,
    m.subject,
    m.created_at,
    'unresponded'::text AS violation_type,
    m.elapsed_minutes,
    pt.unresponded_alarm_minutes AS threshold_minutes
   FROM msg_with_age m
     JOIN priority_thresholds pt ON pt.priority = m.priority
     LEFT JOIN agent_observed_activity oa ON oa.agent_id = m.to_agent
  WHERE m.requires_response = true AND m.responded_at IS NULL AND m.elapsed_minutes > pt.unresponded_alarm_minutes
    -- #24803: only a NOT-observably-active recipient is a real 'unresponded' stall.
    -- COALESCE to -infinity so an absent signal NEVER suppresses (fail-safe: a truly
    -- silent/dead recipient still flags).
    AND COALESCE(oa.last_observed_at, '-infinity'::timestamptz) <= m.created_at;
