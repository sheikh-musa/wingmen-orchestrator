-- 086_mamadah_bot_channel.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Wires the mamadah Telegram channel for real (bus #47808, op#24172/op#24173,
-- due today 18:00Z). bot_channels('mamadah') has existed since 2026-07-02
-- (migration 014) as mode='ai-responder', responder_ref='mamadah_second_brain'
-- -- that handler was NEVER ported (bus #47808: "would stay silent"). Musa
-- wants @mamadahbot in a Telegram group with him + Zahidah (his wife) to help
-- with her CL5822 coursework, Part 2 (personal). This migration repoints the
-- row to the SAME shape as 'oeh'/'angullia' (migrations 070/072): a live
-- agent-session lane (cc-mamadah, fleet_lanes row added directly, same
-- migration-free pattern lanes.sh already uses -- "a lane is a DB row, not an
-- edit to a file") answers via scripts/mamadah_send.sh, not an unported
-- ai-responder.
--
-- PRIVATE FAMILY DATA, not a client workstream: no project_governance /
-- project_governance_families rows here (no one gets a fleet-console
-- operator role out of this task; add later if that ever changes, same
-- deferral oeh's own onboarding originally took).
--
-- bot_channels('mamadah'): mode='agent-session', inject_target='mamadah' (the
-- live cc-mamadah tmux session name once lanes.sh boots it -- same convention
-- as oeh/angullia's inject_target). inject_prefix identifies the sender name
-- is already in the stored message content (two humans post here, Musa AND
-- Zahidah, unlike oeh/angullia's single client contact) so inject_prefix is
-- left generic. group_routing.agent_reviewer='cc-mamadah' (self-supervised,
-- single combined coord+builder lane, same shape as oeh/angullia).
-- allowed_chat_ids seeded with ONLY Musa's own DM id (286619815=
-- MUSA_TELEGRAM_ID, already disclosed in migration 020's header) --
-- deny-by-default per bus #47808's explicit instruction ("ONLY that one group
-- id (+ Musa's DM)"); the family group's chat id is not known yet (Musa is
-- creating it) and will be added by a follow-up UPDATE the moment it's
-- captured, never by loosening this gate speculatively. responder_ref is
-- cleared to NULL (no longer an ai-responder row). log_target stays
-- 'substrate' (unchanged) -- per bus #47808's residency ask, log_target ->
-- wingmen-personal (brrgastulcffamlbggyu) is NOT implemented anywhere in
-- nervous_system/ingest.py today (the column is read and stored on the
-- Channel object but no code path branches the destination DB on it); shipping
-- 'substrate' here is the honest minimal option, same as every other live
-- bot_channels row (oeh/angullia included), not a new gap introduced by this
-- migration. enabled stays false: the Mini's nazim-ingest polls 'mamadah' via
-- a pinned INGEST_CHANNELS entry (boot_nazim_ingest.sh, applied alongside this
-- migration) regardless of `enabled` -- shipping enabled=true would also hand
-- MAMADAH_BOT_TOKEN to the gzb hub's unscoped `WHERE enabled` poll, the same
-- dual-poller 409 class oeh/angullia's rows already avoid.
--
-- REVERT: UPDATE public.bot_channels SET mode='ai-responder', inject_target=NULL,
--         inject_prefix=NULL, responder_ref='mamadah_second_brain',
--         allowed_chat_ids='{}', group_routing='{}'::jsonb
--         WHERE channel_key = 'mamadah';

UPDATE public.bot_channels
SET
  mode            = 'agent-session',
  inject_target   = 'mamadah',
  inject_prefix   = NULL,
  responder_ref   = NULL,
  allowed_chat_ids = '{286619815}',
  group_routing   = '{"agent_phase": "supervised", "agent_reviewer": "cc-mamadah"}'::jsonb,
  updated_at      = now()
WHERE channel_key = 'mamadah';
