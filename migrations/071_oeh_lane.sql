-- 071_oeh_lane.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Onboards a new fleet lane 'oeh' (Musa op#22521; backlog#73, checkpoint #46;
-- bus thread 2cf1d727-32de-46f3-abf4-b8c8c3eeb4b1, #43427). OEH is the
-- offshoreentertainment.events WordPress site being cloned to a new
-- static-first Next.js site on the new domain oehgroup.live (Musa is
-- purchasing it). Pure data: agents + fleet_lanes already exist on the live
-- substrate -- nothing here touches privileges, so no `-- assert:` lines are
-- needed (house convention per migrations 065/069/070 headers).
--
-- SHAPE -- deliberately SMALLER than 070 (angullia) per #43427's own
-- instruction: "NO project_governance/bot channel yet: Musa hasn't said
-- anyone other than him edits it, so I'll add that later if needed." This
-- migration therefore touches ONLY agents + fleet_lanes:
--   1. agents: ONE combined coord+builder lane 'cc-oeh' (same "a single-site
--      project doesn't need the split" shape as cc-angullia), repo_scope=
--      ['oeh'] (the new sheikh-musa/oeh repo).
--   2. fleet_lanes: desired_state='up' -- #43427 says to boot cc-oeh and hand
--      it its first task immediately, same as angullia.
--
-- TOKEN POOL: 'oeh' is a single-word family, so scripts/lib/
-- lane_token_resolver.py's family_of() already resolves 'cc-oeh' -> 'oeh' via
-- its existing strip-cc-then-split-on-first-hyphen path with NO code change
-- (confirmed by reading family_of() directly -- no hyphen in 'oeh' means the
-- naive split already returns the whole string; no _COMPOUND_FAMILIES entry
-- needed). Routed to the SYED pool via a plain .group_default_token.oeh
-- pointer file (not a code change) -- see the companion resolver test in
-- this PR.
--
-- REVERT: DELETE FROM public.fleet_lanes WHERE lane = 'oeh';
--         DELETE FROM public.agents WHERE id = 'cc-oeh';

-- ---------------------------------------------------------------------------
-- 1. cc-oeh -- ONE combined coord+builder lane, booted immediately
--    (desired_state='up'), repo_scope=['oeh'] (the new site repo)
-- ---------------------------------------------------------------------------
INSERT INTO public.agents (id, display_name, repo_scope, status)
VALUES
  ('cc-oeh', 'cc-oeh — combined coord+builder for the OEH site (offshoreentertainment.events -> oehgroup.live)', '{oeh}', 'idle')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.fleet_lanes (lane, worktree_path, branch, model, launcher, base_agent_id, desired_state, notes)
VALUES
  ('oeh',
   '/Users/sheikhmusa/wingmen/projects/oeh',
   'main',
   'claude-opus-4-8',
   'launch_dangerous_cc.sh',
   'cc-oeh',
   'up',
   'op#22521: single combined coord+builder lane for the new sheikh-musa/oeh '
   'repo -- clone the PUBLIC content of offshoreentertainment.events '
   '(WordPress) to a static-first Next.js site, preview-deploy only to the '
   'Vercel ''wingmen'' team (no domain changes -- oehgroup.live is Musa''s '
   'future domain, not configured yet), commit with the GitHub-noreply '
   'identity 97861619+sheikh-musa@users.noreply.github.com from the first '
   'commit, report the preview URL to orch-console. WordPress admin creds '
   '(vault ''oeh_wp_admin'') are READ-ONLY export use ONLY -- never modify '
   'the live WordPress site. Runs on the SYED token pool via a plain '
   '.group_default_token.oeh pointer file -- ''oeh'' is a single-word family '
   'so scripts/lib/lane_token_resolver.py''s family_of() already resolves it '
   'correctly with no _COMPOUND_FAMILIES entry needed. No project_governance '
   '/ bot_channels row yet (#43427: Musa hasn''t said anyone but him edits '
   'it) -- add later if needed.')
ON CONFLICT (lane) DO NOTHING;
