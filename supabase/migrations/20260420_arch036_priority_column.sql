-- ARCH-036: Priority column on narrowed agent_messages.
--
-- Parent: ARCH-036 (strategic_decisions). References:
--   ARCH-035 (narrowed agent_messages channel)
--   CAI-RESP-047 (Q1 P3 suppression load-bearing, Q2 uniform P2 default)
--
-- Schema:
--   1. priority column TEXT NOT NULL DEFAULT 'P2' CHECK IN ('P0','P1','P2','P3')
--   2. Anti-inflation CHECK: (priority IN ('P2','P3') OR requires_response = true)
--      — P0/P1 structurally require requires_response=true.
--   3. Partial index on (priority, created_at) WHERE read_at IS NULL
--      — boot-briefing open-inbox sort path.
--
-- Backfill: every existing row receives DEFAULT 'P2'. Read rows keep P2
-- permanently (historical, irrelevant). Unread rows sort at the bottom
-- of the P2 band by created_at.

ALTER TABLE agent_messages
  ADD COLUMN priority TEXT NOT NULL DEFAULT 'P2'
  CHECK (priority IN ('P0','P1','P2','P3'));

ALTER TABLE agent_messages
  ADD CONSTRAINT agent_messages_priority_requires_response_check
  CHECK (priority IN ('P2','P3') OR requires_response = true);

CREATE INDEX idx_agent_messages_open_by_priority
  ON agent_messages (priority, created_at)
  WHERE read_at IS NULL;
