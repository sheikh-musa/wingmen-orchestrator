-- DRAFT -- hc#95 exec-broker dead-man heartbeat (cutover spec bus #89814 §5.2).
-- PROPOSE-ONLY: orch-console applies via direct psycopg after gating (never `supabase db push`).
-- NOT applied. Migration NNN assigned by CORE at apply. Target: the fleet-bus DB
-- (same database as exec_broker_access_log -- the broker's single DSN).
--
-- PREREQUISITE: exec_broker_access_log must already exist (it is LIVE on the
-- substrate; created by migrations/drafts/hc95_exec_broker_access_log.sql). This
-- file adds a partial index to it (exec_broker_access_log_open_idx, bottom of the
-- file), so applying it to a DB without that table fails and rolls back.
-- Apply order within hc#95: gate_ref -> single_use -> THIS -> exec_prod_ledger ->
-- broker_watch_readonly.
--
-- One row per GATED ref (the broker's standing EXEC_BROKER_LIVE_GATED_REFS set).
-- Writer: scripts/lib/exec_broker/heartbeat.py, started by exec_broker_daemon.py
-- before it serves. Shape mirrors migrations/023_fleet_health_lease.sql: CAS take
-- at boot (only if the row is mine or expired), renew = stamp beat_at from the DB
-- clock, TTL = expiry.
--
-- STALE (the watchdog rule, cc-fleet-health hc#1001):
--   a gated ref has NO row, OR now() > beat_at + make_interval(secs => ttl_seconds).
-- Absence of a broker must never read as "no executions happened": a missing row
-- for a gated ref is stale, not healthy.
--
-- NOT SERVING (page too, distinct cause): the row is FRESH but loop_ok IS NOT TRUE
-- (false, or NULL past the first tick) -- the process is alive and reaching the
-- DB, but its accept loop did not answer the self-probe.
--
-- DEPLOY ORDER: apply THIS before deploying the heartbeat daemon code -- the daemon
-- REFUSES TO BOOT if it cannot take its heartbeat rows (fail closed).
--
-- ALSO IN THIS FILE: the partial index exec_broker_access_log_open_idx on
-- exec_broker_access_log (ts_start) WHERE ts_end IS NULL, for the watchdog's
-- open-row (stuck 'started') probe -- see the bottom of the file.

CREATE TABLE IF NOT EXISTS exec_broker_heartbeat (
  project_ref      text PRIMARY KEY,                 -- the gated ref this broker serves
  holder_host      text        NOT NULL,             -- fleet_host_id() of the broker body
  broker_pid       integer     NOT NULL,
  boot_id          uuid        NOT NULL,             -- random per daemon boot
  started_at       timestamptz NOT NULL DEFAULT now(),
  beat_at          timestamptz NOT NULL DEFAULT now(),  -- DB clock of the last beat
  beat_seq         bigint      NOT NULL DEFAULT 0,   -- beats since started_at
  interval_seconds integer     NOT NULL CHECK (interval_seconds BETWEEN 10 AND 900),
  ttl_seconds      integer     NOT NULL CHECK (ttl_seconds >= interval_seconds),
  taken_over_from  text,                             -- previous holder's boot_id, stamped on ANY boot_id change
  takeover_reason  text CHECK (takeover_reason IS NULL
                               OR takeover_reason IN ('same_host_new_boot', 'host_change')),
  taken_over_from_host text,                         -- previous holder's holder_host, same CASE arms as taken_over_from
  -- Listener self-probe (cc-fleet-health bus #89888): each beat also records
  -- whether the daemon's OWN accept loop answered a b"\n" probe with a
  -- well-formed response line. NULL until the first tick after boot.
  loop_ok          boolean,
  loop_checked_at  timestamptz,                      -- DB now() when the probe result was written
  loop_latency_ms  integer,
  loop_error       text CHECK (loop_error IS NULL OR char_length(loop_error) <= 120)
);

-- Idempotent column adds, in case an earlier revision of this draft was applied.
ALTER TABLE exec_broker_heartbeat ADD COLUMN IF NOT EXISTS loop_ok boolean;
ALTER TABLE exec_broker_heartbeat ADD COLUMN IF NOT EXISTS loop_checked_at timestamptz;
ALTER TABLE exec_broker_heartbeat ADD COLUMN IF NOT EXISTS loop_latency_ms integer;
ALTER TABLE exec_broker_heartbeat ADD COLUMN IF NOT EXISTS loop_error text;
ALTER TABLE exec_broker_heartbeat ADD COLUMN IF NOT EXISTS taken_over_from_host text;

-- Watchdog open-row probe (coord, 2026-11-01): exec_broker_access_log rows still
-- 'started' (ts_end IS NULL) past a threshold mean a broker died mid-call. The
-- partial index keeps that probe cheap as the log grows. It lives HERE, not in
-- the already-merged hc95_exec_broker_gate_ref.sql draft (which must not change).
CREATE INDEX IF NOT EXISTS exec_broker_access_log_open_idx
  ON exec_broker_access_log (ts_start) WHERE ts_end IS NULL;

ALTER TABLE exec_broker_heartbeat ENABLE ROW LEVEL SECURITY;

-- Same Supabase default-privileges lesson as hc95_exec_broker_access_log.sql
-- (bus #72301): name every platform role explicitly.
REVOKE ALL ON exec_broker_heartbeat FROM PUBLIC, authenticated, anon, service_role;

-- The broker's own role writes its rows; no DELETE to anyone (a deleted row would
-- read as "stale", which pages -- but keep it append/update-only anyway).
GRANT INSERT, UPDATE, SELECT ON exec_broker_heartbeat TO exec_broker_role;

-- exec_broker_role is NOT a superuser and the table has RLS enabled: give it a
-- policy so its own writes/reads are not silently filtered to nothing.
DO $$ BEGIN
  CREATE POLICY exec_broker_heartbeat_broker_rw ON exec_broker_heartbeat
    FOR ALL TO exec_broker_role USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- WATCHDOG READ ACCESS: orch-console grants SELECT (plus a matching SELECT policy)
-- to whichever role cc-fleet-health's watchdog connects as, at apply time -- that
-- role is a provisioning decision, deliberately not hard-coded here.
