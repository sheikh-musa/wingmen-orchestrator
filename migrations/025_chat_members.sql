-- 025: chat_members — self-learning roster of Telegram senders, so every
-- inbound message can be attributed to WHO sent it (the operator, Shen, a
-- client) instead of a bare chat_id. ingest.py upserts a row per
-- (chat_id, user_id) as messages arrive (best-effort; a failure there never
-- blocks the sacred operator_messages log). known_label is human-curated (e.g.
-- 'operator') and is NEVER overwritten by the upsert. Applied live via direct
-- psycopg (decision-962: never `supabase db push`).
CREATE TABLE IF NOT EXISTS chat_members (
  chat_id       text NOT NULL,
  user_id       text NOT NULL,
  username      text,
  display_name  text,
  known_label   text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  msg_count     integer NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, user_id)
);

-- Seed the operator's own DM (MUSA_TELEGRAM_ID) so the authority principal is
-- labelled from day one, before his first post-migration message. Idempotent.
INSERT INTO chat_members (chat_id, user_id, known_label)
VALUES ('286619815', '286619815', 'operator')
ON CONFLICT (chat_id, user_id) DO NOTHING;

GRANT SELECT ON chat_members TO console_readonly;
