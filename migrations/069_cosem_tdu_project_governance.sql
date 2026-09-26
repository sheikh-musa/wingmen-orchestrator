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
-- SHAPE (final, per #43311/#43314, which supersedes #43308's unstated priority/
-- chat_id assumptions, and #43311's own since-superseded "residency_ack stays
-- NULL" instruction):
--   1. project_governance('cosem-tdu'): cai_enabled=false, operators=[] (see
--      note below -- NOT a placeholder chat_id), channels=[], money_clearance_
--      enabled=false, reason cites op#22426. residency_ack is then SET by a
--      separate UPDATE below (step 1b), attributed to orch-console specifically
--      -- not folded into this INSERT's updated_by/reason -- so the append-only
--      project_governance_audit trail carries two distinct, correctly-attributed
--      rows: cc-substrate's onboarding INSERT, then orch-console's residency
--      decision (Musa op#22429, bus #43314, superseding #43311's original "leave
--      it NULL" call once Musa clarified TDU has no ADCDA involvement and its
--      prod region is already confirmed in-country).
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
-- residency_ack is set via a follow-up UPDATE (step 1b), not folded into the
-- INSERT above, for two reasons: (a) correct audit attribution -- governance.py's
-- sanctioned console write path (nervous_system/console/governance.py) does NOT
-- even expose residency_ack in its column allowlist (FIELDS = cai_enabled,
-- money_clearance_enabled, operators, channels only) -- it is deliberately a
-- migration/registry-level decision, never a console toggle; (b) it lets
-- `updated_by`/`reason` name the RIGHT author for each fact: cc-substrate for
-- "this project now exists", orch-console for "here is the residency ruling",
-- rather than misattributing Musa's residency call to the lane that merely typed
-- the SQL. The AFTER INSERT OR UPDATE trigger (project_governance_audit_trigger,
-- migration 063) appends one audit row per statement regardless of which SQL
-- client issues it, so both authorships land correctly in project_governance_audit.
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
   'empty rather than with a placeholder value. residency_ack is set by a '
   'separate step-1b UPDATE below, attributed to orch-console (Musa op#22429).')
ON CONFLICT (project) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1b. residency_ack -- separate UPDATE, separately attributed (see header note
--     above for why this is not folded into the INSERT). Musa op#22429 (bus
--     #43314): TDU is a Singapore-local entity with NO ADCDA involvement;
--     its prod Firebase site (tdu-tools-prod) is confirmed asia-southeast1
--     (Singapore) -- in-country for a SG entity. Staging (tdu-tools-staging) is
--     UNVERIFIED (PERMISSION_DENIED checking its region) -- residency_ack
--     covers the confirmed prod finding only; staging remains an open item,
--     not silently folded into this acknowledgement.
--
--     residency_ack is jsonb (migration 063) with no established schema
--     anywhere in this codebase (every live row today is NULL; the console's
--     own field allowlist doesn't even expose it -- nervous_system/console/
--     governance.py surfaces it read-only as a residency_ack_on_file boolean,
--     never validates its shape). jsonb_build_object() is used rather than a
--     hand-quoted JSON string literal so the basis text's own punctuation can
--     never produce invalid JSON.
-- ---------------------------------------------------------------------------
UPDATE public.project_governance
SET residency_ack = jsonb_build_object(
      'basis', 'Musa op#22429: TDU is a Singapore local entity; data in SG '
               '(asia-southeast1) = in-country; staging region unverified.',
      'acked_via', 'orch-console (bus #43314)'
    ),
    updated_by = 'orch-console',
    reason = 'op#22429 (bus #43314): residency ruling on cosem-tdu -- Singapore-'
             'local entity, no ADCDA involvement (corrects an earlier draft of '
             'docs/data-store-registry.md); prod Firebase site confirmed '
             'asia-southeast1 in-country, staging region unverified pending '
             'further access.',
    updated_at = now()
WHERE project = 'cosem-tdu';

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
   'pwd auto-resolution), matching cc-irsyad-coord''s own pattern. Runs on the '
   'SYED token pool, NOT the broader cosem family''s Musa key (op#16101 keeps '
   'cosem-exams on Musa) and NOT irsyad''s musa2 key -- its own compound family '
   '''cosem-tdu'' in scripts/lib/lane_token_resolver.py (family_of()''s '
   '_COMPOUND_FAMILIES carve-out, PR #156) resolves via a dedicated '
   '.group_default_token.cosem-tdu pointer file at the Syed key, auto-covering '
   'this coord and any future cosem-tdu-worker-N autoscaler siblings with no '
   'further renaming.')
ON CONFLICT (lane) DO NOTHING;
