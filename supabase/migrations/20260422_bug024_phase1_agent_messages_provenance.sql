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

-- Reverse: ALTER TABLE strategic_decisions DROP COLUMN cai_session_id;
ALTER TABLE strategic_decisions
  ADD COLUMN cai_session_id TEXT;

-- ============================================================
-- SECTION 3: identity_allowlist table
-- ============================================================

-- Reverse: DROP TABLE identity_allowlist;
CREATE TABLE identity_allowlist (
  posted_by          TEXT NOT NULL,
  allowed_from_agent TEXT NOT NULL,
  note               TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (posted_by, allowed_from_agent)
);

-- Phase 1 starts with zero seed rows. Every message posted via service_role
-- will therefore land with from_agent_verified = NULL (untrusted).
-- Populate this table as part of Phase 2 per-key auth rollout once each
-- logical agent has its own key.
--
-- Example seed (commented — do NOT enable in Phase 1):
--   INSERT INTO identity_allowlist (posted_by, allowed_from_agent, note) VALUES
--     ('service_role', 'musa', 'Musa via Telegram relay'),
--     ('cai_supabase_mcp_key', 'cai', 'cai posting directly');

-- ============================================================
-- SECTION 4: provenance trigger
-- ============================================================

-- Reverse: DROP TRIGGER IF EXISTS trg_agent_messages_provenance ON agent_messages;
--          DROP FUNCTION IF EXISTS populate_agent_messages_provenance();
--
-- Behavior:
--   posted_by_identity <- current_user (Postgres session role — most stable
--     identity signal in Phase 1 before per-key auth lands).
--   from_agent_verified <- true iff identity_allowlist covers the pair,
--                          else NULL (never false via trigger; false is
--                          reserved for admin backfill markers).
--
-- The trigger does NOT reject the INSERT — Phase 1 is provenance, not gate.
-- Phase 2/3 will add RLS policy to enforce.

CREATE OR REPLACE FUNCTION populate_agent_messages_provenance()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  -- Only populate if caller did not explicitly set it (allows admin backfills).
  IF NEW.posted_by_identity IS NULL THEN
    NEW.posted_by_identity := current_user;
  END IF;

  -- Only populate if caller did not explicitly set it.
  IF NEW.from_agent_verified IS NULL THEN
    IF EXISTS (
      SELECT 1 FROM identity_allowlist
       WHERE posted_by = NEW.posted_by_identity
         AND allowed_from_agent = NEW.from_agent
    ) THEN
      NEW.from_agent_verified := true;
    ELSE
      NEW.from_agent_verified := NULL;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_agent_messages_provenance
  BEFORE INSERT ON agent_messages
  FOR EACH ROW
  EXECUTE FUNCTION populate_agent_messages_provenance();

-- ============================================================
-- SECTION 5: indexes
-- ============================================================

-- Reverse: DROP INDEX IF EXISTS idx_agent_messages_cai_session;
CREATE INDEX idx_agent_messages_cai_session
  ON agent_messages (cai_session_id, created_at DESC)
  WHERE from_agent = 'cai';

-- Reverse: DROP INDEX IF EXISTS idx_strategic_decisions_cai_session;
CREATE INDEX idx_strategic_decisions_cai_session
  ON strategic_decisions (cai_session_id, decided_at DESC)
  WHERE source = 'claude_ai_session';

-- ============================================================
-- SECTION 6: boot_briefing view extension
-- ============================================================

-- (Task 6 will fill this in)

-- ============================================================
-- SECTION 7: backfills
-- ============================================================

-- (Task 7 will fill this in)
