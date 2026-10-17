-- 111_brandit_coffeemedia_testbed_wire.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Musa's TG 2.0 one-bot-many-clients routing proof (op#31405/#31416, bus
-- #70415). Musa created a 2nd throwaway chat on the SAME WINGMENDEVBOT_TOKEN
-- as 'coffeemedia' -- a group "brandit/coffeemedia", 2 members only (Musa +
-- wingmendevbot as admin), zero client exposure. Goal: prove coffeemedia
-- (Musa's existing DM, chat_id 286619815) and this new group each route to
-- the right handler by chat_id with zero cross-wire, reusing the
-- group_by_token/match_channel_in_group dispatch already shipped for
-- one-bot-many-clients (op#31298/31307).
--
-- chat_id -5328962086 was DISCOVERED WITHOUT any manual getUpdates call on
-- the live WINGMENDEVBOT_TOKEN poller (which would itself 409 the Mini's
-- pinned poller, bus #70399's exact incident) -- the Mini's existing
-- 'coffeemedia' pin already receives every update on this token's group and
-- durably logs/captures an unmatched chat (gate_allows() false) same as any
-- other unrecognised sender: operator_messages id 31415 (group_chat_created
-- system event) and id 31422 (text "test"), both tag='coffeemedia' (today's
-- first/only row in the group, per match_channel_in_group's documented
-- fallback-to-first-row-for-audit behavior), chat_id=-5328962086, both
-- 2026-10-17. No row claims that chat_id yet (checked: SELECT channel_key
-- FROM bot_channels WHERE -5328962086 = ANY(allowed_chat_ids) -> empty).
--
-- Privacy-mode / bot-access concern (Musa's screenshot flagged wingmendevbot
-- admin as "no access to messages"): moot -- operator_messages id 31422
-- already proves the bot DID receive a plain group text message, so no
-- BotFather /setprivacy or admin-rights change is needed before this ships.
--
-- mode='log-and-route' (not 'agent-session'): this is a routing-dispatch
-- proof, not a live interactive agent -- no new tmux lane/session needed.
-- The proof is the operator_messages audit trail: a message from -5328962086
-- lands with tag='brandit-coffeemedia' while a message from 286619815 (the
-- real coffeemedia DM) keeps tag='coffeemedia' -- two disjoint allowed_chat_ids
-- rows sharing one token, resolved correctly by match_channel_in_group.
--
-- enabled=false: this channel_key is being added to the Mini's INGEST_CHANNELS
-- pin list (scripts/boot_nazim_ingest.sh, same commit) alongside 'coffeemedia'
-- -- the Mini's single poller owns BOTH rows in this token's group, so the
-- gzb hub's unscoped `WHERE enabled` poll never touches WINGMENDEVBOT_TOKEN at
-- all. Same invariant 'oeh'/'angullia'/'coffeemedia' already rely on
-- (migrations 070/072/109; bus #43833; tests/migrations/
-- test_ingest_channels_enabled_false.py enforces this statically for both
-- files together). This is the single-poller answer to bus #70415's open
-- question (1) -- NOT unpinning coffeemedia to the hub, since that would be
-- two hosts needing a restart instead of one and a topology change beyond
-- what this throwaway proof needs.
--
-- NOT APPLIED YET -- drafted for orch-console's gate per bus #70415's explicit
-- "bring the wiring plan to my gate... before enabling anything." Dry-run
-- sha256 computed and reported on the bus; apply only after a gate grant
-- citing that hash, via the usual gated apply_migration.py (never
-- `supabase db push`, decision 962).
--
-- REVERT: DELETE FROM public.bot_channels WHERE channel_key = 'brandit-coffeemedia';

INSERT INTO public.bot_channels
  (channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,
   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset,
   audience)
VALUES
  ('brandit-coffeemedia', 'WINGMENDEVBOT_TOKEN', 'log-and-route', NULL,
   NULL, NULL,
   '{-5328962086}', '{}', '{}'::jsonb,
   'brandit-coffeemedia', 'substrate', false, NULL,
   'internal')
ON CONFLICT (channel_key) DO NOTHING;
