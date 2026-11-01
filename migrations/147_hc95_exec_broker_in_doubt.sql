-- 147_hc95_exec_broker_in_doubt.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- hc#95 queue row 272 (cc-fleet-health bus #89969): boot-time IN-DOUBT
-- reconcile for exec_broker_access_log.
-- Apply via scripts/apply_migration.py with a --gate decision row citing this
-- file's sha12 (direct psycopg, same-transaction ledger write) -- NEVER
-- `supabase db push` (decision 962). Apply AFTER 142-146.
-- PREREQUISITE: exec_broker_access_log exists (live on the substrate; created by
-- migrations/drafts/hc95_exec_broker_access_log.sql, outside the numbered chain).
-- No REVOKE / DROP FUNCTION in this file -> no `-- assert:` lines are required.
--
-- WHAT: a daemon killed mid upstream call (SIGKILL/OOM) leaves its row at
-- status='started', ts_end NULL, forever -- an execution whose outcome is
-- UNKNOWN. At boot, BEFORE serving and before taking heartbeat rows, the broker
-- (scripts/exec_broker_daemon.py _reconcile_in_doubt ->
-- vault_log.mark_previous_boots_in_doubt) moves every such row that belongs to
-- a PREVIOUS boot to status 'in_doubt' and stamps in_doubt_at. It NEVER
-- retries them and NEVER finalizes them as ok/error.
--
-- DESIGN (options weighed):
--   * boot_id (this file) -- written by log_started on every row: the SAME uuid
--     the daemon uses as its heartbeat boot_id. "Previous boot" is then exact:
--     boot_id differs from this boot's AND is not the boot of a broker that is
--     provably alive on ANOTHER host (fresh exec_broker_heartbeat row, different
--     holder_host). Rows written before this column existed (boot_id NULL) are
--     previous-boot by definition. Rejected alternative: ts_start < this boot's
--     start time -- clock-skew sensitive, and it cannot tell a dead boot's rows
--     from a live second broker's.
--   * ts_end stays NULL for in_doubt rows: we genuinely do not know when (or
--     whether) the upstream call finished, and the audit must never imply an end
--     time it does not have. in_doubt_at records when the broker DECLARED it in
--     doubt instead.
--   * WATCHDOG SEMANTICS: an OPEN row is now  status = 'started' AND ts_end IS NULL
--     (in-flight on the current boot -- the NOT SERVING exemption for open rows
--     younger than the max upstream timeout applies only to these). An IN-DOUBT
--     row is  status = 'in_doubt'  -- never "open", never auto-cleared; it needs a
--     human/console decision. A watchdog that still filters on ts_end IS NULL
--     alone MUST add status = 'started'. The partial index below serves exactly
--     the new open-row predicate; 144's ts_end-only index stays (harmless).

ALTER TABLE exec_broker_access_log ADD COLUMN IF NOT EXISTS boot_id uuid;
ALTER TABLE exec_broker_access_log ADD COLUMN IF NOT EXISTS in_doubt_at timestamptz;

-- Extend the status CHECK with 'in_doubt'. The original inline CHECK
-- (hc95_exec_broker_access_log.sql) carries Postgres' default name
-- exec_broker_access_log_status_check; replaced in the same transaction.
ALTER TABLE exec_broker_access_log DROP CONSTRAINT IF EXISTS exec_broker_access_log_status_check;
ALTER TABLE exec_broker_access_log ADD CONSTRAINT exec_broker_access_log_status_check
  CHECK (status IN ('started', 'ok', 'error', 'in_doubt'));

-- in_doubt_at is set exactly when (and only when) a row is in doubt.
DO $$ BEGIN
  ALTER TABLE exec_broker_access_log ADD CONSTRAINT exec_broker_access_log_in_doubt_at_matches
    CHECK ((status = 'in_doubt') = (in_doubt_at IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS exec_broker_access_log_started_open_idx
  ON exec_broker_access_log (ts_start) WHERE status = 'started' AND ts_end IS NULL;
CREATE INDEX IF NOT EXISTS exec_broker_access_log_boot_id_idx
  ON exec_broker_access_log (boot_id);

COMMENT ON COLUMN exec_broker_access_log.boot_id IS
  'hc#95 queue row 272: the broker daemon boot that wrote this row (same uuid as its exec_broker_heartbeat.boot_id).';
COMMENT ON COLUMN exec_broker_access_log.in_doubt_at IS
  'hc#95 queue row 272: when a later boot declared this previous-boot ''started'' row in_doubt (outcome unknown; never retried; ts_end stays NULL).';
