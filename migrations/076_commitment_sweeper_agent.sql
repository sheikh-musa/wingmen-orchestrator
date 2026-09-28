-- 076_commitment_sweeper_agent.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Registers 'commitment-sweeper' in public.agents (bus #44547, follow-up to #44527).
-- nervous_system/commitment_sweeper.py's _notify() hardcoded from_agent='orch-console'
-- on every INSERT INTO agent_messages, so every DUE/FIRED commitment row misattributed
-- itself as having come from orch-console (e.g. #44511 "COMMITMENT #55 FIRED..." arrived
-- as orch-console -> orch-console). Same misattribution class bus_send.py already fixed
-- for _bus_tmp (#43673). This migration registers the dedicated identity BEFORE the code
-- change lands, so the first row _notify() posts under it does not hit
-- agent_messages_from_agent_fkey the way ingest-watchdog's did (#44035) -- see also
-- ddl-coverage-watchdog (migration 074), which applied the same fix-before-first-page
-- lesson.
--
-- Pure data: agents already exists on the live substrate -- nothing here touches
-- privileges, so no `-- assert:` lines are needed (house convention per migrations
-- 065/069/070/071/073/074 headers).
--
-- SHAPE -- mirrors sla-watchdog/ingest-watchdog/ddl-coverage-watchdog's row exactly
-- (id/display_name/repo_scope/status only; current_task and last_heartbeat stay
-- NULL/default, since this identity only ever POSTS bus rows -- it is not a
-- polled/heartbeating lane).
--
-- REVERT: DELETE FROM public.agents WHERE id = 'commitment-sweeper';

INSERT INTO public.agents (id, display_name, repo_scope, status)
VALUES
  ('commitment-sweeper', 'Durable-commitment fire/escalate notifier (nervous_system/commitment_sweeper.py, bus #44547)', '{}', 'active')
ON CONFLICT (id) DO NOTHING;
