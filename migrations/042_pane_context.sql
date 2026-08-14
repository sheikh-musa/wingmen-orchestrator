-- 042: pane_context — the fresh, per-pane context-bloat feed (op#13050-B, console-
-- signed bus 21515). The DB context gauge (cc_session_costs) LIES for worker lanes
-- (stale-on-idle + base/instance mis-map: cc-irsyad gauge 8% vs pane 795.9k; shipforge
-- 13% vs 652.4k — verified 2026-08-15). The ground truth is CC's own pane reclaim hint
-- `new task? /clear to save {N}k tokens`, read live off each Mini-local pane.
--
-- A Mini-local publisher (the auto_recycle observe pass) UPSERTs one row per LIVE tmux
-- session every cycle; the VPS console SELECTs it for the honest header + top-bloat
-- glance (op#13050-B). `updated_at` is the freshness gate: a row older than the
-- consumer's TTL is treated as UNKNOWN (never a stale false-green, never a gauge
-- fallback — the gauge IS the bug). A Mini-local lane with NO fresh row = a publisher-
-- down health signal. Same freshness doctrine + ownership as 041/body_activity_verdict
-- and 036/pool_usage.
--
-- pane_k semantics: reclaimable K-tokens from the hint, or NULL when NO hint is showing
-- (context below CC's ~440k nudge bar OR mid-turn). idle_verdict disambiguates a NULL
-- (WORKING = busy high-ctx no-hint vs IDLE_EMPTY = genuinely low ctx). The VPS hub is
-- the cross-host exception (self-publishes on its heartbeat as a fast-follow; interim
-- the console uses gauge-with-'may-lag' for the hub ONLY).
--
-- Pure OPS-CACHE: additive, no PII / money / governance / residency, on the shared
-- substrate — NOT a cai-gated class (console signed the schema add directly, 21515).
-- Ownership mirrors 041: service_role writes, console_readonly SELECTs. Apply via
-- direct psycopg (decision-962: NEVER `supabase db push`) —
-- scripts/apply_mig042_pane_context.py. Reversible: DROP TABLE public.pane_context.

BEGIN;

CREATE TABLE IF NOT EXISTS public.pane_context (
  session      text PRIMARY KEY,                  -- tmux session name (per-pane, e.g. 'shipforge')
  base         text,                              -- fleet_lanes base_agent_id, or NULL if unmapped
  pane_k       real,                              -- reclaimable K-tokens from the /clear hint; NULL = no hint
  idle_verdict text,                              -- body_activity_oracle state (IDLE_EMPTY|STAGED|WORKING|GHOST_WEDGED|UNSURE)
  host         text,                              -- publishing host (e.g. 'Sheikhs-Mini')
  updated_at   timestamptz NOT NULL DEFAULT now() -- freshness of THIS reading
);

ALTER TABLE public.pane_context ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pane_context_service_only ON public.pane_context;
CREATE POLICY pane_context_service_only ON public.pane_context
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS pane_context_console_ro ON public.pane_context;
CREATE POLICY pane_context_console_ro ON public.pane_context
  FOR SELECT TO console_readonly USING (true);

REVOKE ALL ON public.pane_context FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.pane_context TO service_role;
GRANT SELECT ON public.pane_context TO console_readonly;

COMMIT;
