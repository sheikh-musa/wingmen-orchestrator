-- Skip auto-announce + Tier-3 audit for is_test=true strategic_decisions
--
-- Per Musa 2026-05-07: cai's E2E test fixtures (TEST-* refs with body='t')
-- were generating real Telegram notifications + bridge_tier3_misroute audit
-- firings, polluting Musa's operator-attention surface with synthetic noise.
-- 18 of 20 misroute firings in last 24h were TEST-* fixtures.
--
-- The strategic_decisions.is_test column already exists (default false) for
-- exactly this case; the trigger just didn't honor it. Same shape as the
-- existing `bypass_review` skip already in the early-return condition.
--
-- Adds `OR COALESCE(NEW.is_test, false) = true` to the early-return guard
-- so test rows skip both the agent_messages INSERT and the
-- bridge_tier3_misroute audit-log INSERT.
--
-- Idempotent CREATE OR REPLACE FUNCTION; semantically loosens behavior
-- (existing callers without is_test=true continue working unchanged);
-- qualifies for pre-apply-then-review per CAI-RESP-102.

BEGIN;

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
  -- Early-return guards. Added is_test=true case so cai's E2E test fixtures
  -- (TEST-* refs) skip the announce + audit path entirely, eliminating
  -- operator-attention noise from synthetic substrate exercises.
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status NOT IN ('challenge_window', 'accepted')
     OR COALESCE(NEW.bypass_review, false) = true
     OR COALESCE(NEW.is_test, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' THEN
    IF OLD.announced_by_msg_id IS NOT NULL
       OR OLD.execution_status = 'implemented' THEN
      RETURN NEW;
    END IF;
  END IF;

  -- message_type derived from challenge_status for thread-shape signaling.
  IF NEW.challenge_status = 'challenge_window' THEN
    v_message_type := 'review_request';
    v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
  ELSE
    v_message_type := 'decision';
    v_subject := NEW.decision_ref || ': ' || NEW.title;
  END IF;

  -- AUTO-ANNOUNCE-TRIGGER-FIX-001: requires_response derived from explicit column.
  v_requires_response := COALESCE(NEW.announce_requires_response, false);

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

-- Assertion gate: function exists + the new is_test guard is in the body.
DO $$
DECLARE
    fn_body TEXT;
BEGIN
    SELECT prosrc INTO fn_body
      FROM pg_proc
     WHERE proname='trigger_cai_decision_announce';
    IF fn_body IS NULL THEN
        RAISE EXCEPTION 'trigger_cai_decision_announce missing after CREATE OR REPLACE';
    END IF;
    IF position('is_test' IN fn_body) = 0 THEN
        RAISE EXCEPTION 'trigger body lacks is_test guard';
    END IF;
    RAISE NOTICE 'is_test skip guard verified in trigger body';
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260507100000',
    'skip_test_row_announces',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
