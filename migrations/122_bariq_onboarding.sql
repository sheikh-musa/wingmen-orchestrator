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
-- --store ihsanos multi-tenant DB ceayjeamtmcyzzvqflus
--
-- REVERT: DELETE FROM public.bot_channels WHERE channel_key = 'bariq';
INSERT INTO public.bot_channels
  (channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,
   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset,
   audience, owner_lane)
VALUES
  ('bariq', 'WINGMENDEVBOT_TOKEN', 'agent-session', 'bariq',
   '🏠 Bariq (Afiq): ', NULL,
   '{0}', '{}',
   '{"agent_phase": "supervised", "agent_reviewer": "orch-console", "powers": ["reply_text"], "self_description": "Bariq Employment site-edit channel: send approved site changes (hero headline/description, contact details, testimonials) and I apply them to the live content, then confirm here with a preview link. Nothing else is handled on this channel."}'::jsonb, 'bariq', 'substrate', false, NULL,
   'client', 'cc-bariq')
ON CONFLICT (channel_key) DO NOTHING;
