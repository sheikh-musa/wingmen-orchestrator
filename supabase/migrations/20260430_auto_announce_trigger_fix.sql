-- AUTO-ANNOUNCE-TRIGGER-FIX-001 — Section A semantics enforcement at substrate
--
-- Per CAI-RESP-INBOX-CADENCE-001 Section A and cai's substrate observations
-- in msgs #1106 + #1117:
--
--   The strategic_decisions auto-announce trigger
--   (trigger_cai_decision_announce) was setting agent_messages.requires_response
--   = true on every challenge_window-state CAI ruling, derived from
--   challenge_status. But Section A semantics say: rulings/AGREED close via
--   read_at; the challenge_window mechanism IS the response gate
--   (challenge_status field mutation), NOT requires_response on the announce
--   message. Cai was manually patching requires_response=false on every
--   CAI-RESP-* announce to compensate. Inverse of the Section A discipline —
--   substrate undoing what manual hygiene tries to fix.
--
--   The autoclose trigger (trigger_cai_decision_autoclose_announce) had a
--   parallel issue: it always set responded_at on the announce when
--   execution_status flipped to 'implemented'. Section A reserves
--   responded_at for substantive dialogue turns; if the message had
--   requires_response=false (the new default), there's no response debt to
--   close, and writing responded_at violates Section A.
--
-- Scope:
--   1. ADD COLUMN announce_requires_response BOOLEAN NOT NULL DEFAULT false
--      on strategic_decisions. Callers set true ONLY when the announce row
--      itself needs explicit AGREE/CHALLENGE response (rare — most CAI
--      decisions use challenge_status mechanism instead).
--   2. CREATE OR REPLACE trigger_cai_decision_announce: requires_response
--      derived from NEW.announce_requires_response (default false), not from
--      challenge_status. message_type still derives from challenge_status
--      (review_request vs decision) for thread-shape signaling.
--   3. CREATE OR REPLACE trigger_cai_decision_autoclose_announce: only set
--      responded_at if the target announce message originally had
--      requires_response=true. Otherwise leave alone — read_at closes per
--      Section A.
--
-- Idempotency: ADD COLUMN IF NOT EXISTS + CREATE OR REPLACE FUNCTION.
-- Re-runnable. Migration is additive (ADD COLUMN with safe default; trigger
-- semantics LOOSEN existing behavior so all prior callers continue working);
-- qualifies for pre-apply-then-review per CAI-RESP-102.

BEGIN;

-- ============================================================================
-- Section 1: announce_requires_response column
-- ============================================================================

ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS announce_requires_response BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN strategic_decisions.announce_requires_response IS
  'CAI-PROCESS-INBOX-CADENCE-001 Section A + AUTO-ANNOUNCE-TRIGGER-FIX-001. '
  'Default false because rulings/AGREED close via read_at, and the '
  'challenge_window mechanism IS the response gate. Set true ONLY when the '
  'announce message itself explicitly needs AGREE/CHALLENGE — rare; most '
  'CAI decisions use challenge_status mechanism instead.';


-- ============================================================================
-- Section 2: announce trigger — derive requires_response from new column
-- ============================================================================

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

  -- message_type still derived from challenge_status for thread-shape signaling.
  -- review_request vs decision is about how downstream agents interpret the
  -- subject line (review request → CHALLENGE candidate; decision → ruling).
  IF NEW.challenge_status = 'challenge_window' THEN
    v_message_type := 'review_request';
    v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
  ELSE
    v_message_type := 'decision';
    v_subject := NEW.decision_ref || ': ' || NEW.title;
  END IF;

  -- AUTO-ANNOUNCE-TRIGGER-FIX-001: requires_response is now ONLY derived from
  -- the explicit announce_requires_response column (default false). Section A
  -- semantics: rulings/AGREED close via read_at; challenge_window mechanism IS
  -- the response gate for proposals. Setting requires_response=true on
  -- challenge_window announces created phantom unresponded debt that cai had
  -- to manually patch on every CAI-RESP-* row pre-fix.
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

  -- BUG-030: 3-tier recipient + thread_id routing (unchanged from prior version).
  -- Tier 1 (explicit): NEW.announce_to_agent / NEW.announce_thread_id if set.
  -- Tier 2 (inferred): if parent_msg_id populated, inherit parent's from_agent
  --                    (reply-to-sender) and parent's thread_id (stay in thread).
  -- Tier 3 (legacy):   cc-ihsanos + fresh uuid (historical default).
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


-- ============================================================================
-- Section 3: autoclose trigger — only close if requires_response was true
-- ============================================================================

CREATE OR REPLACE FUNCTION public.trigger_cai_decision_autoclose_announce()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
  v_orig_requires_response BOOLEAN;
BEGIN
  IF NEW.execution_status = 'implemented'
     AND (OLD.execution_status IS DISTINCT FROM 'implemented')
     AND NEW.announced_by_msg_id IS NOT NULL THEN

    -- AUTO-ANNOUNCE-TRIGGER-FIX-001: only set responded_at if the announce
    -- originally had requires_response=true. Section A reserves responded_at
    -- for substantive dialogue turns; if the announce had
    -- requires_response=false (the new default for rulings/AGREED), the
    -- autoclose trigger writing responded_at would violate Section A and
    -- create the same phantom-debt-inverse pattern the announce trigger
    -- fix is designed to prevent.
    SELECT requires_response
      INTO v_orig_requires_response
      FROM agent_messages
     WHERE id = NEW.announced_by_msg_id;

    IF COALESCE(v_orig_requires_response, false) = true THEN
      UPDATE agent_messages
         SET responded_at = now(),
             response_ref = 'auto-closed-on-implementation:' || NEW.decision_ref
       WHERE id = NEW.announced_by_msg_id
         AND responded_at IS NULL;
    END IF;
  END IF;
  RETURN NEW;
END;
$function$;


-- ============================================================================
-- Section 4: assertion gate (fail-loud per CAI-RESP-080 CHALLENGE-1 pattern)
-- ============================================================================

DO $$
DECLARE
    col_exists BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name='strategic_decisions'
           AND column_name='announce_requires_response'
    ) INTO col_exists;
    IF NOT col_exists THEN
        RAISE EXCEPTION 'announce_requires_response column missing after ADD COLUMN';
    END IF;

    -- Verify default is false on existing rows (no NULL violations)
    PERFORM 1 FROM strategic_decisions WHERE announce_requires_response IS NULL LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'announce_requires_response has NULL rows — NOT NULL DEFAULT false should have backfilled';
    END IF;

    RAISE NOTICE 'AUTO-ANNOUNCE-TRIGGER-FIX-001 — assertions passed';
END $$;

COMMIT;

-- schema_migrations bookkeeping
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260430110000',
    'auto_announce_trigger_fix',
    ARRAY[]::text[]
)
ON CONFLICT (version) DO NOTHING;
