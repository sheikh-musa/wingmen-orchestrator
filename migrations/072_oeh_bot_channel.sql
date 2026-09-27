-- 072_oeh_bot_channel.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Wires the OEH client Telegram channel (Musa op#22521; bus #43713, orch-console
-- P1 review_request). cc-oeh is ALREADY an onboarded fleet lane (migration
-- 071_oeh_lane.sql), which deliberately deferred this exact row: "NO project_
-- governance/bot channel yet: Musa hasn't said anyone other than him edits it,
-- so I'll add that later if needed." Musa is now creating the Telegram group
-- with the client user (Sya) + @oehgroup_bot live -- this is that "later". Pure
-- data: bot_channels already exists on the live substrate -- nothing here
-- touches privileges, so no `-- assert:` lines are needed (house convention
-- per migrations 065/069/070 headers).
--
-- SHAPE (mirrors migration 070/angullia's bot_channels row exactly -- same
-- single-operator-support, agent-session shape). Deliberately does NOT add a
-- project_governance or project_governance_families row: bus #43713's explicit
-- 5-point instruction only asks for the bot_channels row + ingest wiring + send
-- script, not full project governance, and 071's own deferral note said "add
-- later if needed" -- this migration is scoped to exactly what was asked, not
-- a silent re-run of 070's full shape.
--   bot_channels('oeh'): token_env_key=OEH_BOT_TOKEN (already in the Mini .env
--   + vault 'oeh_bot_token', per bus #43713 -- getMe verified by Musa). mode=
--   'agent-session', inject_target='oeh' (the live cc-oeh tmux session name,
--   confirmed via `tmux ls` -- same convention as angullia's inject_target=
--   'angullia'). inject_prefix identifies the client user by name (Sya), same
--   pattern as angullia's '(Rhaihan)'. group_routing.agent_reviewer='cc-oeh'
--   (supervised phase, same shape as angullia/cc-angullia). enabled=false,
--   allowed_chat_ids='{}' (deny-by-default) -- the bot exists but no chat/group
--   is wired yet; orch-console will set allowed_chat_ids once the group pings
--   (bus #43713: "I'll set it once the group pings").
--
-- REVERT: DELETE FROM public.bot_channels WHERE channel_key = 'oeh';

-- ---------------------------------------------------------------------------
-- 1. bot_channels -- disabled, deny-by-default, modeled on the live 'angullia' row
-- ---------------------------------------------------------------------------
INSERT INTO public.bot_channels
  (channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,
   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset)
VALUES
  ('oeh', 'OEH_BOT_TOKEN', 'agent-session', 'oeh',
   '🎤 OEH (Sya): ', NULL,
   '{}', '{}', '{"agent_phase": "supervised", "agent_reviewer": "cc-oeh"}'::jsonb,
   'oeh', 'substrate', false, NULL)
ON CONFLICT (channel_key) DO NOTHING;
