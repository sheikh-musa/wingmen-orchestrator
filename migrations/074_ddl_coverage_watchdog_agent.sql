-- 074_ddl_coverage_watchdog_agent.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Registers 'ddl-coverage-watchdog' in public.agents (bus #44135/#44139/#44140,
-- op#22669 item 3 follow-on: apply_migration.py's known gap (b) — a repo's
-- production applies can bypass --gate entirely by running DDL directly
-- against a PRODUCTION_SILOS member instead of going through the tool).
-- scripts/ddl_coverage_watchdog.py pages orch-console via a bus row from this
-- identity when a silo's schema fingerprint drifts with no corresponding new
-- migration_ledger row. Applying bus #44035's lesson: this identity is
-- registered BEFORE the watchdog ever runs, so its first real page cannot
-- hit agent_messages_from_agent_fkey the way ingest-watchdog's did.
--
-- Pure data: agents already exists on the live substrate -- nothing here
-- touches privileges, so no `-- assert:` lines are needed (house convention
-- per migrations 065/069/070/071/073 headers).
--
-- SHAPE -- mirrors sla-watchdog/ingest-watchdog's row exactly (id/
-- display_name/repo_scope/status only; current_task and last_heartbeat stay
-- NULL/default, since this identity only ever POSTS bus rows -- it is not a
-- polled/heartbeating lane).
--
-- REVERT: DELETE FROM public.agents WHERE id = 'ddl-coverage-watchdog';

INSERT INTO public.agents (id, display_name, repo_scope, status)
VALUES
  ('ddl-coverage-watchdog', 'Per-silo schema-fingerprint drift watchdog (scripts/ddl_coverage_watchdog.py, op#22669 item 3 coverage gap)', '{}', 'active')
ON CONFLICT (id) DO NOTHING;
