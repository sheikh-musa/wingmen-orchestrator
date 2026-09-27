-- 073_ingest_watchdog_agent.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Registers 'ingest-watchdog' in public.agents (Musa op#22669 item 3 fallout,
-- bus #44035). PR #182's new runtime pinned-channel-drift check
-- (nervous_system/ingest.py) pages the operator via a bus row with
-- from_agent='ingest-watchdog' on its first live cycle -- that row hit
-- agent_messages_from_agent_fkey and failed with ForeignKeyViolation, because
-- no migration had ever registered this identity the way 'sla-watchdog' (see
-- scripts/priority_sla_watchdog.py) already is. Same failure class as the
-- underlying incident this whole workstream exists to fix: a code path
-- assuming an identity exists on the bus without a migration ever having
-- created it.
--
-- Pure data: agents already exists on the live substrate -- nothing here
-- touches privileges, so no `-- assert:` lines are needed (house convention
-- per migrations 065/069/070/071 headers).
--
-- SHAPE -- mirrors sla-watchdog's row exactly (id/display_name/repo_scope/
-- status only; current_task and last_heartbeat stay NULL/default, same as
-- sla-watchdog, since this identity only ever POSTS bus rows -- it is not a
-- polled/heartbeating lane).
--
-- REVERT: DELETE FROM public.agents WHERE id = 'ingest-watchdog';

INSERT INTO public.agents (id, display_name, repo_scope, status)
VALUES
  ('ingest-watchdog', 'Ingest pinned-channel-drift runtime watchdog (nervous_system/ingest.py, op#22669 item 3)', '{}', 'active')
ON CONFLICT (id) DO NOTHING;
