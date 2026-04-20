-- Governance hygiene batch — composes 6 structural fixes into one migration.
--
-- Parent: GOVERNANCE-CLEANUP-001 thread in agent_messages (thread_id
-- 4af8f733-4ba4-48fd-91f0-ce0616b1a70b). Negotiated across msgs 339, 346,
-- 374, 376, 378. See docs/superpowers/plans/2026-04-20-governance-hygiene-batch.md.
--
-- Sections:
--   A. Schema — 'superseded' enum value, superseded_by_decision_ref FK,
--               agent_messages.skipped_at column
--   B. Data fix — CAI-LEDGER-004 vocabulary correction, bulk-close msgs 360-372
--   C. Trigger rewrite — OLD-side guard + new AFTER trigger for auto-close
--   D. pg_cron — banned-prefix purge (24h) + agent_status_history TTL (90d)
--
-- Applied via CAI Supabase MCP (same protocol as ARCH-035/036, LEDGER-049).
-- Authors: cc-ihsanos-3 (spec + SQL), cai (adversarial review).

BEGIN;

-- ══════════════════════════════════════════════════════════════════════
-- Section A — Schema additions
-- ══════════════════════════════════════════════════════════════════════

-- A.1 Add 'superseded' to challenge_status CHECK.
ALTER TABLE strategic_decisions
  DROP CONSTRAINT IF EXISTS strategic_decisions_challenge_status_check;

ALTER TABLE strategic_decisions
  ADD CONSTRAINT strategic_decisions_challenge_status_check
  CHECK (challenge_status = ANY (ARRAY[
    'unchallenged'::text,
    'challenge_window'::text,
    'challenged'::text,
    'accepted'::text,
    'overridden'::text,
    'cai_review_requested'::text,
    'informational'::text,
    'implemented'::text,
    'superseded'::text
  ]));

-- A.2 Add superseded_by_decision_ref FK column (ON DELETE RESTRICT per CAI msg 376).
ALTER TABLE strategic_decisions
  ADD COLUMN IF NOT EXISTS superseded_by_decision_ref TEXT
    REFERENCES strategic_decisions(decision_ref) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS strategic_decisions_superseded_idx
  ON strategic_decisions (superseded_by_decision_ref)
  WHERE superseded_by_decision_ref IS NOT NULL;

-- A.2.5 Self-supersede guard (CAI msg 380 A3): a decision cannot supersede itself.
--       Prevents circular lineage — real amanah failure class, cheap CHECK.
ALTER TABLE strategic_decisions
  DROP CONSTRAINT IF EXISTS strategic_decisions_no_self_supersede_check;

ALTER TABLE strategic_decisions
  ADD CONSTRAINT strategic_decisions_no_self_supersede_check
  CHECK (superseded_by_decision_ref IS NULL
         OR superseded_by_decision_ref != decision_ref);

-- A.3 Add agent_messages.skipped_at (notifier None-skip stamp — per CAI msg 378 Q3).
ALTER TABLE agent_messages
  ADD COLUMN IF NOT EXISTS skipped_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS agent_messages_skipped_idx
  ON agent_messages (skipped_at)
  WHERE skipped_at IS NOT NULL;

-- ══════════════════════════════════════════════════════════════════════
-- Section B — Data fix
-- ══════════════════════════════════════════════════════════════════════

-- B.1 CAI-LEDGER-004 vocabulary correction: overridden → superseded with lineage.
UPDATE strategic_decisions
   SET challenge_status = 'superseded',
       superseded_by_decision_ref = 'CAI-LEDGER-004-REV01',
       updated_at = now()
 WHERE decision_ref = 'CAI-LEDGER-004'
   AND challenge_status = 'overridden';

-- B.2 Bulk-close announce-noise rows from Step 1 hygiene-flip transaction.
--     These are legitimate BUG-025 acceptance-path announces that fired on
--     pre-BUG-020 backlog rows (never previously announced). Trigger rewrite
--     in Section C prevents future recurrence; this closes the 13 rows
--     preserved as regression fixtures for Task 2's verification script.
UPDATE agent_messages
   SET responded_at = now(),
       response_ref = 'GOVERNANCE-CLEANUP-001-hygiene-flip'
 WHERE id BETWEEN 360 AND 372
   AND responded_at IS NULL
   AND response_ref IS NULL        -- H5: don't stomp a different-mechanism close.
   AND from_agent = 'cai'          -- A2: strict scope to the 13-row announce set.
   AND to_agent = 'cc-ihsanos';    -- A2: strict scope to the 13-row announce set.

-- ══════════════════════════════════════════════════════════════════════
-- Section C — Trigger rewrite
-- ══════════════════════════════════════════════════════════════════════

