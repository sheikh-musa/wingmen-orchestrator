-- 122_bariq_onboarding.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Rendered by scripts/onboard_client_channel.py (TG2.0 onboarding-automation,
-- op#31450/#31473). NOT applied yet -- bring this + its sha256 to orch-console's
-- gate before apply_migration.py --gate runs it.
--
-- audience=client, host=mini, phase=supervised, token=WINGMENDEVBOT_TOKEN
-- SHARED token per Musa's settled one-bot model (op#33571, 2026-10-24; supersedes
-- bus #71813) -- isolation is per-channel: private chat_id binding (no overlap
-- with siblings on this token), self-description free of sibling names.
-- --store none
--
-- op#34846 SELF-SERVE INVITE: the channel ships with an EMPTY allowlist;
-- the first '/start <token>' on the t.me deep link binds the chat (ingest.py
-- try_invite_bind), consumes the single-use invite and replies with this
-- channel's own self_description. The PLAINTEXT token is NOT in this file
-- (sha256 at rest only); it lives solely in the render's stdout + a 0600
-- link file under .secrets/ (gitignored). Relay it out-of-band, never
-- over the bus. Expiry anchors at APPLY time (now()+interval), not render.
--
-- Asserts (lint: the REVOKE below requires them):
-- assert: no_table_privilege anon public.bot_channel_invites SELECT
-- assert: no_table_privilege anon public.bot_channel_invites INSERT
-- assert: no_table_privilege anon public.bot_channel_invites UPDATE
-- assert: no_table_privilege anon public.bot_channel_invites DELETE
-- assert: no_table_privilege anon public.bot_channel_invites TRUNCATE
-- assert: no_table_privilege authenticated public.bot_channel_invites SELECT
-- assert: no_table_privilege authenticated public.bot_channel_invites INSERT
-- assert: no_table_privilege authenticated public.bot_channel_invites UPDATE
-- assert: no_table_privilege authenticated public.bot_channel_invites DELETE
-- assert: no_table_privilege authenticated public.bot_channel_invites TRUNCATE
-- REVERT: DELETE FROM public.bot_channels WHERE channel_key = 'bariq';
-- REVERT: DELETE FROM public.bot_channel_invites WHERE channel_key = 'bariq';
INSERT INTO public.bot_channels
  (channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,
   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset,
   audience, owner_lane)
VALUES
  ('bariq', 'WINGMENDEVBOT_TOKEN', 'agent-session', 'bariq',
   '🏠 Bariq (Afiq): ', NULL,
   '{}', '{}',
   '{"agent_phase": "supervised", "agent_reviewer": "orch-console", "powers": ["reply_text"], "self_description": "Bariq Employment site-edit channel: send approved site changes (hero headline/description, contact details, testimonials) and I apply them to the live content, then confirm here with a preview link. Nothing else is handled on this channel."}'::jsonb, 'bariq', 'substrate', false, NULL,
   'client', 'cc-bariq')
ON CONFLICT (channel_key) DO NOTHING;

-- Single-use, expiring invite keyed to this channel (op#34846). The
-- lock-down block is the 014 substrate pattern: service-role only, RLS
-- + deny-all + REVOKEs (new tables get anon/authenticated ALL by
-- pg_default_acl implicitly — see the assert header above).
CREATE TABLE IF NOT EXISTS public.bot_channel_invites (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  channel_key    TEXT NOT NULL REFERENCES public.bot_channels(channel_key)
                 ON DELETE CASCADE,
  token_hash     TEXT NOT NULL UNIQUE,
  expires_at     TIMESTAMPTZ NOT NULL,
  used_at        TIMESTAMPTZ,
  bound_chat_id  BIGINT,
  created_by     TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.bot_channel_invites ENABLE ROW LEVEL SECURITY;
CREATE POLICY deny_all_bot_channel_invites ON public.bot_channel_invites
  FOR ALL TO PUBLIC USING (false) WITH CHECK (false);
REVOKE ALL ON public.bot_channel_invites FROM PUBLIC, anon, authenticated;

INSERT INTO public.bot_channel_invites
  (channel_key, token_hash, expires_at, created_by)
VALUES
  ('bariq', '1d76d967baad411920170e281eb37e2bef95d0edb9ccd97d194349ee1a3dc32a', now() + make_interval(hours => 72), 'cc-bariq')
ON CONFLICT (token_hash) DO NOTHING;
