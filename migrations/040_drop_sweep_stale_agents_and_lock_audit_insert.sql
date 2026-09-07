-- 040_drop_sweep_stale_agents_and_lock_audit_insert.sql
--
-- WHY (CAI-761/762/764/765, 2026-08-07): the LAST step of retiring the rejected-A
-- forgery pattern. sweep_stale_agents() offlined stale agents by set_config-ing
-- each ghost's OWN identity per row (the A-spoof cai rejected — the audit read as
-- each corpse marking itself). Its three runtime callers are now cut over to
-- admin_mark_offline (the scoped, offline-only, audited primitive):
--   * scripts/lib/auto_agent_id.py::reap_stale_family -> LAUNCHER branch;
--   * nervous_system/lane_watchdog.py::reap_ghosts     -> WATCHDOG branch;
--   * scripts/fleet_health.py                          -> LEASE branch.
-- ZERO runtime callers remain (re-verified before this migration; the only other
-- reference is scripts/apply_ghost_reaper.py's post-apply existence-ASSERTION for
-- the historical mig-009 — an inert one-time apply script, not a runtime caller;
-- deleted 2026-09-05 PR #88, so this note is now archaeology, not a live pointer).
--
-- Also folds in the CAI-765 audit-INSERT tighten: after 039 made admin_offline_audit
-- erase-proof (no UPDATE/DELETE/TRUNCATE for the RLS/service tiers), lock INSERT too
-- so ONLY fleet_reaper (via admin_mark_offline) + postgres can write it — no
-- fabricated reap-claims from service_role/authenticated/anon.
--
-- SCOPE: bus/coordination silo tscuymavysscrvoberrr. Apply via cai §6.6 guarded
-- path (orch-console executor, never db push). Runs as owner (postgres).
--
-- SEQUENCING (IMPORTANT): apply this ONLY AFTER the caller-cutover is COMMITTED to
-- git (the edits are already the on-disk code the launchd jobs/launcher run, so
-- runtime callers are already zero — but a git-based redeploy that predates the
-- cutover commit would reintroduce a sweep_stale_agents caller and then break on
-- the missing function). Re-verify zero callers at apply time.

BEGIN;

-- Retire the A-pattern function (one signature). IF EXISTS = idempotent.
DROP FUNCTION IF EXISTS public.sweep_stale_agents(interval, text, text);

-- Audit-INSERT tighten (CAI-765): admin_offline_audit is now append-only AND
-- only-fleet_reaper-writable. fleet_reaper keeps INSERT (037, via the fn);
-- postgres keeps owner DML; readers keep SELECT.
REVOKE INSERT ON public.admin_offline_audit FROM service_role, authenticated, anon;

COMMIT;

-- ===========================================================================
-- APPLY NOTES for cai (§6.6)
-- ===========================================================================
-- Verified by cc-fleet-health (rolled-back psql):
--   * DROP FUNCTION succeeds (postgres owns sweep_stale_agents); no dependent
--     objects (zero runtime callers).
--   * post-REVOKE, admin_offline_audit: service_role/authenticated/anon have ZERO
--     INSERT/UPDATE/DELETE/TRUNCATE (fully locked); fleet_reaper retains INSERT +
--     SELECT; postgres retains owner DML.
-- LEDGER: repo=orchestrator, migration_name=040_drop_sweep_stale_agents_and_lock_audit_insert.sql,
-- silo_ref=tscuymavysscrvoberrr.
-- AFTER APPLY: a clean fleet_health sweep + the A-pattern is gone fleet-wide ->
-- cai closes CAI-761/762/764/765 green-final.
-- FOLLOW-UP (non-blocking, my note): scripts/apply_ghost_reaper.py still asserts
-- sweep_stale_agents exists (mig-009 apply script) — it will fail if ever re-run
-- post-drop. Harmless (one-time historical script), but worth a comment/removal in
-- a future housekeeping pass; not folded here to keep this migration minimal.
-- ===========================================================================
