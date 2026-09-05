-- 036: pool_usage — latest Max weekly/5h usage reading per pool (op#9770 / #15000).
--
-- The weekly_limit_monitor (op#9658) polls the anthropic-ratelimit-unified-7d /
-- -5h utilization headers per pool (Musa, Syed) each run. Until now that reading
-- lived only in a logs/ JSON state file — not queryable by the console. This table
-- is the console's READ PATH: the monitor UPSERTs the latest reading here every
-- poll, and the fleet console renders `pct_7d` in its header ("weekly % up top").
--
-- One row per pool (pool = PK) — always the LATEST reading, overwritten in place;
-- `updated_at` lets the console show/greY-out a stale reading if the monitor stops
-- (same freshness doctrine as everything else on the console).
--
-- Ownership mirrors 035: cc-fleet-health writes via service_role; the read-only
-- console role only SELECTs. Apply via scripts/apply_migration.py 036
-- --silo tscuymavysscrvoberrr (historical applier: apply_pool_usage.py,
-- deleted 2026-09-05 PR #90; decision-962: NEVER `supabase db push`).

BEGIN;

CREATE TABLE IF NOT EXISTS public.pool_usage (
  pool        text PRIMARY KEY,                 -- 'Musa' | 'Syed'
  pct_7d      numeric,                           -- weekly-window utilization, 0..100
  pct_5h      numeric,                           -- 5-hour-window utilization, 0..100
  resets_at   timestamptz,                       -- when the weekly (7d) window resets
  status_7d   text,                              -- allowed | allowed_warning | ...
  updated_at  timestamptz NOT NULL DEFAULT now() -- freshness of THIS reading
);

ALTER TABLE public.pool_usage ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pool_usage_service_only ON public.pool_usage;
CREATE POLICY pool_usage_service_only ON public.pool_usage
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS pool_usage_console_ro ON public.pool_usage;
CREATE POLICY pool_usage_console_ro ON public.pool_usage
  FOR SELECT TO console_readonly USING (true);

REVOKE ALL ON public.pool_usage FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.pool_usage TO service_role;
GRANT SELECT ON public.pool_usage TO console_readonly;

COMMIT;
