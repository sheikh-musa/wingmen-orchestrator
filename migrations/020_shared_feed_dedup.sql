-- 020_shared_feed_dedup.sql
-- War-room live feed (WAR-ROOM-FEED-001) — cross-bot log-once dedup guard.
--
-- The war-room Telegram group has 3 member bots — operator-orch + cai-channel
-- (hub) and nazim-console (Mini) — each receiving every group message via its
-- own getUpdates. ingest_dedup (channel_key, telegram_update_id) is a PER-BOT
-- replay guard and CANNOT dedup one logical message across bots: each bot assigns
-- the message a DIFFERENT update_id.
--
-- Telegram's message.message_id is unique per chat and IDENTICAL across every bot
-- that receives it, so (chat_id, message_id) is the message's cross-bot identity.
-- This table is the ADDITIONAL guard for shared-feed chats only: the first ingest
-- loop (any of the 3 bots / EITHER machine — all share this substrate) to
-- INSERT ... ON CONFLICT DO NOTHING wins and logs; the rest skip the log entirely.
-- Result: one 'war-room'-tagged operator_messages row, no DM pollution.
-- ingest_dedup is UNCHANGED — still the per-bot replay guard (both layers apply).
--
-- Substrate table (coordination fabric), service-role-only — same posture as
-- migration 014's ingest_dedup/tg_out. Apply via direct-psycopg, NEVER db push
-- (CLAUDE.md / decision 962). Safe to apply ahead of the code cutover: the live
-- (old) ingest never references this table, so creating it is a no-op for the
-- running daemon.

CREATE TABLE IF NOT EXISTS shared_feed_dedup (
  chat_id         BIGINT NOT NULL,      -- shared group's chat_id (war-room = -5383530504)
  message_id      BIGINT NOT NULL,      -- Telegram per-chat message id — same across all member bots
  channel_key     TEXT,                 -- which bot loop won the race (audit; informational, no FK)
  operator_msg_id BIGINT,               -- the operator_messages row it produced (audit joint)
  logged_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chat_id, message_id)
);

-- Lockdown (migration-013/014 pattern: substrate = service_role only).
ALTER TABLE shared_feed_dedup ENABLE ROW LEVEL SECURITY;
-- rls-policy-exempt: shared_feed_dedup select/insert/update/delete (service-role-only substrate table)
CREATE POLICY deny_all_shared_feed_dedup ON shared_feed_dedup FOR ALL TO public USING (false);
REVOKE ALL ON shared_feed_dedup FROM PUBLIC, anon, authenticated;

-- Migration tracker.
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('20260711000000', '020_shared_feed_dedup', ARRAY[
  'CREATE TABLE shared_feed_dedup (cross-bot log-once guard: PK chat_id+message_id)',
  'RLS deny-all + REVOKE PUBLIC/anon/authenticated (service-role-only substrate table)'
]) ON CONFLICT (version) DO NOTHING;
