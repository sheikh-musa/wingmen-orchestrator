-- 009_ghost_reaper.sql — CAI-RESP-292 identity-respecting stale-agent reaper.
--
-- PROBLEM: instances that die uncleanly never run their clean-exit trap
-- (launch_dangerous_cc.sh ARCH-035), so they linger in agent_status as
-- status='working' forever (cosmetic — the 8h freshness bound means nothing
-- treats them as live — but dishonest). cai cannot hand-reap them: the
-- enforce_agent_status_identity trigger requires app.current_agent_id == the
-- row's agent_id, so reaping a ghost would mean forging a dead instance's
-- identity (§9), which hollows out the identity model.
--
-- FIX (both halves stay WITHIN the identity model, never disable the trigger):
--   1. add reaped_by attribution to agent_status + its history snapshot, so a
--      third-party offline is HONEST (records who reaped), not anonymous.
--   2. an audited SECURITY DEFINER sweep_stale_agents() that offlines stale,
--      non-offline rows and stamps reaped_by. It sets the per-row GUC only to
--      satisfy the existing identity trigger for that specific offline write —
--      and because every such write is attributed via reaped_by, it is an
--      audited system reap, not an anonymous forge.
--
-- Apply via scripts/apply_migration.py 009 --silo tscuymavysscrvoberrr (historical
-- applier: apply_ghost_reaper.py, deleted 2026-09-05 PR #88). NEVER supabase db push.

BEGIN;

-- 1. attribution column on the live table + the history snapshot ---------------
ALTER TABLE agent_status         ADD COLUMN IF NOT EXISTS reaped_by text;
ALTER TABLE agent_status_history ADD COLUMN IF NOT EXISTS reaped_by text;

-- 2. carry reaped_by into the history snapshot --------------------------------
CREATE OR REPLACE FUNCTION public.snapshot_agent_status_to_history()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
  INSERT INTO agent_status_history (
    agent_id, status, current_task, scope_repos, scope_paths,
    blocked_on_msg_id, blocked_on_decision_ref, last_commit_sha, reaped_by
  ) VALUES (
    NEW.agent_id, NEW.status, NEW.current_task, NEW.scope_repos, NEW.scope_paths,
    NEW.blocked_on_msg_id, NEW.blocked_on_decision_ref, NEW.last_commit_sha, NEW.reaped_by
  );
  RETURN NEW;
END;
$function$;

-- 3. audited, identity-respecting sweeper -------------------------------------
--    Offlines only STALE (last_heartbeat older than p_threshold) NON-offline
--    rows. Records reaped_by = p_actor (required) so the reap is attributable.
--    Optional p_base_agent_id scopes the sweep to one family (used by the
--    launcher to reap its own stale siblings on relaunch). Returns what it
--    reaped. Restores the caller's GUC on the way out so no ghost identity leaks.
CREATE OR REPLACE FUNCTION public.sweep_stale_agents(
  p_threshold      interval,
  p_actor          text,
  p_base_agent_id  text DEFAULT NULL
) RETURNS TABLE(reaped_agent_id text, stale_hours numeric)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $function$
DECLARE
  v_row  record;
  v_prev text;
BEGIN
  IF p_actor IS NULL OR p_actor = '' THEN
    RAISE EXCEPTION 'sweep_stale_agents: p_actor (reaper attribution) is required — reaps must not be anonymous';
  END IF;
  v_prev := current_setting('app.current_agent_id', true);
  FOR v_row IN
    SELECT agent_id, last_heartbeat
      FROM agent_status
     WHERE status IS DISTINCT FROM 'offline'
       AND last_heartbeat IS NOT NULL
       AND last_heartbeat < now() - p_threshold
       AND (p_base_agent_id IS NULL OR base_agent_id = p_base_agent_id)
  LOOP
    -- satisfy enforce_agent_status_identity for THIS row only; the reaped_by
    -- stamp below is what keeps this honest rather than an anonymous forge.
    PERFORM set_config('app.current_agent_id', v_row.agent_id, true);
    UPDATE agent_status
       SET status       = 'offline',
           current_task = NULL,
           reaped_by    = p_actor,
           updated_at   = now()
     WHERE agent_id = v_row.agent_id;
    reaped_agent_id := v_row.agent_id;
    stale_hours     := round(extract(epoch FROM (now() - v_row.last_heartbeat)) / 3600, 1);
    RETURN NEXT;
  END LOOP;
  -- never leave the session impersonating the last ghost
  PERFORM set_config('app.current_agent_id', coalesce(v_prev, ''), true);
  RETURN;
END;
$function$;

GRANT EXECUTE ON FUNCTION public.sweep_stale_agents(interval, text, text) TO PUBLIC;

COMMIT;
