-- 069_cosem_tdu_project_governance.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Onboards a new governed project 'cosem-tdu' (op#22426; negotiated in full across
-- bus thread cce66db2-0b74-47fa-8938-53e4c5d66cf9, #43308 -> #43310 -> #43311,
-- orch-console final go-ahead "Build the one PR"). Pure data: every table this
-- migration writes to (project_governance, project_governance_families,
-- bot_channels, agents, fleet_lanes) already exists on the live substrate --
-- nothing here touches privileges, so no `-- assert:` lines are needed (house
-- convention per migrations 065/068 headers).
--
-- NOTE on numbering / a pre-existing trunk gap this migration did NOT introduce:
-- this branch (fable/substrate-safe-fixes) is missing migrations 063
-- (063_project_governance.sql) and 064 (064_project_governance_cai_gate.sql) --
-- both exist on `main` and were applied to the LIVE substrate DB from there
-- (visible today as updated_by='migration-063-seed' on the live irsyad/cosem/
-- substrate rows), but their files never landed on this trunk. This migration's
-- INSERTs depend on tables 063/064 create, which is fine against the actual live
-- DB (already has them) and fine for this repo's own ephemeral wet-prove test
-- (which builds a minimal fixture mirroring the live schema, same pattern as
-- migrations/060's test) -- but a from-scratch replay of ONLY this trunk's
-- migrations/ directory in order would break at this file. Flagging for
-- orch-console: 063/064 likely want backporting onto fable/substrate-safe-fixes
-- at some point so the directory is fully self-replayable again. Not blocking
-- this PR -- pre-existing, not caused by it.
--
-- SHAPE (final, per #43311, which supersedes #43308's unstated priority/chat_id
-- assumptions):
--   1. project_governance('cosem-tdu'): cai_enabled=false, operators=[] (see
--      note below -- NOT a placeholder chat_id), channels=[], money_clearance_
--      enabled=false, residency_ack left NULL (Musa/Fazlie's on-record decision
--      per op#20706 -- not this migration's to fill), reason cites op#22426.
--   2. project_governance_families: 'cosem-tdu' rows at priority=5 (lower than
--      the existing 'cosem' family's priority=10 -- '^cc-cosem-tdu' and
--      '%cosem-tdu%' must resolve to 'cosem-tdu' before the broader 'cosem'
--      family gets a chance, same precedence trick migration 064 already uses
--      for irsyad-vs-ihsanos). '%tdu-tools%' added too (#43308 point 2) as a
--      plausible alias for the underlying Firebase project name.
--   3. bot_channels('cosem-tdu'): modeled on the real 'cosem-exams' row (same
--      product family, live production shape) rather than a literal 'irsyad'
--      key, which does not exist in bot_channels today (irsyad's only channel_
--      key is 'gazzabyte-irsyad', a vendor-support channel, not a template fit
--      here). enabled=false, allowed_chat_ids='{}' (deny-by-default) -- Musa
--      has not created the bot yet.
--   4. agents + fleet_lanes: new cc-cosem-tdu-coord lane, repo_scope='{}' (empty,
--      exactly like cc-irsyad-coord) so it can never collide with the EXISTING
--      cc-cosem-tdu agent row (repo_scope=['cosem-tdu']) in
--      scripts/lib/auto_agent_id.py's load_family_map() collision check.
--      fleet_lanes.desired_state='down' -- not booted by this migration.
--
-- DEVIATION FLAGGED (reasoned, not silent): #43308's literal wording implied a
-- placeholder chat_id for Fazlie ("chat_id: TBD"). The live project_governance
-- shape (migration 063) requires operators[].chat_id to be a real Telegram USER
-- id -- nervous_system/console/governance.py's normalize_operators() enforces
-- this on every console-side write, and a placeholder would be exactly the kind
-- of value that invariant exists to keep out. Seeding operators=[] instead (an
-- empty, valid array -- the console already renders this as "no operators on
-- file", i.e. pending) and recording "Fazlie (TDU operator, Telegram user id
-- pending)" in `reason` achieves the same "pending" state #43311 asked for
-- without writing a value the column's own contract forbids.
--
-- REVERT: DELETE FROM public.fleet_lanes WHERE lane = 'cosem-tdu-coord';
--         DELETE FROM public.agents WHERE id = 'cc-cosem-tdu-coord';
--         DELETE FROM public.bot_channels WHERE channel_key = 'cosem-tdu';
--         DELETE FROM public.project_governance_families
--           WHERE project = 'cosem-tdu';
--         DELETE FROM public.project_governance WHERE project = 'cosem-tdu';
-- (project_governance has a BEFORE DELETE forbid-delete trigger per migration
-- 063 -- a real revert of that one row needs an explicit one-time exception from
-- whoever owns that trigger, same as retiring any other project's row would.)

-- ---------------------------------------------------------------------------
-- 1. project_governance
-- ---------------------------------------------------------------------------
INSERT INTO public.project_governance
  (project, cai_enabled, operators, channels, money_clearance_enabled, residency_ack, updated_by, reason)
VALUES
  ('cosem-tdu', false, '[]'::jsonb, '[]'::jsonb, false, NULL, 'cc-substrate',
   'op#22426: cosem-tdu onboarding (bus cce66db2-0b74-47fa-8938-53e4c5d66cf9, '
   '#43308/#43310/#43311). Fazlie is TDU''s external operator (Telegram user id '
   'pending -- Musa has not created the bot yet); operators/channels seeded '
   'empty rather than with a placeholder value. residency_ack intentionally '
   'left NULL (op#20706: an explicit on-record acknowledgement, never a silent '
   'default, is Musa/Fazlie''s call to make, not this migration''s).')
ON CONFLICT (project) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. project_governance_families -- priority=5, checked before the 'cosem' family
-- ---------------------------------------------------------------------------
INSERT INTO public.project_governance_families (match_type, pattern, project, priority)
VALUES
  ('agent_prefix', '^cc-cosem-tdu', 'cosem-tdu', 5),
  ('repo_pattern', '%cosem-tdu%',   'cosem-tdu', 5),
  ('repo_pattern', '%tdu-tools%',   'cosem-tdu', 5)
ON CONFLICT (match_type, pattern) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. bot_channels -- disabled, deny-by-default, modeled on the live 'cosem-exams' row
-- ---------------------------------------------------------------------------
INSERT INTO public.bot_channels
  (channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,
   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset)
VALUES
  ('cosem-tdu', 'COSEM_TDU_BOT_TOKEN', 'agent-session', 'cosem-tdu-coord',
   '🛠️ COSEM TDU (Fazlie): ', NULL,
   '{}', '{}', '{"agent_phase": "supervised", "agent_reviewer": "cc-cosem-tdu-coord"}'::jsonb,
   'cosem-tdu', 'substrate', false, NULL)
ON CONFLICT (channel_key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 4. cc-cosem-tdu-coord -- new coord lane, repo_scope=[] (never collides with the
--    existing cc-cosem-tdu builder agent, repo_scope=['cosem-tdu'])
-- ---------------------------------------------------------------------------
INSERT INTO public.agents (id, display_name, repo_scope, status)
VALUES
  ('cc-cosem-tdu-coord', 'cc-cosem-tdu-coord — cosem-tdu coordinator for Fazlie (TDU operator), distinct base', '{}', 'idle')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.fleet_lanes (lane, worktree_path, branch, model, launcher, base_agent_id, desired_state, notes)
VALUES
  ('cosem-tdu-coord',
   '/Users/sheikhmusa/wingmen/projects/cosem-tdu.wt-coord',
   'lane/cosem-tdu-coord',
   'claude-opus-4-8',
   'launch_dangerous_cc.sh',
   'cc-cosem-tdu-coord',
   'down',
   'op#22426: coord for Fazlie (TDU''s external operator) -- product/scoping calls '
   'are Fazlie''s; floor/residency/money gates route to orch-console. Builds are '
   'dispatched to the EXISTING cc-cosem-tdu lane (attendance/geofences/wages, '
   'desired_state=down, already live) -- per #43311 that becomes this coord''s '
   'BUILDER lane, one track, plus more autoscaler builders if parallel work '
   'appears. Boots with an explicit CC_BASE_OVERRIDE=cc-cosem-tdu-coord (never '
   'pwd auto-resolution), matching cc-irsyad-coord''s own pattern. Not irsyad --'
   ' runs on whichever token this lane''s family (''cosem'') pointer resolves to '
   '(scripts/lib/lane_token_resolver.py, file-based, family-keyed by session '
   'name prefix) -- no override invented by this migration, and per fleet '
   'convention (every irsyad lane on the musa2 key; every non-irsyad lane, this '
   'one included, stays off it) this was never going to be an irsyad-key lane.')
ON CONFLICT (lane) DO NOTHING;
