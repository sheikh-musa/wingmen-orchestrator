-- 007: operator_messages — durable log of the operator<->orchestrator Telegram
-- bridge (both directions), so the conversation is never lost and stays coherent
-- across the live-tmux and headless incarnations of cc-orchestrator. Read-only
-- to the console role. Applied live via direct psycopg (decision-962).
CREATE TABLE IF NOT EXISTS operator_messages (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  direction   text NOT NULL CHECK (direction IN ('inbound','outbound')),
  channel     text NOT NULL DEFAULT 'telegram',
  chat_id     text,
  tag         text,
  text        text NOT NULL,
  delivered   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS operator_messages_created_idx ON operator_messages (created_at DESC);
GRANT SELECT ON operator_messages TO console_readonly;
