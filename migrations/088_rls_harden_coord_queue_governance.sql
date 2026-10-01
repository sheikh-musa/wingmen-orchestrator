-- 088_rls_harden_coord_queue_governance.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- SECURITY (orch-console #48445, Musa-path, P1 — SEPARATE from 087, applied FIRST): two
-- public tables had ROW LEVEL SECURITY OFF while anon/authenticated held table grants:
--   * coord_dispatch_queue        — anon=SELECT, authenticated=SELECT/INSERT/UPDATE/DELETE.
--     Its rows carry irsyad work specs with CLIENT names/details in the title → anyone with
--     the public anon key could READ them, any authenticated user could REWRITE the queue.
--   * project_governance_families — anon=SELECT, authenticated=full.
-- Consumer audit (rg orchestrator + role check): every fleet consumer of both tables
-- (scripts/queue_age_watchdog.py, scripts/irsyad_autoscaler.py, scripts/irsyad_spin_worker.py)
-- connects as `postgres` (rolbypassrls=true) → UNAFFECTED by RLS. The console does not read
-- either table (no nervous_system/console ref), so no console_readonly policy is needed.
-- Fix: enable RLS, add a service_role FOR ALL policy, and REVOKE the anon/authenticated
-- grants. Re-run-safe (ENABLE RLS is idempotent; DROP POLICY IF EXISTS before CREATE; REVOKE
-- is idempotent). (For the record, the other RLS-off public tables —
-- invariant_assertion_runs, share_lane_labels, share_pool_map — have NO anon/authenticated
-- SELECT grant, so they are not anon-reachable; left for a separate sweep.)
--
-- Post-apply assertions (CAI-RESP-1397 #5 — a REVOKE can be a silent no-op, so prove the
-- effect): after this migration, anon + authenticated must hold NO data-access privilege on
-- either table. apply_migration rolls back + refuses if any assertion is still true.
-- assert: no_table_privilege anon public.coord_dispatch_queue SELECT
-- assert: no_table_privilege authenticated public.coord_dispatch_queue SELECT
-- assert: no_table_privilege authenticated public.coord_dispatch_queue INSERT
-- assert: no_table_privilege authenticated public.coord_dispatch_queue UPDATE
-- assert: no_table_privilege authenticated public.coord_dispatch_queue DELETE
-- assert: no_table_privilege anon public.project_governance_families SELECT
-- assert: no_table_privilege authenticated public.project_governance_families SELECT
-- assert: no_table_privilege authenticated public.project_governance_families INSERT
-- assert: no_table_privilege authenticated public.project_governance_families UPDATE
-- assert: no_table_privilege authenticated public.project_governance_families DELETE

ALTER TABLE coord_dispatch_queue        ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_governance_families ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS coord_dispatch_queue_service_only ON coord_dispatch_queue;
CREATE POLICY coord_dispatch_queue_service_only ON coord_dispatch_queue
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS project_governance_families_service_only ON project_governance_families;
CREATE POLICY project_governance_families_service_only ON project_governance_families
  FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON coord_dispatch_queue        FROM anon, authenticated;
REVOKE ALL ON project_governance_families FROM anon, authenticated;
