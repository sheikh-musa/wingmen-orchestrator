-- 037: pool pace layer (op#12617) — PACE / PROJECTED / RUNWAY for the weekly pools.
--
-- 036 gave us `pool_usage` (latest absolute reading per pool). Absolute % answers
-- "how full NOW"; it does NOT answer "are we on track to blow the weekly limit
-- before it resets". This migration adds the PACE layer:
--
--   (a) three ADDITIVE columns on pool_usage — the LATEST computed pace metrics,
--       for the console header (back-compat: existing SELECTs name their columns,
--       so adding columns breaks nothing). All nullable (NULL until first pace run
--       / when there is no trailing reading for a runway).
--         pace          numeric  -- used% / elapsed% ; 1.0 == on pace, >1 == ahead
--         projected_pct numeric  -- linear end-of-week extrapolation (== pace*100)
--         runway_days   numeric  -- days-to-exhaust at trailing-24h burn (NULL == inf)
--
--   (b) pool_usage_history — an APPEND-ONLY trail of readings, so the monitor can
--       compute a TRAILING-24h burn rate (runway needs two same-window points; the
--       one-row-per-pool pool_usage table cannot hold history). One row per pool
--       per poll; `resets_at` is stamped so burn is only ever computed WITHIN a
--       window (a reset drops util to ~0 -> a spurious negative burn otherwise).
--
-- Ownership mirrors 035/036: cc-fleet-health (the SRE) writes via service_role;
-- the read-only console role only SELECTs. Apply via scripts/apply_migration.py 037
-- --silo tscuymavysscrvoberrr (historical applier: apply_pool_pace.py, deleted
-- 2026-09-05 PR #90; decision-962: NEVER `supabase db push`). Idempotent.

BEGIN;

ALTER TABLE public.pool_usage
  ADD COLUMN IF NOT EXISTS pace          numeric,
  ADD COLUMN IF NOT EXISTS projected_pct numeric,
  ADD COLUMN IF NOT EXISTS runway_days   numeric;

CREATE TABLE IF NOT EXISTS public.pool_usage_history (
  id          bigserial PRIMARY KEY,
  pool        text NOT NULL,
  pct_7d      numeric,
  pct_5h      numeric,
  resets_at   timestamptz,
  recorded_at timestamptz NOT NULL DEFAULT now()
);

-- Trailing-burn lookup: newest same-window reading older than ~24h, per pool.
CREATE INDEX IF NOT EXISTS pool_usage_history_pool_time
  ON public.pool_usage_history (pool, recorded_at DESC);

ALTER TABLE public.pool_usage_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pool_usage_history_service_only ON public.pool_usage_history;
CREATE POLICY pool_usage_history_service_only ON public.pool_usage_history
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS pool_usage_history_console_ro ON public.pool_usage_history;
CREATE POLICY pool_usage_history_console_ro ON public.pool_usage_history
  FOR SELECT TO console_readonly USING (true);

REVOKE ALL ON public.pool_usage_history FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.pool_usage_history TO service_role;
GRANT SELECT ON public.pool_usage_history TO console_readonly;
GRANT USAGE, SELECT ON SEQUENCE public.pool_usage_history_id_seq TO service_role;

COMMIT;
