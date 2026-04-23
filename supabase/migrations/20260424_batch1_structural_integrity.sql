-- Batch 1 Structural Integrity Bundle
-- Ships 4 P1 fixes per CAI msg #620 consolidated briefing:
--   BUG-024 Phase 1B — agent_status.base_agent_id FK + backfill + prefix CHECK
--   BUG-024 Phase 1C = BUG-032 — strategic_decisions provenance + trigger
--   BUG-031 — is_test on strategic_decisions + enforcer test_mode parameter
--   BUG-029 Part A — is_test on agent_messages
--
-- Parent decisions: BUG-024, BUG-029, BUG-031, BUG-032.
-- Rulings: CAI-RESP-059 (Phase 1B clearance), CAI-RESP-072 (Phase 1 patterns),
--          CAI-RESP-077 (test-mutates-prod root cause), msg #620 (bundling),
--          CAI-RESP-080 (Open Q1/Q2/Q3 + Refinements 1/2).
--
-- Sections:
--   1. agent_status + base_agent_id FK column
--   2. agent_status backfill from agent_id pattern
--   3. agent_status base_agent_id NOT NULL + prefix CHECK
--   4. strategic_decisions provenance columns
--   5. populate_strategic_decisions_provenance trigger
--   6. is_test columns on strategic_decisions + agent_messages
--   7. enforce_challenge_window_timeouts REPLACE with test_mode parameter
--   8. boot_briefing view extension — unverified_decisions section
--
-- Pre-flight verified (see plan Task 1):
--   * identity_allowlist exists (Phase 1A shipped, zero-seed)
--   * agent_messages.sub_tag CHECK name = agent_messages_sub_tag_family_prefix_chk
--   * agents table has 7 rows (broadcast, cai, cc-cosem, cc-ihsanos, cc-scholar,
--     cc-web, musa) — FK target
--   * agent_status has 5 rows (cc-cosem-1, cc-ihsanos-1/2/3, cc-scholar-1) — backfill
--     predictable via regexp_replace

-- ============================================================
-- SECTION 1: agent_status + base_agent_id FK column (nullable initial)
-- ============================================================

-- Reverse: ALTER TABLE agent_status DROP COLUMN base_agent_id;
-- Add nullable first so existing rows don't violate NOT NULL during CREATE.
-- Section 2 backfills; Section 3 enforces NOT NULL + prefix CHECK.
ALTER TABLE agent_status
  ADD COLUMN base_agent_id TEXT REFERENCES agents(id);

-- ============================================================
-- SECTION 2: agent_status backfill base_agent_id from agent_id pattern
-- ============================================================

-- Parse sub-identity suffix `-N` where N is numeric. E.g., cc-ihsanos-3 → cc-ihsanos.
-- Current 5 rows: cc-cosem-1, cc-ihsanos-1, cc-ihsanos-2, cc-ihsanos-3, cc-scholar-1.
-- All parse cleanly to known agent IDs (cc-cosem, cc-ihsanos, cc-scholar) which
-- exist in agents table.
UPDATE agent_status
   SET base_agent_id = regexp_replace(agent_id, '-[0-9]+$', '')
 WHERE base_agent_id IS NULL;

-- ============================================================
-- SECTION 3: agent_status base_agent_id NOT NULL + prefix CHECK
-- ============================================================

-- After Section 2 backfill, enforce NOT NULL. Any future INSERT must supply
-- base_agent_id (auto_agent_id.py populates explicitly — see Task 12).
-- Reverse: ALTER TABLE agent_status ALTER COLUMN base_agent_id DROP NOT NULL;
ALTER TABLE agent_status
  ALTER COLUMN base_agent_id SET NOT NULL;

-- CAI-RESP-080 Open Q2 ruling: FK alone allows cross-family prefix mismatch
-- (e.g., agent_id='cc-ihsanos-99' with base_agent_id='cc-scholar' — both exist
-- in agents table, so FK accepts; CHECK catches). Structural enforcement
-- preserves the family-identity invariant at INSERT time rather than relying on
-- auto_agent_id.py app-layer discipline.
-- Reverse: ALTER TABLE agent_status DROP CONSTRAINT agent_status_base_agent_id_prefix_chk;
ALTER TABLE agent_status
  ADD CONSTRAINT agent_status_base_agent_id_prefix_chk
  CHECK (base_agent_id = regexp_replace(agent_id, '-[0-9]+$', ''));

-- ============================================================
-- SECTION 4: strategic_decisions provenance columns
-- ============================================================

-- BUG-032 / BUG-024 Phase 1C: parallel pattern to agent_messages Phase 1A.
-- Reverse: ALTER TABLE strategic_decisions DROP COLUMN posted_by_identity;
--          ALTER TABLE strategic_decisions DROP COLUMN decided_by_verified;
ALTER TABLE strategic_decisions
  ADD COLUMN posted_by_identity TEXT;

-- Nullable: NULL = unverified (Phase 1 default), true = allowlist match,
-- false = explicit admin mark (reserved; no auto-false via trigger).
ALTER TABLE strategic_decisions
  ADD COLUMN decided_by_verified BOOLEAN;

-- ============================================================
-- SECTION 5: populate_strategic_decisions_provenance trigger
-- ============================================================

-- BUG-032 per CAI-RESP-077: parallel pattern to populate_agent_messages_provenance.
-- Fires on INSERT OR UPDATE OF decided_by, challenge_status — UPDATE coverage is
-- critical because the bulk-flip failure mode is UPDATE not INSERT.
-- Reuses identity_allowlist from BUG-024 Phase 1A (zero-seed in Phase 1; Phase 2
-- per-key auth populates).
-- Reverse: DROP TRIGGER IF EXISTS trg_strategic_decisions_provenance ON strategic_decisions;
--          DROP FUNCTION IF EXISTS populate_strategic_decisions_provenance();

CREATE OR REPLACE FUNCTION populate_strategic_decisions_provenance()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  -- Only populate if caller did not explicitly set it (admin backfills preserved).
  -- PHASE 2 REWRITE: when per-agent API keys land, replace `current_user` with
  --   current_setting('request.jwt.claims', true)::json ->> 'agent_id'
  -- Trigger signature stays the same; only this one line changes.
  IF NEW.posted_by_identity IS NULL THEN
    NEW.posted_by_identity := current_user;
  END IF;

  IF NEW.decided_by_verified IS NULL THEN
    IF EXISTS (
      SELECT 1 FROM identity_allowlist
       WHERE posted_by = NEW.posted_by_identity
         AND allowed_from_agent = NEW.decided_by
    ) THEN
      NEW.decided_by_verified := true;
    ELSE
      NEW.decided_by_verified := NULL;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_strategic_decisions_provenance
  BEFORE INSERT OR UPDATE OF decided_by, challenge_status ON strategic_decisions
  FOR EACH ROW
  EXECUTE FUNCTION populate_strategic_decisions_provenance();

-- ============================================================
-- SECTION 6: is_test columns on strategic_decisions + agent_messages
-- ============================================================

-- (Task 6 will fill this in)

-- ============================================================
-- SECTION 7: enforce_challenge_window_timeouts REPLACE with test_mode parameter
-- ============================================================

-- (Task 7 will fill this in)

-- ============================================================
-- SECTION 8: boot_briefing view extension
-- ============================================================

-- (Task 8 will fill this in)
