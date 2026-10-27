-- 121_operator_ask_turnaround.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
-- assert: no_table_privilege anon public.operator_ask_turnaround SELECT
-- assert: no_table_privilege authenticated public.operator_ask_turnaround SELECT
-- assert: no_table_privilege anon public.operator_ask_turnaround INSERT
-- assert: no_table_privilege authenticated public.operator_ask_turnaround INSERT
-- assert: no_table_privilege anon public.operator_ask_turnaround UPDATE
-- assert: no_table_privilege authenticated public.operator_ask_turnaround UPDATE
-- assert: no_table_privilege anon public.operator_ask_turnaround_daily SELECT
-- assert: no_table_privilege authenticated public.operator_ask_turnaround_daily SELECT
--
-- Operator-ask turnaround measurement (Musa op#34803; orch-console #78190 plan,
-- approved with one change in #78198).
--
-- WHY: Musa: "lets measure how long these ui changes take and always aim to
-- improve". Turnaround was hand-computed from operator_asks + deploy times.
--
-- WHAT:
--  * operator_asks.delivered_at: the LIVE-verified stamp, set by
--    `asks_triage.py --delivered` (first stamp wins). UI asks deliberately stay
--    OPEN after live until Musa confirms (agents can't log in to test), so
--    closed_at measures Musa's reply latency, not ours (#78198). Turnaround =
--    delivered_at, falling back to closed_at.
--  * operator_asks.slow_paged_at: once-only dedup for the slow-ask page (open
--    > 2h with no lane subject ack), so it survives daemon restarts.
--  * operator_ask_turnaround: one row per delivered ask, phases derived from
--    lane bus SUBJECTS citing op#N (body mentions are false acks, see #78194).
--    Refreshed by scripts/ask_turnaround.py (idempotent upsert on ask_id).
--  * operator_ask_turnaround_daily: per SGT-day x surface rollup for the digest.
--
-- Access: same lockdown shape as 103/116. Service role writes, console reads,
-- anon/authenticated nothing. The view is security_invoker so the table's RLS
-- applies to whoever reads it.
--
-- Applied via scripts/apply_migration.py (direct psycopg, same-transaction
-- ledger write) — NEVER `supabase db push` (decision 962).

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.operator_asks ADD COLUMN IF NOT EXISTS delivered_at timestamptz;
ALTER TABLE public.operator_asks ADD COLUMN IF NOT EXISTS slow_paged_at timestamptz;

CREATE TABLE IF NOT EXISTS public.operator_ask_turnaround (
  ask_id           bigint PRIMARY KEY REFERENCES public.operator_asks(id) ON DELETE CASCADE,
  op               bigint NOT NULL,                 -- operator_messages.id (= op#N)
  surface          text   NOT NULL,                 -- from the ACKING lane's family
  ack_agent        text,
  asked_at         timestamptz NOT NULL,
  acked_at         timestamptz,
  started_at       timestamptz,
  delivered_at     timestamptz NOT NULL,
  delivered_source text   NOT NULL CHECK (delivered_source IN ('delivered_at', 'closed_at')),
  queue_wait       interval,                        -- asked -> first lane subject ack
  queued_after_ack interval,                        -- ack -> started (when started is known)
  build_to_live    interval,                        -- coalesce(started, ack) -> delivered
  total            interval NOT NULL,               -- asked -> delivered
  refreshed_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS operator_ask_turnaround_delivered_idx
  ON public.operator_ask_turnaround (delivered_at);

ALTER TABLE public.operator_ask_turnaround ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS operator_ask_turnaround_service_only ON public.operator_ask_turnaround;
CREATE POLICY operator_ask_turnaround_service_only ON public.operator_ask_turnaround
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS operator_ask_turnaround_console_ro ON public.operator_ask_turnaround;
CREATE POLICY operator_ask_turnaround_console_ro ON public.operator_ask_turnaround
  FOR SELECT TO console_readonly USING (true);
REVOKE ALL ON public.operator_ask_turnaround FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.operator_ask_turnaround TO console_readonly;
GRANT ALL ON public.operator_ask_turnaround TO service_role;

CREATE OR REPLACE VIEW public.operator_ask_turnaround_daily
  WITH (security_invoker = true) AS
SELECT
  (t.delivered_at AT TIME ZONE 'Asia/Singapore')::date            AS day_sgt,
  COALESCE(t.surface, 'all')                                      AS surface,
  count(*)                                                        AS delivered,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY t.total)            AS median_total,
  percentile_cont(0.9) WITHIN GROUP (ORDER BY t.total)            AS p90_total,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY t.queue_wait)       AS median_queue_wait,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY t.build_to_live)    AS median_build_to_live,
  max(t.total)                                                    AS max_total
FROM public.operator_ask_turnaround t
GROUP BY GROUPING SETS (((t.delivered_at AT TIME ZONE 'Asia/Singapore')::date, t.surface),
                        ((t.delivered_at AT TIME ZONE 'Asia/Singapore')::date));

REVOKE ALL ON public.operator_ask_turnaround_daily FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.operator_ask_turnaround_daily TO console_readonly;
GRANT SELECT ON public.operator_ask_turnaround_daily TO service_role;

COMMIT;
