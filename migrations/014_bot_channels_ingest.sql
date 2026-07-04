-- 014_bot_channels_ingest.sql
-- Unified bot ingest — implementing BOT-INGEST-TOPOLOGY-001 per CAI-RESP-357
-- (ratified w/ amendments A1/A2/A3, msg #5023; filename registered #5027).
-- HELD for cai grant at window close 2026-07-02 21:05Z — do NOT apply before.
-- Target: monolith/substrate DB (bot_channels/tg_out are coordination fabric,
-- SUB-classified — CAI-RESP-357 ask (d)). Apply via direct-psycopg, never db push.
--
-- Amendment mapping:
--   A1 idempotent ingest ..... UNIQUE (channel_key, telegram_update_id) on the log
--                              (ingest_dedup table keyed off getUpdates update_id;
--                              nudge + responder dispatch key off the deduped row)
--   A2 transport/brains ...... mode + responder_ref columns; responder execution is
--                              a separate drain unit reading tg_out/operator_messages
--   A3 Zahidah log dest ...... log_target column NOW (her channels may point at the
--                              wingmen-personal project post ZAHIDAH-DATA-ISOLATION-001)

-- ── 1. Channel registry: a bot = a config row ─────────────────────────────────
CREATE TABLE bot_channels (
  channel_key        TEXT PRIMARY KEY,          -- 'operator-orch','cai-channel','gazzabyte-irsyad','mamadah',...
  token_env_key      TEXT NOT NULL,             -- .env var NAME (indirection — the token itself NEVER in the DB)
  mode               TEXT NOT NULL CHECK (mode IN ('agent-session','ai-responder','log-and-route')),
  inject_target      TEXT,                      -- tmux session for agent-session mode ('orch','cai')
  inject_prefix      TEXT,                      -- e.g. '📱 Operator (Telegram): '
  responder_ref      TEXT,                      -- ai-responder mode: handler/persona key for the drain unit
  allowed_chat_ids   BIGINT[] NOT NULL DEFAULT '{}',  -- deny-by-default: empty accepts NOTHING
  allowed_usernames  TEXT[]  NOT NULL DEFAULT '{}',   -- scoped-partner auth (Desmond Shen pattern)
  group_routing      JSONB   NOT NULL DEFAULT '{}'::jsonb,
  channel_tag        TEXT NOT NULL,             -- tag stamped on operator_messages rows
  log_target         TEXT NOT NULL DEFAULT 'substrate',  -- A3: per-channel durable-log destination
  enabled            BOOLEAN NOT NULL DEFAULT false,
  poll_offset        BIGINT,                    -- getUpdates cursor IN THE DB (survives host moves)
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT agent_session_needs_target CHECK (mode <> 'agent-session' OR inject_target IS NOT NULL),
  CONSTRAINT ai_responder_needs_ref     CHECK (mode <> 'ai-responder'  OR responder_ref IS NOT NULL)
);

-- ── 2. A1: idempotency ledger — at-least-once redelivery never double-processes ─
-- The un-acked-offset fail-safe (REQUIRED per CAI-RESP-357) makes Telegram redeliver
-- after a DB outage; this table is the mandatory pair. INSERT ... ON CONFLICT DO
-- NOTHING is the gate: only the winning insert logs/nudges/dispatches.
CREATE TABLE ingest_dedup (
  channel_key        TEXT NOT NULL REFERENCES bot_channels(channel_key) ON DELETE CASCADE,
  telegram_update_id BIGINT NOT NULL,
  operator_msg_id    BIGINT,                    -- the operator_messages row it produced (audit joint)
  processed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (channel_key, telegram_update_id)
);

-- ── 3. Outbound queue — at-least-once outbound with audit, symmetric w/ inbound ─
CREATE TABLE tg_out (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  channel_key  TEXT NOT NULL REFERENCES bot_channels(channel_key),
  chat_id      BIGINT,                          -- NULL = channel's default chat (operator)
  text         TEXT,
  file_path    TEXT,                            -- host path for file sends (gateway validates)
  reply_to_operator_msg_id BIGINT,              -- thread-back reference
  status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','dead')),
  attempts     INT  NOT NULL DEFAULT 0,
  last_error   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at      TIMESTAMPTZ,
  CONSTRAINT text_or_file CHECK (text IS NOT NULL OR file_path IS NOT NULL)
);
CREATE INDEX tg_out_pending_idx ON tg_out(status, created_at) WHERE status = 'queued';

-- ── 4. Lockdown (migration-013 pattern: substrate = service_role only) ────────
ALTER TABLE bot_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_dedup ENABLE ROW LEVEL SECURITY;
ALTER TABLE tg_out       ENABLE ROW LEVEL SECURITY;
-- rls-policy-exempt: bot_channels select/insert/update/delete (service-role-only substrate table)
-- rls-policy-exempt: ingest_dedup select/insert/update/delete (service-role-only substrate table)
-- rls-policy-exempt: tg_out select/insert/update/delete (service-role-only substrate table)
CREATE POLICY deny_all_bot_channels ON bot_channels FOR ALL TO public USING (false);
CREATE POLICY deny_all_ingest_dedup ON ingest_dedup FOR ALL TO public USING (false);
CREATE POLICY deny_all_tg_out       ON tg_out       FOR ALL TO public USING (false);
REVOKE ALL ON bot_channels, ingest_dedup, tg_out FROM PUBLIC, anon, authenticated;

-- ── 5. Seed: the live channels first (P1), everything else disabled until its
--     phase. Tokens stay in .env; operator flips enabled as cutover proceeds. ──
INSERT INTO bot_channels (channel_key, token_env_key, mode, inject_target, inject_prefix,
                          responder_ref, allowed_chat_ids, channel_tag, log_target, enabled)
VALUES
  ('operator-orch',   'WINGMEN_BOT_TOKEN',        'agent-session', 'orch', '📱 Operator (Telegram): ',
   NULL, '{}', 'orch-channel',    'substrate', false),
  ('cai-channel',     'CAI_TELEGRAM_BOT_TOKEN',   'agent-session', 'cai',  '📱 Operator (Telegram→cai): ',
   NULL, '{}', 'cai-channel',     'substrate', false),
  ('gazzabyte-irsyad','IRSYAD_SUPPORT_BOT_TOKEN', 'log-and-route', NULL,   '📩 Irsyad-Support (Gazzabyte group): ',
   NULL, '{}', 'gazzabyte-irsyad','substrate', false),
  ('mamadah',         'MAMADAH_BOT_TOKEN',        'ai-responder',  NULL,   NULL,
   'mamadah_second_brain', '{}', 'mamadah',     'substrate', false),   -- A3: log_target flips to wingmen-personal at P3 cutover
  ('nutri-study',     'NUTRI_SCIENCE_BOT_TOKEN',  'ai-responder',  NULL,   NULL,
   'nutri_study',          '{}', 'nutri-study', 'substrate', false);
-- allowed_chat_ids deliberately seeded EMPTY (accept-nothing): the cutover step for
-- each channel populates its allowlist from .env/verified sources, then enables it.
-- Seeding real chat ids belongs to the operational cutover, not the schema migration.

-- ── 6. Migration tracker ──────────────────────────────────────────────────────
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('20260702000000', '014_bot_channels_ingest', ARRAY[
  'CREATE TABLE bot_channels (channel registry, config-row-per-bot, A3 log_target)',
  'CREATE TABLE ingest_dedup (A1 idempotency: UNIQUE channel_key+telegram_update_id)',
  'CREATE TABLE tg_out (outbound queue, at-least-once + audit)',
  'RLS deny-all + REVOKE PUBLIC/anon/authenticated on all three',
  'Seed 5 channels DISABLED with empty allowlists'
]) ON CONFLICT (version) DO NOTHING;
