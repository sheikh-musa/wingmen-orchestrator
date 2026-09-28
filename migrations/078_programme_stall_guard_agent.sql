-- 078_programme_stall_guard_agent.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Registers 'programme-stall-guard' in public.agents (bus #44697, same class as bus #44547
-- / migration 076). scripts/programme_stall_guard.py's check() hardcoded
-- from_agent='orch-console' on every STALLED-BY-CONSTRUCTION INSERT INTO agent_messages, so
-- every escalation misattributed itself as having come from orch-console (e.g. #44695
-- arrived orch-console -> orch-console). This migration registers the dedicated identity
-- BEFORE the code change lands, so the first row check() posts under it does not hit
-- agent_messages_from_agent_fkey the way ingest-watchdog's did (#44035) -- see also
-- ddl-coverage-watchdog (074), commitment-sweeper (076), live-checkout-guard (077), which
-- applied the same fix-before-first-page lesson.
--
-- Pure data: agents already exists on the live substrate -- nothing here touches
-- privileges, so no `-- assert:` lines are needed (house convention per migrations
-- 065/069/070/071/073/074/076/077 headers).
--
-- SHAPE -- mirrors sla-watchdog/ingest-watchdog/ddl-coverage-watchdog/commitment-sweeper/
-- live-checkout-guard's row exactly (id/display_name/repo_scope/status only; current_task
-- and last_heartbeat stay NULL/default, since this identity only ever POSTS a bus row --
-- it is not a polled/heartbeating lane).
--
-- REVERT: DELETE FROM public.agents WHERE id = 'programme-stall-guard';

INSERT INTO public.agents (id, display_name, repo_scope, status)
VALUES
  ('programme-stall-guard', 'No-live-checkpoint / corrupt-checkpoint-payload escalator (scripts/programme_stall_guard.py, bus #44697)', '{}', 'active')
ON CONFLICT (id) DO NOTHING;
