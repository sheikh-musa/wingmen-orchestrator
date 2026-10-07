-- 096_host_metrics.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Resource + temperature samples for fleet hosts, starting with cubeasht (Musa's
-- Windows desktop, left on 24/7, which hosts the self-hosted GitHub Actions
-- runner `cubeasht-orchestrator` in WSL). Written every 5 min by
-- scripts/cubeasht_monitor.py (cc-fleet-health; orch-console #58069/#58243,
-- Musa op#27169). One row per sample attempt:
--   ok=true   -> metrics holds GPU/NVMe temps, CPU load, RAM, disk C:, WSL, runner
--   ok=false  -> the probe failed (desktop asleep/off/unreachable, or unparseable
--                probe output); `error` says which. Failed samples are stored on
--                purpose so the "unreachable >30 min" alert has a window to read.
-- Retention: the collector deletes rows older than 30 days (bounded LIMIT per run).
--
-- All additive (new table + index only). REVERT: DROP TABLE host_metrics;

CREATE TABLE IF NOT EXISTS host_metrics (
  id       bigserial PRIMARY KEY,
  host     text        NOT NULL,
  ts       timestamptz NOT NULL DEFAULT now(),
  metrics  jsonb       NOT NULL,
  ok       boolean     NOT NULL,
  error    text
);

CREATE INDEX IF NOT EXISTS host_metrics_host_ts_idx ON host_metrics (host, ts);

-- RLS on the new table (pg_default_acl grants anon/authenticated on every new
-- table — a public table without RLS is reachable via the anon/authenticated
-- API key). service_role-only; REVOKE the default grants. Re-run-safe.
-- assert: no_table_privilege anon public.host_metrics SELECT
-- assert: no_table_privilege authenticated public.host_metrics SELECT
-- assert: no_table_privilege authenticated public.host_metrics INSERT
-- assert: no_table_privilege authenticated public.host_metrics UPDATE
-- assert: no_table_privilege authenticated public.host_metrics DELETE
-- assert: no_sequence_privilege anon public.host_metrics_id_seq USAGE
-- assert: no_sequence_privilege authenticated public.host_metrics_id_seq USAGE
ALTER TABLE host_metrics ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS host_metrics_service_only ON host_metrics;
CREATE POLICY host_metrics_service_only ON host_metrics
  FOR ALL TO service_role USING (true) WITH CHECK (true);
REVOKE ALL ON host_metrics FROM anon, authenticated;
REVOKE ALL ON SEQUENCE host_metrics_id_seq FROM anon, authenticated;
