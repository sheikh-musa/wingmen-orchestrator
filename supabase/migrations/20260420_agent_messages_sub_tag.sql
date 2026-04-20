-- supabase/migrations/20260420_agent_messages_sub_tag.sql
-- B1 (CAI-RESP-053): replace string-encoded sub-tag with structural column.
-- Idempotent — safe to re-run.

ALTER TABLE agent_messages
    ADD COLUMN IF NOT EXISTS sub_tag TEXT NULL;

-- Structural impersonation guard: sub_tag must be an N-suffix of from_agent.
-- NULL is permitted (CAI writes, background jobs, legacy rows).
ALTER TABLE agent_messages
    DROP CONSTRAINT IF EXISTS agent_messages_sub_tag_family_prefix_chk;
ALTER TABLE agent_messages
    ADD CONSTRAINT agent_messages_sub_tag_family_prefix_chk
    CHECK (sub_tag IS NULL OR sub_tag LIKE from_agent || '-%');

CREATE INDEX IF NOT EXISTS idx_agent_messages_sub_tag
    ON agent_messages (from_agent, sub_tag)
    WHERE sub_tag IS NOT NULL;

COMMENT ON COLUMN agent_messages.sub_tag IS
  'Sub-identity (e.g. cc-ihsanos-3) of the CC session that wrote this row. '
  'NULL for CAI, orchestrator background jobs, and legacy rows. '
  'CHECK constraint enforces structural family match with from_agent.';
