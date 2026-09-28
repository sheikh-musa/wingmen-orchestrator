-- 077_live_checkout_guard_agent.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Registers 'live-checkout-guard' in public.agents (bus #44681). scripts/git-hooks/
-- post-checkout posts a P1 bus row to orch-console under this identity when the LIVE
-- checkout ($HOME/wingmen/orchestrator -- the commitment sweeper, DDL watchdog, and every
-- other launchd daemon execute from it) lands on any branch other than
-- fable/substrate-safe-fixes. Same fix-before-first-page lesson as ddl-coverage-watchdog
-- (migration 074) and commitment-sweeper (migration 076): agent_messages.from_agent FKs
-- to agents.id, so the identity must exist BEFORE the hook's first live post, or it hits
-- agent_messages_from_agent_fkey the way ingest-watchdog's did (#44035).
--
-- Pure data: agents already exists on the live substrate -- nothing here touches
-- privileges, so no `-- assert:` lines are needed (house convention per migrations
-- 065/069/070/071/073/074/076 headers).
--
-- SHAPE -- mirrors sla-watchdog/ingest-watchdog/ddl-coverage-watchdog/commitment-sweeper's
-- row exactly (id/display_name/repo_scope/status only; current_task and last_heartbeat
-- stay NULL/default, since this identity only ever POSTS a bus row from a git hook -- it
-- is not a polled/heartbeating lane).
--
-- REVERT: DELETE FROM public.agents WHERE id = 'live-checkout-guard';

INSERT INTO public.agents (id, display_name, repo_scope, status)
VALUES
  ('live-checkout-guard', 'post-checkout hook guard against branch drift in the live orchestrator checkout (bus #44681)', '{}', 'active')
ON CONFLICT (id) DO NOTHING;
