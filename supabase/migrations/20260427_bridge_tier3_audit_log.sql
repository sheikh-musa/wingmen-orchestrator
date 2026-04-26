-- CAI-PROCESS-ROUTING-001 + CAI-RESP-091: log Tier-3 fallback firings to notification_log
-- so future CAI persona drift on the announce_to_agent / parent_msg_id discipline is
-- visible at write-time (auditable post-hoc) rather than only at inbox-review-time
-- (which is what cc-ihsanos's #847 / #870 forwards have been doing manually).
--
-- Parent decisions: CAI-PROCESS-ROUTING-001 (the discipline rules), CAI-RESP-091
-- (this destination ruling — notification_log not audit_log; audit_log is the
-- ihsanos amanah cryptographic hash chain, structurally mismatched).
--
-- Approach: extend trigger_cai_decision_announce body with a single conditional
-- INSERT into notification_log that fires only when both Tier-1 (explicit) and
-- Tier-2 (parent-inferred) inputs are NULL — i.e. the trigger is genuinely
-- falling through to the cc-ihsanos legacy default. Legacy rows that never had
-- announce_to_agent populated and have no parent_msg_id (the entire pre-BUG-030
-- corpus) WILL log; that's intended — the audit captures the structural-drift
-- pattern, not just future drift.
--
-- Idempotent: CREATE OR REPLACE FUNCTION + assertion DO-block. Ran twice = no-op
-- on second run.

BEGIN;

-- ============================================================
-- SECTION 1: Replace trigger_cai_decision_announce with audit-extended body
-- ============================================================

CREATE OR REPLACE FUNCTION public.trigger_cai_decision_announce()
  RETURNS trigger
  LANGUAGE plpgsql
  SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
  v_message_type TEXT;
  v_requires_response BOOLEAN;
  v_to_agent TEXT;
  v_thread_id UUID;
  v_parent_from_agent TEXT;
  v_parent_thread_id UUID;
BEGIN
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status NOT IN ('challenge_window', 'accepted')
     OR COALESCE(NEW.bypass_review, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' THEN
    IF OLD.announced_by_msg_id IS NOT NULL
       OR OLD.execution_status = 'implemented' THEN
      RETURN NEW;
    END IF;
  END IF;

  IF NEW.challenge_status = 'challenge_window' THEN
    v_message_type := 'review_request';
    v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
    v_requires_response := true;
  ELSE
    v_message_type := 'decision';
    v_subject := NEW.decision_ref || ': ' || NEW.title;
    v_requires_response := false;
  END IF;

  v_body := format(
    E'Decision %s filed by CAI (status: %s).\nFull spec: see strategic_decisions.decision_ref=%s%s\n',
    NEW.decision_ref,
    NEW.challenge_status,
    NEW.decision_ref,
    CASE WHEN NEW.parent_ref IS NOT NULL
         THEN E'\nParent: ' || NEW.parent_ref
         ELSE '' END
  );

  -- BUG-030: 3-tier recipient + thread_id routing.
  -- Tier 1 (explicit): NEW.announce_to_agent / NEW.announce_thread_id if set.
  -- Tier 2 (inferred): if parent_msg_id populated, inherit parent's from_agent
  --                    (reply-to-sender) and parent's thread_id (stay in thread).
  -- Tier 3 (legacy):   cc-ihsanos + fresh uuid (historical default).
  -- Concurrent-delete race is tolerated: ON DELETE RESTRICT on parent_msg_id_fkey
  -- means a committed parent delete is impossible while this decision row exists,
  -- but an uncommitted concurrent delete visible under READ COMMITTED would leave
  -- v_parent_* NULL and silently fall through to Tier 3. Audit loss is negligible
  -- (legacy default still routes to cc-ihsanos); not worth FOR SHARE overhead.
  IF NEW.parent_msg_id IS NOT NULL THEN
    SELECT from_agent, thread_id
      INTO v_parent_from_agent, v_parent_thread_id
      FROM agent_messages
     WHERE id = NEW.parent_msg_id;
  END IF;

  v_to_agent := COALESCE(NEW.announce_to_agent, v_parent_from_agent, 'cc-ihsanos');
  v_thread_id := COALESCE(NEW.announce_thread_id, v_parent_thread_id, gen_random_uuid());

  INSERT INTO agent_messages (
    thread_id, from_agent, to_agent, message_type,
    subject, body, requires_response
  ) VALUES (
    v_thread_id, 'cai', v_to_agent, v_message_type,
    v_subject, v_body, v_requires_response
  )
  RETURNING id INTO v_msg_id;

  NEW.announced_by_msg_id := v_msg_id;
  NEW.notified_at := now();

  -- CAI-PROCESS-ROUTING-001 + CAI-RESP-091: audit Tier-3 fallback firings.
  -- Only logs when BOTH explicit (announce_to_agent) and inferred (parent.from_agent)
  -- are absent — i.e. the trigger genuinely fell through to the cc-ihsanos legacy
  -- default. Captures the structural-drift pattern: every Tier-3 firing is now
  -- visible in notification_log with source='bridge_tier3_misroute', queryable
  -- post-hoc for CAI persona-discipline absorption tracking.
  IF NEW.announce_to_agent IS NULL
     AND v_parent_from_agent IS NULL THEN
    INSERT INTO notification_log (source, decision_ref, channel, recipient, message_text)
    VALUES (
      'bridge_tier3_misroute',
      NEW.decision_ref,
      'agent_messages',
      'cc-ihsanos',
      json_build_object(
        'spawned_msg_id',       v_msg_id,
        'parent_msg_id',        NEW.parent_msg_id,
        'announce_to_agent',    NEW.announce_to_agent,
        'announce_thread_id',   NEW.announce_thread_id,
        'reason',               'Tier-3 fallback fired — populate announce_to_agent or parent_msg_id at INSERT to route correctly per CAI-PROCESS-ROUTING-001'
      )::text
    );
  END IF;

  RETURN NEW;
END;
$function$;

-- ============================================================
-- SECTION 2: Post-apply assertion — body carries the Tier-3 audit INSERT
-- ============================================================

DO $$
DECLARE
  body TEXT;
BEGIN
  SELECT pg_get_functiondef(oid) INTO body
    FROM pg_proc WHERE proname = 'trigger_cai_decision_announce';
  IF body NOT LIKE '%bridge_tier3_misroute%'
     OR body NOT LIKE '%notification_log%'
     OR body NOT LIKE '%spawned_msg_id%' THEN
    RAISE EXCEPTION 'CAI-RESP-091 migration assertion failed: trigger body missing Tier-3 audit INSERT';
  END IF;
END $$;

COMMIT;
