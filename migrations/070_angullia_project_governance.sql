-- 070_angullia_project_governance.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Onboards a new governed project 'angullia' (Musa op#21154 + op#22433; backlog#72,
-- checkpoint #42; bus thread 26555a3f-3649-4b4f-adcf-aa041989fb8e, #43320).
-- Angullia is a Singapore business (brochure + booking site) with no personal data
-- in scope yet -- the site holds none unless/until a booking form is added. Pure
-- data: every table this migration writes to (project_governance,
-- project_governance_families, bot_channels, agents, fleet_lanes) already exists
-- on the live substrate -- nothing here touches privileges, so no `-- assert:`
-- lines are needed (house convention per migrations 065/069 headers).
--
-- SHAPE (per #43320, mirroring cosem-tdu's shape (069/PR #156) with the
-- simplifications #43320 itself calls out):
--   1. project_governance('angullia'): cai_enabled=false, money_clearance_
--      enabled=false, operators=[] (Rhaihan's Telegram user id is pending --
--      same "empty array, not a placeholder" pattern as 069, for the same
--      reason: normalize_operators() in nervous_system/console/governance.py
--      requires a real Telegram user id per operator, and a placeholder would
--      violate the exact invariant that check exists to enforce), reason
--      records "Rhaihan pending id" verbatim per #43320. residency_ack is left
--      NULL -- #43320 says this explicitly ("residency_ack for now... I'll
--      record the ack when the data shape is known"), so unlike 069 there is
--      no step-1b UPDATE here: NULL is the deliberate, on-record state, not an
--      oversight.
--   2. project_governance_families: '^cc-angullia' and '%angullia%' -> project
--      'angullia', priority=10 (the fleet's ordinary default for a standalone
--      project with no broader superset family to out-rank -- unlike
--      cosem-tdu's priority=5 carve against the existing 'cosem' family at 10,
--      there is no pre-existing 'angullia'-adjacent family here).
--   3. bot_channels('angullia'): modeled on the live 'cosem-tdu' row (same
--      single-operator-support shape). enabled=false, allowed_chat_ids='{}'
--      (deny-by-default) -- the bot exists (ANGULLIA_BOT_TOKEN already in Mini
--      .env + vault 'angullia_bot_token', getMe OK) but no chat/group is wired
--      yet. OPEN QUESTION flagged back to orch-console (not resolvable here):
--      the bot's privacy mode is ON (can_read_all_group_messages=false), so in
--      a group it only sees commands/mentions/replies -- confirm whether
--      Rhaihan will @mention it, or whether privacy mode should be turned off
--      via BotFather instead.
--   4. agents + fleet_lanes: ONE combined coord+builder lane 'cc-angullia' (per
--      #43320: "a single-site project doesn't need the split" -- unlike
--      cosem-tdu's separate -coord/builder lanes). repo_scope=['angullia']
--      (the new sheikh-musa/angullia repo) since this lane both governs AND
--      builds -- there is no separate builder agent row to avoid colliding
--      with (contrast cosem-tdu-coord's empty repo_scope, which exists
--      specifically to not collide with the pre-existing cc-cosem-tdu builder
--      row). fleet_lanes.desired_state='up' -- #43320 explicitly says to boot
--      cc-angullia immediately and have it start the first clone pass without
--      waiting for the group, so (unlike cosem-tdu-coord's desired_state=
--      'down') this migration seeds it already wanting to run.
--
-- TOKEN POOL: 'angullia' is a single-word family, so scripts/lib/
-- lane_token_resolver.py's family_of() already resolves 'cc-angullia' ->
-- 'angullia' via its existing strip-cc-then-split-on-first-hyphen path with NO
-- code change (confirmed by reading family_of() directly -- no hyphen in
-- 'angullia' means the naive split already returns the whole string; the
-- _COMPOUND_FAMILIES carve-out cosem-tdu needed does not apply here). Routed
-- to the SYED pool via a plain .group_default_token.angullia pointer file
-- (not a code change) -- see the companion resolver test in this PR.
--
-- REVERT: DELETE FROM public.fleet_lanes WHERE lane = 'angullia';
--         DELETE FROM public.agents WHERE id = 'cc-angullia';
--         DELETE FROM public.bot_channels WHERE channel_key = 'angullia';
--         DELETE FROM public.project_governance_families
--           WHERE project = 'angullia';
--         DELETE FROM public.project_governance WHERE project = 'angullia';
-- (project_governance has a BEFORE DELETE forbid-delete trigger per migration
-- 063 -- a real revert of that one row needs an explicit one-time exception
-- from whoever owns that trigger, same as retiring any other project's row
-- would.)

-- ---------------------------------------------------------------------------
-- 1. project_governance
-- ---------------------------------------------------------------------------
INSERT INTO public.project_governance
  (project, cai_enabled, operators, channels, money_clearance_enabled, residency_ack, updated_by, reason)
VALUES
  ('angullia', false, '[]'::jsonb, '[]'::jsonb, false, NULL, 'cc-substrate',
   'op#21154/op#22433 (bus 26555a3f-3649-4b4f-adcf-aa041989fb8e, #43320): '
   'angullia onboarding. Rhaihan pending id -- operators seeded empty rather '
   'than with a placeholder value (normalize_operators() requires a real '
   'Telegram user id). residency_ack left NULL deliberately per #43320 -- '
   'Musa will record the ack once the data shape (e.g. a future booking form) '
   'is known; the site holds no personal data today.')
ON CONFLICT (project) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. project_governance_families -- priority=10 (no broader superset family to
--    out-rank, unlike cosem-tdu vs cosem)
-- ---------------------------------------------------------------------------
INSERT INTO public.project_governance_families (match_type, pattern, project, priority)
VALUES
  ('agent_prefix', '^cc-angullia', 'angullia', 10),
  ('repo_pattern', '%angullia%',   'angullia', 10)
ON CONFLICT (match_type, pattern) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. bot_channels -- disabled, deny-by-default, modeled on the live 'cosem-tdu' row
-- ---------------------------------------------------------------------------
INSERT INTO public.bot_channels
  (channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,
   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset)
VALUES
  ('angullia', 'ANGULLIA_BOT_TOKEN', 'agent-session', 'angullia',
   '🏝️ Angullia (Rhaihan): ', NULL,
   '{}', '{}', '{"agent_phase": "supervised", "agent_reviewer": "cc-angullia"}'::jsonb,
   'angullia', 'substrate', false, NULL)
ON CONFLICT (channel_key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 4. cc-angullia -- ONE combined coord+builder lane, booted immediately
--    (desired_state='up'), repo_scope=['angullia'] (the new site repo)
-- ---------------------------------------------------------------------------
INSERT INTO public.agents (id, display_name, repo_scope, status)
VALUES
  ('cc-angullia', 'cc-angullia — combined coord+builder for the angullia site (Rhaihan, TDU operator)', '{angullia}', 'idle')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.fleet_lanes (lane, worktree_path, branch, model, launcher, base_agent_id, desired_state, notes)
VALUES
  ('angullia',
   '/Users/sheikhmusa/wingmen/projects/angullia',
   'main',
   'claude-opus-4-8',
   'launch_dangerous_cc.sh',
   'cc-angullia',
   'up',
   'op#21154/op#22433: single combined coord+builder lane for the new '
   'sheikh-musa/angullia repo (a Singapore brochure+booking site) -- a '
   'single-site project does not need cosem-tdu''s separate coord/builder '
   'split (#43320). First task: reproduce angullia.com as a static-first '
   'Next.js site, preview-deploy only to the Vercel ''wingmen'' team (no '
   'domain changes), report the preview URL to orch-console. Runs on the '
   'SYED token pool via a plain .group_default_token.angullia pointer file '
   '-- ''angullia'' is a single-word family so '
   'scripts/lib/lane_token_resolver.py''s family_of() already resolves it '
   'correctly with no _COMPOUND_FAMILIES entry needed (unlike cosem-tdu).')
ON CONFLICT (lane) DO NOTHING;
