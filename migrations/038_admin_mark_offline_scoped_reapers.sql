-- 038_admin_mark_offline_scoped_reapers.sql
--
-- WHY (CAI-RESP-762, 2026-08-07): 037 gave the SRE a lease-gated reaper, but
-- sweep_stale_agents() (the rejected-A forgery pattern) still has TWO live
-- callers that are NOT the lease holder and so cannot use admin_mark_offline:
--   * scripts/lib/auto_agent_id.py::reap_stale_family — runs at EVERY lane launch,
--     actor 'launcher:<base>', reaps ONLY stale ghosts of its OWN base family (8h);
--   * nervous_system/lane_watchdog.py — actor 'lane_watchdog', fleet-wide stale (8h).
-- A plain DROP of sweep_stale_agents would break launch + watchdog reaping. cai
-- ruled: broaden admin_mark_offline's INTERNAL authorization to a SCOPED
-- disjunction (NOT a broad bypass — that would resurrect the rejected B), cut
-- both callers over, THEN drop sweep_stale_agents (a later migration, 039).
--
-- This migration:
--   1. protected_agents — a data-driven protected-singleton set. The primitive
--      REFUSES to offline any protected agent for EVERY branch, so the hub
--      mis-reap class (guard living in one of three callers) can never recur:
--      ONE enforcement point in the primitive.
--   2. reaper_actors — the sanctioned non-lease actors + their stale bounds
--      (allowlist + bound in a table, NOT hardcoded).
--   3. admin_mark_offline extended to a 3-branch auth disjunction. Every branch
--      stays offline-only + per-row + mandatory-reason + atomic audit recording
--      the caller's TRUE identity + which branch authorized. The caller ALWAYS
--      declares its OWN id in app.current_agent_id (never the A-spoof). The trigger
--      exemption (current_user=fleet_reaper) is UNCHANGED — only the fn's
--      front-door authorization broadens, so all CAI-761 trigger guarantees hold.
--
-- HONEST TRADEOFF (cai stated + accepted, CAI-762): the launcher/watchdog branches
-- authorize on a GUC -> forgeable within the app-tier. That matches the existing
-- cooperative-identity model and is strictly better than the A-spoof; blast radius
-- is bounded (launcher self-scoped to its own base; fleet-wide stays lease-gated;
-- protected singletons refused for ALL callers). Defending a malicious app-tier is
-- out of scope (it can DROP the trigger).
--
-- SCOPE: bus/coordination silo tscuymavysscrvoberrr. Apply via cai §6.6 guarded
-- path (never db push). See APPLY NOTES / positive controls at the foot.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. protected_agents — singletons that must NEVER be heartbeat-reaped. Their
--    liveness is lease/reclaim-tracked (the hub self-registers but runs no
--    continuous heartbeat, so a stale last_heartbeat is NOT death-evidence).
--    Centralised so admin_mark_offline enforces it for EVERY caller (CAI-762).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.protected_agents (
  agent_id text PRIMARY KEY,
  reason   text NOT NULL,
  added_by text NOT NULL,
  added_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.protected_agents IS
  'CAI-762: agents whose liveness is lease/reclaim-tracked (not heartbeat-tracked). '
  'admin_mark_offline REFUSES to offline any of these, for every reaper branch. '
  'The single enforcement point that retro-hardens the hub-mis-reap class.';

ALTER TABLE public.protected_agents ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.protected_agents TO fleet_reaper, console_readonly, service_role;
DROP POLICY IF EXISTS protected_agents_ro ON public.protected_agents;
CREATE POLICY protected_agents_ro ON public.protected_agents
  FOR SELECT TO fleet_reaper, console_readonly, service_role USING (true);

INSERT INTO public.protected_agents (agent_id, reason, added_by) VALUES
  ('cc-orchestrator','singleton hub — liveness is orch_lease-tracked; self-registers without a continuous heartbeat','cc-fleet-health'),
  ('cai',            'singleton governor — lease/reclaim-tracked','cc-fleet-health'),
  ('cc-fleet-health','singleton SRE (self) — fleet_health_lease-tracked','cc-fleet-health'),
  ('orch-console',   'singleton console — persistent','cc-fleet-health'),
  ('nazim-console',  'singleton console — persistent','cc-fleet-health')
ON CONFLICT (agent_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. reaper_actors — sanctioned non-lease reaper actors + their stale bounds.
--    Allowlist + bound in data (not hardcoded), so a new sanctioned reaper is
--    one INSERT and a stale set cannot silently drift.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.reaper_actors (
  actor       text PRIMARY KEY,
  branch      text NOT NULL CHECK (branch IN ('launcher','watchdog')),
  stale_bound interval NOT NULL,
  note        text
);
COMMENT ON TABLE public.reaper_actors IS
  'CAI-762: sanctioned non-lease reaper actors. branch=launcher is the pattern '
  'row (actor=launcher:*) authorizing a launcher:<base> caller to reap ONLY its '
  'own base family past stale_bound; branch=watchdog is an explicit per-actor '
  'allowlist authorizing fleet-wide stale reaps past stale_bound.';

ALTER TABLE public.reaper_actors ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.reaper_actors TO fleet_reaper, console_readonly, service_role;
DROP POLICY IF EXISTS reaper_actors_ro ON public.reaper_actors;
CREATE POLICY reaper_actors_ro ON public.reaper_actors
  FOR SELECT TO fleet_reaper, console_readonly, service_role USING (true);

INSERT INTO public.reaper_actors (actor, branch, stale_bound, note) VALUES
  ('launcher:*',   'launcher','8 hours','auto_agent_id.reap_stale_family — self-scoped to its OWN base family at launch'),
  ('lane_watchdog','watchdog','8 hours','nervous_system/lane_watchdog.py ghost-reap — fleet-wide stale')
ON CONFLICT (actor) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. admin_offline_audit gains auth_branch — records WHICH branch authorized
--    each reap (truthful attribution; additive, nullable so 037-era rows are ok).
-- ---------------------------------------------------------------------------
ALTER TABLE public.admin_offline_audit ADD COLUMN IF NOT EXISTS auth_branch text;

-- ---------------------------------------------------------------------------
-- 4. Extend admin_mark_offline: scoped 3-branch auth + centralised protected
--    refusal. Owner stays fleet_reaper. CREATE OR REPLACE requires being the
--    OWNER (membership alone is insufficient), so we transiently SET ROLE
--    fleet_reaper (postgres granted membership + CREATE-on-schema for the
--    duration only, both revoked after — same minimal-footprint dance as 037).
-- ---------------------------------------------------------------------------
GRANT fleet_reaper TO postgres;                 -- transient: so postgres can SET ROLE fleet_reaper
GRANT CREATE ON SCHEMA public TO fleet_reaper;  -- transient: the replacer needs CREATE on schema
SET ROLE fleet_reaper;                           -- replace as the OWNER

CREATE OR REPLACE FUNCTION public.admin_mark_offline(p_agent_id text, p_reason text)
 RETURNS boolean                                    -- true = newly marked offline; false = already offline (no-op)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
DECLARE
  v_caller text;
  v_prior  text;
  v_base   text;                                     -- target's base_agent_id
  v_hb     timestamptz;                              -- target's last_heartbeat
  v_bound  interval;
  v_branch text := NULL;                             -- which branch authorized (NULL = none yet)
BEGIN
  -- C4: exactly one target/call; C3: reason mandatory.
  IF p_agent_id IS NULL OR p_agent_id = '' THEN
    RAISE EXCEPTION 'admin_mark_offline: p_agent_id is required';
  END IF;
  IF p_reason IS NULL OR p_reason = '' THEN
    RAISE EXCEPTION 'admin_mark_offline: p_reason is required (reaps must not be anonymous)';
  END IF;

  -- Caller ALWAYS declares its OWN identity (never the target's — no A-spoof).
  v_caller := current_setting('app.current_agent_id', true);
  IF v_caller IS NULL OR v_caller = '' THEN
    RAISE EXCEPTION 'admin_mark_offline: caller identity (app.current_agent_id) not set';
  END IF;

  -- Read + lock the target; get base + heartbeat for the scoped branches.
  SELECT status, base_agent_id, last_heartbeat
    INTO v_prior, v_base, v_hb
    FROM agent_status WHERE agent_id = p_agent_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'admin_mark_offline: no agent_status row for % (nothing to reap)', p_agent_id;
  END IF;

  -- CENTRALISED SINGLETON PROTECTION (CAI-762): refuse a protected agent for
  -- EVERY caller/branch. This is the one enforcement point that kills the
  -- hub-mis-reap class regardless of which reaper calls in.
  IF EXISTS (SELECT 1 FROM protected_agents WHERE agent_id = p_agent_id) THEN
    RAISE EXCEPTION 'admin_mark_offline: % is a protected singleton (lease/reclaim-tracked) — never heartbeat-reaped', p_agent_id
      USING ERRCODE = '42501';
  END IF;

  -- AUTH DISJUNCTION. Each branch is offline-only + per-row + audited.
  -- branch LEASE: the active fleet_health lease-holder may reap ANY stale lane
  -- (strongest; the dead-man's switch — if the lease lapses this branch fails).
  IF EXISTS (
    SELECT 1 FROM fleet_health_lease
     WHERE lease_key = 'fleet-health' AND holder = v_caller
       AND renewed_at > now() - make_interval(secs => ttl_seconds)
  ) THEN
    v_branch := 'lease';

  -- branch LAUNCHER: 'launcher:<base>' may reap ONLY stale ghosts of its OWN
  -- base family (target.base_agent_id = <base>) past the launcher bound.
  ELSIF v_caller LIKE 'launcher:%' THEN
    SELECT stale_bound INTO v_bound FROM reaper_actors WHERE actor = 'launcher:*' AND branch = 'launcher';
    IF v_bound IS NOT NULL
       AND substring(v_caller from 10) <> ''
       AND v_base = substring(v_caller from 10)          -- target belongs to caller's OWN base
       AND v_hb IS NOT NULL AND v_hb < now() - v_bound   -- and is stale past the bound
    THEN
      v_branch := 'launcher';
    END IF;

  -- branch WATCHDOG: an allowlisted actor may reap fleet-wide, but only past bound.
  ELSE
    SELECT stale_bound INTO v_bound FROM reaper_actors WHERE actor = v_caller AND branch = 'watchdog';
    IF v_bound IS NOT NULL AND v_hb IS NOT NULL AND v_hb < now() - v_bound THEN
      v_branch := 'watchdog';
    END IF;
  END IF;

  IF v_branch IS NULL THEN
    RAISE EXCEPTION 'admin_mark_offline: caller % not authorized to reap % (not lease-holder / not own-base launcher / not allowlisted watchdog / not stale past bound)', v_caller, p_agent_id
      USING ERRCODE = '42501';
  END IF;

  -- C1: one-directional. Already offline -> idempotent no-op (never un-offline).
  IF v_prior = 'offline' THEN
    RETURN false;
  END IF;

  -- C3: atomic audit FIRST, same txn — true actor + branch (rolls back with the write).
  INSERT INTO admin_offline_audit (caller, target_agent_id, prior_status, reason, auth_branch)
  VALUES (v_caller, p_agent_id, v_prior, p_reason, v_branch);

  -- C5: marker set-and-reset around the single offline write.
  PERFORM set_config('app.admin_offline_active', p_agent_id, true);
  UPDATE agent_status SET status = 'offline' WHERE agent_id = p_agent_id;  -- C1: status only
  PERFORM set_config('app.admin_offline_active', '', true);

  RETURN true;
END;
$function$;

COMMENT ON FUNCTION public.admin_mark_offline(text, text) IS
  'CAI-761/762: narrow offline-only, per-row, audited reaper. Refuses protected '
  'singletons for every caller. Scoped auth disjunction: LEASE (fleet_health '
  'holder -> any stale) / LAUNCHER (launcher:<base> -> own base family, stale>bound) '
  '/ WATCHDOG (allowlisted actor -> fleet-wide stale>bound). Caller declares its '
  'OWN id; audit records true actor + branch. Trigger exemption (current_user='
  'fleet_reaper) unchanged.';

RESET ROLE;                                     -- back to postgres
REVOKE CREATE ON SCHEMA public FROM fleet_reaper;
REVOKE fleet_reaper FROM postgres;

COMMIT;

-- ===========================================================================
-- APPLY NOTES for cai (§6.6) — CAI-762 positive controls
-- ===========================================================================
-- Verified by cc-fleet-health via rolled-back psql before handoff (see the
-- accompanying bus message for the full run). Positive controls:
--   NEW (CAI-762):
--   1. LAUNCHER cannot reap another base: caller 'launcher:baseA' reaping a
--      target whose base_agent_id='baseB' -> DENIED.
--   2. LAUNCHER can reap its OWN base's stale ghost past bound -> allowed.
--   3. NON-allowlisted actor (not lease, not launcher:*, not a watchdog row) -> DENIED.
--   4. WATCHDOG allowlisted actor reaps a fleet-wide stale lane past bound -> allowed.
--   5. PROTECTED singleton REFUSED for EVERY branch (lease/launcher/watchdog).
--   6. NOT-stale-enough target (heartbeat within bound) via launcher/watchdog -> DENIED.
--   7. Audit records the TRUE actor + auth_branch (no A-spoof; attribution honest).
--   CARRIED (CAI-761, must still hold):
--   - offline-only, no other column mutates; no un-offline (idempotent no-op);
--   - atomic audit per call; base identity guard still blocks agent-writes-another-row;
--   - forged marker from a non-fleet_reaper role does NOT bypass (trigger unchanged).
--
-- OWNERSHIP: CREATE OR REPLACE preserves owner=fleet_reaper; postgres is granted
-- fleet_reaper membership transiently only to replace the fn, then revoked (so the
-- exemption stays unforgeable — same residual as 037: a CREATEROLE admin can
-- re-grant itself, accepted for the trusted app-tier).
--
-- SEQUENCE AFTER APPLY: cut reap_stale_family + lane_watchdog over to
-- admin_mark_offline (keep the launcher's best-effort/non-fatal wrapper — a reap
-- failure must NEVER block a launch); THEN 039 = plain DROP sweep_stale_agents,
-- re-verifying zero callers at that point.
-- LEDGER: repo=orchestrator, migration_name=038_admin_mark_offline_scoped_reapers.sql,
-- silo_ref=tscuymavysscrvoberrr.
-- ===========================================================================
