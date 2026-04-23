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
