-- 045_share_lane_status.sql — live (never-stale) team status for client share pages.
--
-- WHY (operator op#13342, 2026-08-15: "the gazzabyte status needs to show realtime
-- lane data. its so stale"): the client-facing board at share.wingmen.dev/r/gazzabyte-status
-- carried a HAND-BAKED "Snapshot as of 2026-Aug-14 14:59 UTC" team panel. Every number on
-- it froze at publish time — lane states, context %, "Weekly capacity used 23%" (musa2 was
-- actually 43%), and a "Resets in 4 days" countdown that only ever decays. Same failure
-- class the console's YOUR-ASKS board fixed in fc-v55: DO NOT STORE STATUS — derive it live.
--
-- This migration gives the share host (a separate Vercel app, wingmen-share) a
-- minimum-privilege, client-safe read path onto live fleet telemetry:
--   * share_lane_labels — the ONLY place a client-facing lane name/blurb is defined.
--     Internal agent_ids and lane keys NEVER reach the client; the view emits labels only.
--   * share_pool_map    — which capacity pool a tenant's work runs on.
--   * two SECURITY DEFINER-free views owned by postgres, so the reader role needs NO grant
--     on the underlying fleet tables (pane_context / fleet_lanes / pool_usage).
--   * role share_readonly — LOGIN, SELECT on exactly those two views and nothing else.
--
-- Apply with scripts/apply_migration.py 045 --silo tscuymavysscrvoberrr (historical
-- applier: apply_mig045_share_lane_status.py, deleted 2026-09-05 PR #89).
-- NEVER `supabase db push` against production (decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001).

-- ---------------------------------------------------------------- mapping tables

CREATE TABLE IF NOT EXISTS public.share_lane_labels (
  tenant      text    NOT NULL,
  lane        text    NOT NULL,           -- matches pane_context.session / fleet_lanes.lane
  label       text    NOT NULL,           -- what the CLIENT sees ("Coordinator")
  blurb       text    NOT NULL DEFAULT '',-- one-line role description for the client
  sort        int     NOT NULL DEFAULT 100,
  visible     boolean NOT NULL DEFAULT true,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, lane)
);

COMMENT ON TABLE public.share_lane_labels IS
  'Client-facing lane naming for share.wingmen.dev status boards. The owning client-facing '
  'coordinator lane owns these rows (wording is client content). A lane with no row here is '
  'INVISIBLE to the client — enrolment is opt-in, never automatic, so a newly spun internal '
  'lane can never leak onto a client board.';

CREATE TABLE IF NOT EXISTS public.share_pool_map (
  tenant     text PRIMARY KEY,
  pool       text NOT NULL,               -- matches pool_usage.pool
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- live views

-- Live per-lane status. Status is DERIVED at query time from pane_context (the pane-truth
-- gauge proven in fc-v53/fc-v54) — never stored, so it cannot go stale.
--
-- context_pct is fail-closed in the same shape as the console: prefer the parsed pct, fall
-- back to pane_k/1000 (1M-token window), else NULL. NULL renders as "—", never as a fake 0.
CREATE OR REPLACE VIEW public.share_lane_status_v AS
SELECT
  l.tenant,
  l.label,
  l.blurb,
  l.sort,
  -- FRESHNESS GUARD (the whole point of op#13342): pane_context rows persist after a lane
  -- stops being observed, so an un-refreshed row would render as live — the exact stale-as-
  -- live bug this replaces. The publisher ticks every 300s; beyond 3 missed ticks the row is
  -- no longer evidence of anything and the lane reads 'unknown', never a remembered state.
  CASE
    WHEN p.session IS NULL                                    THEN 'unknown'
    WHEN p.updated_at < now() - interval '15 minutes'         THEN 'unknown'
    WHEN p.idle_verdict IN ('WORKING', 'STAGED')              THEN 'active'
    WHEN p.idle_verdict = 'IDLE_EMPTY'                        THEN 'resting'
    ELSE 'unknown'
  END                                                   AS state,
  -- pane_k is reclaimable K-tokens against a 1,000,000-token window, so pct = pane_k / 10 —
  -- the SAME arithmetic and window the console uses (app.py _ctx_level / _CTX_WINDOW).
  -- An out-of-range reading is dropped to NULL ("—"), never clamped into a plausible-looking
  -- number: an unreadable gauge must say unknown, not invent a value.
  CASE
    WHEN p.updated_at < now() - interval '15 minutes' THEN NULL
    WHEN p.pct IS NOT NULL AND p.pct BETWEEN 0 AND 100
      THEN p.pct::int
    WHEN p.pct IS NULL AND p.pane_k IS NOT NULL AND p.pane_k > 0 AND p.pane_k <= 1000
      THEN ROUND(p.pane_k / 10.0)::int
    ELSE NULL
  END                                                   AS context_pct,
  p.updated_at                                          AS observed_at
FROM public.share_lane_labels l
LEFT JOIN public.pane_context p ON p.session = l.lane
WHERE l.visible
ORDER BY l.sort, l.label;

COMMENT ON VIEW public.share_lane_status_v IS
  'Client-safe live lane status (op#13342). Emits label/blurb/state/context_pct only — no '
  'agent_id, no lane key, no host, no tmux session. Status derived per query, never stored.';

-- Live capacity for a tenant's pool. Same rule: derived, never stored.
CREATE OR REPLACE VIEW public.share_pool_status_v AS
SELECT
  m.tenant,
  ROUND(u.pct_7d)::int                                   AS used_pct,
  ROUND(u.projected_pct)::int                            AS projected_pct,
  u.pace                                                 AS pace,
  u.resets_at                                            AS resets_at,
  u.updated_at                                           AS observed_at
FROM public.share_pool_map m
JOIN public.pool_usage u ON u.pool = m.pool
-- Same freshness rule as the lane view: a pool reading nobody has refreshed in an hour is
-- not a live capacity figure, and the board must show nothing rather than a remembered number.
WHERE u.updated_at > now() - interval '1 hour';

COMMENT ON VIEW public.share_pool_status_v IS
  'Client-safe live capacity for the pool a tenant''s work runs on (op#13342). Emits no pool '
  'name and no account identity — only the percentages the client board already displayed.';

-- ---------------------------------------------------------------- reader role
-- Minimum privilege: LOGIN role that can SELECT the two client-safe views and NOTHING else.
-- The views are owned by postgres, so no grant on pane_context / pool_usage is needed or given.
-- The password is set by the apply script (never committed).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'share_readonly') THEN
    CREATE ROLE share_readonly LOGIN;
  END IF;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM share_readonly;
GRANT USAGE ON SCHEMA public TO share_readonly;
GRANT SELECT ON public.share_lane_status_v TO share_readonly;
GRANT SELECT ON public.share_pool_status_v TO share_readonly;

-- Belt-and-braces: these views are internal-facing plumbing, not PostgREST surface.
REVOKE ALL ON public.share_lane_status_v FROM anon, authenticated;
REVOKE ALL ON public.share_pool_status_v FROM anon, authenticated;
REVOKE ALL ON public.share_lane_labels   FROM anon, authenticated;
REVOKE ALL ON public.share_pool_map      FROM anon, authenticated;

-- ---------------------------------------------------------------- seed (gazzabyte)
-- Parity with the panel already live on the board, plus the three lanes spun on 2026-08-15
-- that the frozen snapshot could never show. Labels are CLIENT WORDING — cc-irsyad-coord owns
-- them from here; edit these rows, not the HTML.

INSERT INTO public.share_pool_map (tenant, pool) VALUES ('gazzabyte', 'musa2')
ON CONFLICT (tenant) DO UPDATE SET pool = EXCLUDED.pool, updated_at = now();

INSERT INTO public.share_lane_labels (tenant, lane, label, blurb, sort) VALUES
  ('gazzabyte', 'irsyad-coord',          'Coordinator',    'Plans and sequences the team''s work',              10),
  ('gazzabyte', 'irsyad-prog1',          'Builder 1',      'Feature development',                                20),
  ('gazzabyte', 'irsyad-prog2',          'Builder 2',      'Feature development',                                30),
  ('gazzabyte', 'irsyad-receipt',        'Receipts',       'Issues donor receipts & appreciation letters',       40),
  ('gazzabyte', 'irsyad-tabung-jumaat',  'Tabung Jumaat',  'One combined Tabung Jumaat total',                   50),
  ('gazzabyte', 'irsyad-tabung-history', 'Tabung history', 'Imports past tabung history & Tabung Umum stats',    60),
  ('gazzabyte', 'irsyad-cisco-recon',    'Cisco coins',    'Banking & reconciliation for Cisco coins',           70)
ON CONFLICT (tenant, lane) DO UPDATE
  SET label = EXCLUDED.label, blurb = EXCLUDED.blurb, sort = EXCLUDED.sort, updated_at = now();