-- C.1 Rewrite BEFORE INSERT/UPDATE announce trigger with OLD-side guard.
--     Semantics: "never announce something already announced OR already implemented."
--     Supersedes the trigger function in 20260419_bug025_acceptance_path_announce.sql.
--     INSERT/UPDATE triggers from 20260419_bug020_bug021_governance_comms_hardening.sql
--     (cai_decision_announce_insert, cai_decision_announce_update) carry over — the
--     function they call is replaced.

CREATE OR REPLACE FUNCTION trigger_cai_decision_announce()
RETURNS TRIGGER AS $$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
  v_message_type TEXT;
  v_requires_response BOOLEAN;
BEGIN
  -- Shared early exits (INSERT + UPDATE).
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status NOT IN ('challenge_window', 'accepted')
     OR COALESCE(NEW.bypass_review, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  -- UPDATE-only OLD-side guard (CAI msg 378 Q1):
  -- suppress if row was ALREADY announced or ALREADY implemented BEFORE this UPDATE.
  -- Prevents hygiene-flip announce storms on pre-BUG-020 backlog rows.
  IF TG_OP = 'UPDATE' THEN
    IF OLD.announced_by_msg_id IS NOT NULL
       OR OLD.execution_status = 'implemented' THEN
      RETURN NEW;
    END IF;
  END IF;

  -- Branch message shape on challenge_status (BUG-025 behaviour, preserved).
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
$$ LANGUAGE plpgsql
SET search_path = public, pg_temp;  -- H1: CVE-2018-1058 class pin (CAI msg 380).

-- C.2 New AFTER UPDATE trigger — auto-close the announce row when the parent
--     decision transitions into execution_status='implemented'.
--     Separate function because it writes to agent_messages (not the row being
--     modified) — BEFORE triggers can't reliably do this while preserving the
--     NEW assignment contract.

-- Intent note (CAI msg 380 A1): this trigger fires only on the FORWARD
-- transition INTO 'implemented'. Backward transitions (implemented → failed,
-- implemented → NULL) are tolerated as no-ops — the `responded_at IS NULL`
-- guard on the inner UPDATE makes the re-fire harmless even if the transition
-- matrix later widens. Documented here so future trigger evolution preserves
-- the invariant.
CREATE OR REPLACE FUNCTION trigger_cai_decision_autoclose_announce()
RETURNS TRIGGER AS $$
BEGIN
  -- Fire only on the transition INTO 'implemented'.
  IF NEW.execution_status = 'implemented'
     AND (OLD.execution_status IS DISTINCT FROM 'implemented')
     AND NEW.announced_by_msg_id IS NOT NULL THEN
    UPDATE agent_messages
       SET responded_at = now(),
           response_ref = 'auto-closed-on-implementation:' || NEW.decision_ref
     WHERE id = NEW.announced_by_msg_id
       AND responded_at IS NULL;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
SET search_path = public, pg_temp;  -- H1: CVE-2018-1058 class pin (CAI msg 380).

DROP TRIGGER IF EXISTS cai_decision_autoclose_announce ON strategic_decisions;
CREATE TRIGGER cai_decision_autoclose_announce
  AFTER UPDATE OF execution_status ON strategic_decisions
  FOR EACH ROW EXECUTE FUNCTION trigger_cai_decision_autoclose_announce();

-- ══════════════════════════════════════════════════════════════════════
-- Section D — pg_cron jobs
-- ══════════════════════════════════════════════════════════════════════

-- D.1 Banned-prefix purge. The regex below MUST stay in sync with
--     nervous_system/agent_messages_poll.py L45 (_BANNED_PREFIX_RE). If you
--     change one, change the other — follow-up task filed to extract the
--     regex into a shared source (CAI msg 380 H6).
-- H3: cron.schedule is NOT idempotent — wrap in existence guard so re-apply
--     on a DB that already has the job is a clean no-op.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'governance_banned_prefix_purge_24h') THEN
    PERFORM cron.schedule(
      'governance_banned_prefix_purge_24h',
      '15 3 * * *',
      $CRON$
        DELETE FROM agent_messages
         WHERE subject ~ '^(CLAIM|STATUS|HEARTBEAT|DIGEST|COMPLETE):'
           AND read_at IS NULL
           AND forwarded_to_telegram_at IS NULL
           AND skipped_at IS NULL
           AND created_at < now() - interval '24 hours'
      $CRON$
    );
  END IF;
END $$;

-- D.2 agent_status_history 90-day TTL (CAI-RESP-042 follow-up).
-- H3: same idempotency guard as D.1.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'agent_status_history_90d_ttl') THEN
    PERFORM cron.schedule(
      'agent_status_history_90d_ttl',
      '0 4 * * *',
      $CRON$
        DELETE FROM agent_status_history
         WHERE created_at < now() - interval '90 days'
      $CRON$
    );
  END IF;
END $$;

COMMIT;
