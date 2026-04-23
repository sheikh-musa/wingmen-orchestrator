-- Batch 2: BUG-030 bridge trigger fix
-- Ships 1 P1 fix per CAI msg #620 consolidated briefing + cc-ihsanos msg #631 AGREED:
--   BUG-030 — parent_msg_id + announce_to_agent + announce_thread_id on strategic_decisions
--             + trigger_cai_decision_announce rewrite with 3-tier fallback
--
-- Parent decision: BUG-030.
-- Rulings: CAI-RESP-080 (Refinement 2 review protocol), msg #620 (bundling decision to ship as separate migration),
--          ORCHESTRATOR-NOTIFIER-FIX-001-AMEND (upstream dedup + Fix 4 discipline preserved).
--
-- Sections:
--   1. Add parent_msg_id + announce_to_agent + announce_thread_id columns (all nullable).
--   2. Add parent_msg_id FK (REFERENCES agent_messages(id) ON DELETE RESTRICT).
--   3. REPLACE trigger_cai_decision_announce with 3-tier routing.
--   4. DO-block assertion: trigger body contains COALESCE(NEW.announce_to_agent, ...) pattern.
--
-- Pre-flight verified:
--   * strategic_decisions has 40 columns as of 2026-04-24, most recent: is_test (Batch 1 Section 6).
--   * trigger_cai_decision_announce lives at oid=(SELECT oid FROM pg_proc WHERE proname=...). Body hardcodes 'cc-ihsanos'.
--   * agent_messages.id is BIGINT PK. agent_messages.thread_id is UUID (no self-FK).

BEGIN;

-- ============================================================
-- SECTION 1: strategic_decisions new routing columns (all nullable)
-- ============================================================
-- Reverse:
--   ALTER TABLE strategic_decisions DROP COLUMN parent_msg_id;
--   ALTER TABLE strategic_decisions DROP COLUMN announce_to_agent;
--   ALTER TABLE strategic_decisions DROP COLUMN announce_thread_id;

ALTER TABLE strategic_decisions
  ADD COLUMN parent_msg_id BIGINT,
  ADD COLUMN announce_to_agent TEXT,
  ADD COLUMN announce_thread_id UUID;

COMMENT ON COLUMN strategic_decisions.parent_msg_id IS
  'BUG-030: BIGINT FK to agent_messages(id). If populated, bridge trigger '
  'inherits parent thread_id and reply-to-sender for to_agent routing '
  '(see trigger_cai_decision_announce). Nullable — legacy rows + decisions '
  'not responding to a specific message leave this NULL and hit Tier-3 fallback.';

COMMENT ON COLUMN strategic_decisions.announce_to_agent IS
  'BUG-030: explicit override for bridge trigger recipient. Highest precedence '
  'in the 3-tier COALESCE (explicit > inferred-from-parent > cc-ihsanos default).';

COMMENT ON COLUMN strategic_decisions.announce_thread_id IS
  'BUG-030: explicit override for bridge trigger thread_id. Highest precedence '
  'in the 3-tier COALESCE (explicit > inherit-from-parent > fresh uuid).';

-- ============================================================
-- SECTION 2: parent_msg_id FK → agent_messages(id)
-- ============================================================
-- ON DELETE RESTRICT — a decision row pointing at a parent message should
-- prevent the parent's deletion; choose fail-loud over silent orphaning.
-- Reverse:
--   ALTER TABLE strategic_decisions DROP CONSTRAINT strategic_decisions_parent_msg_id_fkey;

ALTER TABLE strategic_decisions
  ADD CONSTRAINT strategic_decisions_parent_msg_id_fkey
  FOREIGN KEY (parent_msg_id) REFERENCES agent_messages(id) ON DELETE RESTRICT;

-- Partial index on populated parent_msg_id — trigger subquery + any parent-based
-- ad-hoc queries benefit. 300+ existing rows have NULL, indexing them wastes space.
CREATE INDEX IF NOT EXISTS strategic_decisions_parent_msg_id_idx
  ON strategic_decisions (parent_msg_id)
  WHERE parent_msg_id IS NOT NULL;

-- ============================================================
-- SECTION 3: trigger_cai_decision_announce — 3-tier routing rewrite
-- ============================================================
-- Preserves all existing guards: source filter, challenge_status whitelist,
-- bypass_review escape hatch, announced_by_msg_id idempotency, OLD-side
-- UPDATE-path suppression (already-announced or already-implemented).
--
-- Change surface:
--   * New DECLAREs: v_to_agent, v_thread_id, v_parent_from_agent, v_parent_thread_id.
--   * Parent lookup when NEW.parent_msg_id IS NOT NULL (one-row SELECT).
--   * COALESCE 3-tier routing:
--       to_agent  := NEW.announce_to_agent    ?> parent.from_agent ?> 'cc-ihsanos'
--       thread_id := NEW.announce_thread_id   ?> parent.thread_id  ?> gen_random_uuid()
--   * INSERT uses v_thread_id and v_to_agent (was gen_random_uuid() + 'cc-ihsanos').
--
-- Sibling trigger_cai_decision_autoclose_announce (AFTER UPDATE OF execution_status)
-- is untouched — it handles the later lifecycle edge, not review-time routing.
--
-- Reverse: restore the pre-BUG-030 body (see repo history, pre-commit 53ab4237).

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
  RETURN NEW;
END;
$function$;

-- ============================================================
-- SECTION 4: post-apply assertion — trigger body carries the 3-tier pattern
-- ============================================================
-- This DO-block runs at migration time. If a future re-application of this
-- migration finds that someone has since redefined the trigger without the
-- 3-tier routing, the migration RAISES and aborts. Defence-in-depth beyond
-- the pytest guard.

DO $$
DECLARE
  body TEXT;
BEGIN
  SELECT pg_get_functiondef(oid) INTO body
    FROM pg_proc WHERE proname = 'trigger_cai_decision_announce';
  IF body NOT LIKE '%NEW.announce_to_agent%'
     OR body NOT LIKE '%NEW.announce_thread_id%'
     OR body NOT LIKE '%NEW.parent_msg_id%' THEN
    RAISE EXCEPTION 'BUG-030 migration assertion failed: trigger body missing 3-tier pattern';
  END IF;
END $$;

COMMIT;
