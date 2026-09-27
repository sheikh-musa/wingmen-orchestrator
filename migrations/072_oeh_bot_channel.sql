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
-- data: bot_channels/project_governance/project_governance_families already
-- exist on the live substrate -- nothing here touches privileges, so no
-- `-- assert:` lines are needed (house convention per migrations 065/069/070
-- headers).
--
-- SHAPE (mirrors migration 070/angullia's shape exactly). This migration
-- originally shipped bot_channels ONLY (bus #43713's explicit 5-point
-- instruction covered just that + ingest wiring + send script), but three
-- same-day follow-up decisions from orch-console widened the scope to match
-- 070's full shape before this PR merged:
--   - bus #43719 (Musa op#22647): Sya (she/her, head of OEH, will manage the
--     website) gets the fleet-console operator role, same as Fazlie has on
--     cosem-tdu -- requires a project_governance('oeh') row to exist.
--   - bus #43724: orch-console checked live data and found BOTH angullia and
--     cosem-tdu have project_governance_families rows, and without one
--     cc-oeh's work would never resolve to project 'oeh' for governance
--     gating -- so that block is required too, not optional polish.
--   - bus #43775: the OEH Telegram group now exists and is LIVE (chat id
--     -5585966657, group "OEH Group Bot"). orch-console already sent Sya
--     (@sya_hadi) the welcome message via oeh_send.sh, and her reply is
--     sitting in Telegram's update queue (~24h retention) until ingest
--     actually polls the 'oeh' channel -- so this migration wires the real
--     chat id and enables the channel now, not as a manual follow-up.
--   bot_channels('oeh'): token_env_key=OEH_BOT_TOKEN (already in the Mini .env
--   + vault 'oeh_bot_token', per bus #43713 -- getMe verified by Musa). mode=
--   'agent-session', inject_target='oeh' (the live cc-oeh tmux session name,
--   confirmed via `tmux ls` -- same convention as angullia's inject_target=
--   'angullia'). inject_prefix identifies the client user by name (Sya), same
--   pattern as angullia's '(Rhaihan)'. group_routing.agent_reviewer='cc-oeh'
--   (supervised phase, same shape as angullia/cc-angullia). allowed_chat_ids=
--   '{-5585966657}' wires the live group, but enabled=false (bus #43833
--   correction, superseding the original #43775 enabled=true): 'oeh' is
--   PINNED in the Mini's nazim-ingest INGEST_CHANNELS (boot_nazim_ingest.sh),
--   which polls that channel regardless of `enabled` -- shipping enabled=true
--   would ALSO make the gzb hub's ingest (which polls every `enabled` row,
--   no INGEST_CHANNELS scoping) long-poll the same OEH_BOT_TOKEN, the exact
--   dual-poller 409 class angullia's row already avoids by staying
--   enabled=false. The Mini's pinned poller does not need `enabled=true` to
--   see this channel.
--   project_governance('oeh'): cai_enabled=false, money_clearance_enabled=false,
--   operators=[] still (Sya's Telegram user id arrives when she first posts --
--   normalize_operators() requires a real id, same "empty array, not a
--   placeholder" pattern as angullia's Rhaihan row; orch-console adds it once
--   she posts), channels=["-5585966657"] (bus #43775 -- same JSON-array-of-
--   string-chat_ids shape as migration 063's irsyad/cosem seed rows), residency_ack
--   left NULL (website content only, no personal data today -- Musa will record
--   the ack if/when that changes, per #43719).
--   project_governance_families('oeh'): priority=10, mirroring angullia's
--   convention -- a standalone single-word project with no broader superset
--   family to out-rank (unlike cosem-tdu's priority=5 carve against 'cosem' at
--   10). Pattern '%/oeh%' (not bare '%oeh%') checked against every live
--   fleet_lanes/agents row per #43724's own instruction to verify no collision
--   -- only cc-oeh / sheikh-musa/oeh contain "oeh".
--
-- REVERT: DELETE FROM public.project_governance_families WHERE project = 'oeh';
--         DELETE FROM public.project_governance WHERE project = 'oeh';
--         DELETE FROM public.bot_channels WHERE channel_key = 'oeh';
-- (project_governance has a BEFORE DELETE forbid-delete trigger per migration
-- 063 -- a real revert of that one row needs an explicit one-time exception
-- from whoever owns that trigger, same as retiring any other project's row
-- would.)

-- ---------------------------------------------------------------------------
-- 1. bot_channels -- live group chat wired; enabled=false (bus #43833: 'oeh'
--    is pinned in the Mini's INGEST_CHANNELS, so enabled=true would also
--    hand the same bot token to the gzb hub's unscoped `WHERE enabled` poll
--    -- dual-poller 409, same class angullia's row already avoids)
-- ---------------------------------------------------------------------------
INSERT INTO public.bot_channels
  (channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,
   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset)
VALUES
  ('oeh', 'OEH_BOT_TOKEN', 'agent-session', 'oeh',
   '🎤 OEH (Sya): ', NULL,
   '{-5585966657}', '{}', '{"agent_phase": "supervised", "agent_reviewer": "cc-oeh"}'::jsonb,
   'oeh', 'substrate', false, NULL)
ON CONFLICT (channel_key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. project_governance -- Sya (she/her) getting the operator role (bus #43719);
--    channels wired to the live group chat id (bus #43775)
-- ---------------------------------------------------------------------------
INSERT INTO public.project_governance
  (project, cai_enabled, operators, channels, money_clearance_enabled, residency_ack, updated_by, reason)
VALUES
  ('oeh', false, '[]'::jsonb, '["-5585966657"]'::jsonb, false, NULL, 'cc-substrate',
   'op#22521/op#22647/bus#43775: oeh onboarding, Sya (head of OEH) getting '
   'the operator role. operators seeded empty -- her Telegram user id arrives when she '
   'first posts (normalize_operators() requires a real id, same pattern as angullia); '
   'orch-console adds it once she posts. channels=["-5585966657"], the live OEH Telegram '
   'group ("OEH Group Bot", bus #43775). residency_ack left NULL: '
   'website content only, no personal data today (Musa will record the ack if/when '
   'that changes).')
ON CONFLICT (project) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. project_governance_families -- priority=10 (no broader superset family to
--    out-rank, same reasoning as angullia; bus #43724)
-- ---------------------------------------------------------------------------
INSERT INTO public.project_governance_families (match_type, pattern, project, priority)
VALUES
  ('agent_prefix', '^cc-oeh', 'oeh', 10),
  ('repo_pattern', '%/oeh%',  'oeh', 10)
ON CONFLICT (match_type, pattern) DO NOTHING;
