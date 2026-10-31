-- 137_host_metrics_console_ro_grant.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- GRANT for the machines-resources panel (op#36664, bus #86675/#86707 —
-- /api/resources + /resources, console-revamp lane).
--
-- WHY: host_metrics (migration 096) holds the per-5-min CPU/RAM/temp samples
-- for the Mini/gzbai/cubeasht written by cc-fleet-health's mini_monitor.py/
-- gzb_monitor.py/cubeasht_monitor.py, but it was created service_role-only —
-- console_readonly has never held SELECT on it. Without this grant the new
-- endpoint 500s with `permission denied for table host_metrics` on every
-- load.
--
-- SCOPE: SELECT only, table-level, one role. host_metrics rows carry only
-- host identifiers + resource numbers (no PII). Read path is
-- nervous_system/console/db.py build_resources_query().
--
-- Applied via scripts/apply_migration.py (direct psycopg, same-transaction
-- ledger write) — NEVER `supabase db push` (decision 962).

BEGIN;

SET LOCAL lock_timeout = '5s';

GRANT SELECT ON public.host_metrics TO console_readonly;

COMMIT;
