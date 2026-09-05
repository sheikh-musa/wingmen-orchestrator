-- 041: body_activity_verdict — the cross-host body-activity oracle verdict cache
-- (op#11774 Phase-1, G-b VPS-instance oracle; console-signed 18932).
--
-- The hub (cc-orchestrator) runs on the VPS; the SRE oracle runs on the Mini and
-- cannot read the hub's tmux pane directly (VPS-instance-not-ssh, 18673). So a
-- detect-only oracle instance on the VPS reads the hub's LOCAL pane and UPSERTs its
-- verdict here every ~60s; the Mini oracle SELECTs the latest row for a remote body.
-- This is the shared-substrate publish channel — NO ssh in the read path.
--
-- One row per agent (agent = PK), always the LATEST verdict, overwritten in place;
-- `updated_at` is the freshness gate: the Mini treats a verdict older than its TTL
-- (default 180s) as UNSURE — so a dead VPS publisher fails SAFE to UNSURE, never a
-- stale coverage-guess (same freshness doctrine as pool_usage / the whole console).
--
-- Pure OPS-CACHE: additive, no PII / money / governance / residency, on the shared
-- substrate — NOT a cai-gated class (console signed the schema add directly, 18932).
-- Ownership mirrors 036/pool_usage: service_role writes, console_readonly SELECTs
-- (so #4a's honest board can render it later). Apply via scripts/apply_migration.py 041
-- --silo tscuymavysscrvoberrr (historical applier: apply_mig041_body_activity_verdict.py,
-- deleted 2026-09-05 PR #89; decision-962: NEVER `supabase db push`).
-- Reversible: DROP TABLE public.body_activity_verdict.

BEGIN;

CREATE TABLE IF NOT EXISTS public.body_activity_verdict (
  agent       text PRIMARY KEY,                  -- e.g. 'cc-orchestrator'
  state       text NOT NULL,                     -- WORKING|IDLE_EMPTY|STAGED|GHOST_WEDGED|UNSURE
  reason      text,                              -- human-readable classify() reason
  host        text,                              -- publishing host (e.g. 'vps')
  updated_at  timestamptz NOT NULL DEFAULT now() -- freshness of THIS verdict
);

ALTER TABLE public.body_activity_verdict ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS body_activity_verdict_service_only ON public.body_activity_verdict;
CREATE POLICY body_activity_verdict_service_only ON public.body_activity_verdict
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS body_activity_verdict_console_ro ON public.body_activity_verdict;
CREATE POLICY body_activity_verdict_console_ro ON public.body_activity_verdict
  FOR SELECT TO console_readonly USING (true);

REVOKE ALL ON public.body_activity_verdict FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.body_activity_verdict TO service_role;
GRANT SELECT ON public.body_activity_verdict TO console_readonly;

COMMIT;
