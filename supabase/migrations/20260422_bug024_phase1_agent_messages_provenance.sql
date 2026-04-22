-- BUG-024 Phase 1 — agent_messages provenance layer + CAI-RESP-060 amendment folded
--
-- Parent: BUG-024. References:
--   CAI-RESP-059 (Phase 1 = agent_messages provenance; Phase 1B = agent_status FK)
--   CAI-RESP-060 (amendment: fold cai_session_id + boot_briefing extension into same migration)
--   ARCH-040 (fragmented-cai governance drift — the problem cai_session_id solves)
--   cc-ihsanos msg #543 (minor challenge: time-window boot_briefing text at 14 days)
--
-- Sections:
--   1. agent_messages: posted_by_identity, from_agent_verified, cai_session_id
--   2. strategic_decisions: cai_session_id
--   3. identity_allowlist table + seed
--   4. Trigger: populate_agent_messages_provenance (BEFORE INSERT)
--   5. Indexes
--   6. boot_briefing view extension (time-windowed per msg #543)
--   7. Backfills (msg 252 from_agent_verified=false; cai_session_id NULL explicit)
--
-- Rollback: every ALTER / CREATE has a reverse operation documented inline
-- as a comment above each statement. No rollback script is committed
-- because we ship forward-only and re-migrate if needed.

-- ============================================================
-- SECTION 1: agent_messages provenance columns
-- ============================================================

-- Reverse: ALTER TABLE agent_messages DROP COLUMN posted_by_identity;
ALTER TABLE agent_messages
  ADD COLUMN posted_by_identity TEXT;

-- Reverse: ALTER TABLE agent_messages DROP COLUMN from_agent_verified;
-- Nullable: NULL = unverified (Phase 1 default), true = allowlist-matched,
-- false = explicit admin mark (e.g. msg 252 known impersonation).
ALTER TABLE agent_messages
  ADD COLUMN from_agent_verified BOOLEAN;

-- Reverse: ALTER TABLE agent_messages DROP COLUMN cai_session_id;
-- Nullable: NULL on pre-tracking rows, populated via app convention
-- (cai opens every chat with 'cai-YYYYMMDD-topic' identifier).
ALTER TABLE agent_messages
  ADD COLUMN cai_session_id TEXT;

-- ============================================================
-- SECTION 2: strategic_decisions cai_session_id
-- ============================================================

-- (Task 5 will fill this in)

-- ============================================================
-- SECTION 3: identity_allowlist table
-- ============================================================

-- (Task 4 will fill this in)

-- ============================================================
-- SECTION 4: provenance trigger
-- ============================================================

-- (Task 4 will fill this in)

-- ============================================================
-- SECTION 5: indexes
-- ============================================================

-- (Task 5 will fill this in)

-- ============================================================
-- SECTION 6: boot_briefing view extension
-- ============================================================

-- (Task 6 will fill this in)

-- ============================================================
-- SECTION 7: backfills
-- ============================================================

-- (Task 7 will fill this in)
