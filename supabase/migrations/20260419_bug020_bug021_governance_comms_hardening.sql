-- BUG-020 + BUG-021: Governance comms pipeline v1 hardening.
-- Fixes 2026-04-18 governance blackout. See:
--   docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md
--
-- Adds:
--   1. agent_messages.forwarded_to_telegram_at   (BUG-021 — replaces read_at clobber)
--   2. strategic_decisions.announced_by_msg_id   (BUG-020 — FK dedup for trigger)
--   3. Partial indexes on the NULL subsets of both columns
--   4. trigger_cai_decision_announce() function + two triggers
--   5. Per-orphan atomic backfill DO block
--
-- RLS deferred (service_role bypasses; dead policies worse than none).

-- ── 1. Schema changes ───────────────────────────────────────────────────────

ALTER TABLE agent_messages
  ADD COLUMN IF NOT EXISTS forwarded_to_telegram_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS agent_messages_forwarded_idx
  ON agent_messages (forwarded_to_telegram_at)
  WHERE forwarded_to_telegram_at IS NULL;

ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS announced_by_msg_id BIGINT
    REFERENCES agent_messages(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS strategic_decisions_announced_idx
  ON strategic_decisions (announced_by_msg_id)
  WHERE announced_by_msg_id IS NULL;

-- ── 2. BUG-020 trigger function + triggers ──────────────────────────────────

CREATE OR REPLACE FUNCTION trigger_cai_decision_announce()
RETURNS TRIGGER AS $$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
BEGIN
  -- Guard: only claude_ai_session decisions in challenge_window, not already announced
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status IS DISTINCT FROM 'challenge_window'
     OR COALESCE(NEW.bypass_review, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  -- UPDATE: only fire on transition INTO challenge_window
  IF TG_OP = 'UPDATE' AND OLD.challenge_status = 'challenge_window' THEN
    RETURN NEW;
  END IF;

  v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
  v_body := format(
    E'Decision %s filed by CAI in challenge_window.\nFull spec: see strategic_decisions.decision_ref=%s%s\n',
    NEW.decision_ref,
    NEW.decision_ref,
    CASE WHEN NEW.parent_ref IS NOT NULL
         THEN E'\nParent: ' || NEW.parent_ref
         ELSE '' END
  );

  INSERT INTO agent_messages (
    thread_id, from_agent, to_agent, message_type,
    subject, body, requires_response
  ) VALUES (
    gen_random_uuid(), 'cai', 'cc-ihsanos', 'review_request',
    v_subject, v_body, true
  )
  RETURNING id INTO v_msg_id;

  NEW.announced_by_msg_id := v_msg_id;
  NEW.notified_at := now();

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER cai_decision_announce_insert
  BEFORE INSERT ON strategic_decisions
  FOR EACH ROW EXECUTE FUNCTION trigger_cai_decision_announce();

CREATE TRIGGER cai_decision_announce_update
  BEFORE UPDATE OF challenge_status ON strategic_decisions
  FOR EACH ROW EXECUTE FUNCTION trigger_cai_decision_announce();

-- ── 3. Backfill sweep (atomic per orphan) ───────────────────────────────────

DO $$
DECLARE
  v_row RECORD;
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
BEGIN
  FOR v_row IN
    SELECT id, decision_ref, title, parent_ref
    FROM strategic_decisions
    WHERE source = 'claude_ai_session'
      AND challenge_status = 'challenge_window'
      AND COALESCE(bypass_review, false) = false
      AND announced_by_msg_id IS NULL
      AND notified_at IS NULL
    ORDER BY created_at ASC
  LOOP
    v_subject := v_row.decision_ref || ': ' || v_row.title
                  || ' — for review + challenge (backfilled)';
    v_body := format(
      E'Decision %s backfilled by BUG-020 migration.\nFull spec: see strategic_decisions.decision_ref=%s%s',
      v_row.decision_ref, v_row.decision_ref,
      CASE WHEN v_row.parent_ref IS NOT NULL
           THEN E'\nParent: ' || v_row.parent_ref
           ELSE '' END
    );

    INSERT INTO agent_messages (
      thread_id, from_agent, to_agent, message_type,
      subject, body, requires_response
    ) VALUES (
      gen_random_uuid(), 'cai', 'cc-ihsanos', 'review_request',
      v_subject, v_body, true
    )
    RETURNING id INTO v_msg_id;

    UPDATE strategic_decisions
    SET announced_by_msg_id = v_msg_id,
        notified_at = now()
    WHERE id = v_row.id;
  END LOOP;
END $$;
