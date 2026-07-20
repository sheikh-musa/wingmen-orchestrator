-- 020: operator_messages sender-identity capture.
-- ADDITIVE + BACKWARD-COMPATIBLE. Telegram updates carry the individual sender in
-- `message.from` (id/username/first_name/last_name), but 007 only stored the CHAT
-- (chat_id), so a message from a GROUP (negative chat_id, e.g. -5557014342) could
-- not be attributed to the human who sent it (e.g. Hafiz) — everything read as the
-- operator (Musa, id=286619815=MUSA_TELEGRAM_ID). These three nullable columns let
-- ingest.py record WHO sent each inbound row. All nullable: pre-existing rows and
-- any update lacking `message.from` (channel posts, service messages) stay NULL.
-- Applied live via direct psycopg (decision-962; NEVER `supabase db push`).
-- Idempotent: ADD COLUMN IF NOT EXISTS.
ALTER TABLE operator_messages ADD COLUMN IF NOT EXISTS from_user_id  text;  -- message.from.id (Telegram user id, as text)
ALTER TABLE operator_messages ADD COLUMN IF NOT EXISTS from_username text;  -- message.from.username (no leading @), may be NULL
ALTER TABLE operator_messages ADD COLUMN IF NOT EXISTS from_name     text;  -- first_name + ' ' + last_name, trimmed
