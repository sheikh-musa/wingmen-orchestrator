-- BUG-025: Announce CAI-filed decisions filed as challenge_status='accepted',
--   not just 'challenge_window'. Branches message shape on status.
--
-- Parent: BUG-020. References:
--   docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md
--   docs/superpowers/plans/2026-04-19-bug-025-acceptance-path-trigger.md
--   strategic_decisions.decision_ref='BUG-025'  (challenge)
--   strategic_decisions.decision_ref='CAI-RESP-040'  (acceptance — B1 + A1 + A2 + concession)
--
-- Shape:
--   CREATE OR REPLACE on trigger_cai_decision_announce(); the existing
--   BEFORE INSERT and BEFORE UPDATE OF challenge_status triggers carry over.
--   No DROP TRIGGER, no schema change.
--
-- Behaviour change vs BUG-020 (357a135):
--   1. Announceable status set widened: 'challenge_window' → ('challenge_window', 'accepted').
--   2. Message shape branches on challenge_status:
--        challenge_window → review_request, requires_response=true (BUG-020 preserved).
--        accepted         → decision,        requires_response=false (BUG-025).
--   3. OLD.challenge_status='challenge_window' state-transition guard dropped —
--      announced_by_msg_id IS NOT NULL is already the universal dedup.
--   4. No SIMILAR TO regex on decision_ref. source='claude_ai_session' is the
--      canonical "from CAI" signal; prefix enumeration would be dead code and
--      would silently drop future namespaces (CAI-OPS, CAI-MUFTI, etc.).
--
-- Forward-compat with ARCH-035: 'decision' is in the planned message_type CHECK.

CREATE OR REPLACE FUNCTION trigger_cai_decision_announce()
RETURNS TRIGGER AS $$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
  v_message_type TEXT;
  v_requires_response BOOLEAN;
BEGIN
  -- Guard: CAI-filed decisions only, in an announceable status,
  --        not bypass_review, not already announced.
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status NOT IN ('challenge_window', 'accepted')
     OR COALESCE(NEW.bypass_review, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  -- Branch message shape on challenge_status.
  IF NEW.challenge_status = 'challenge_window' THEN
    v_message_type := 'review_request';
    v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
    v_requires_response := true;
  ELSE
    -- challenge_status = 'accepted'
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

  INSERT INTO agent_messages (
    thread_id, from_agent, to_agent, message_type,
    subject, body, requires_response
  ) VALUES (
    gen_random_uuid(), 'cai', 'cc-ihsanos', v_message_type,
    v_subject, v_body, v_requires_response
  )
  RETURNING id INTO v_msg_id;

  NEW.announced_by_msg_id := v_msg_id;
  NEW.notified_at := now();

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
