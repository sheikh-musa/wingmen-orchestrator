-- CC-WATCHDOG-CALIBRATION-001 tracking infra per CAI-RESP-168 §5
--
-- Cai ratified Phase B (CAI-RESP-167) with a 30-day calibration window
-- (2026-05-26 → 2026-06-25) for the content-shape thresholds. CAI-RESP-168 §5
-- amended the calibration scope: "CC-WATCHDOG-CALIBRATION-001 must
-- additionally track signal_a NEAR-MISSES — any real (non-probe) caller
-- whose median file size lands within 15 percent of the 80KB threshold.
-- Binary FP/TP outcome data is insufficient."
--
-- The current substrate logs hard_kill (notification_log), monitored
-- (watchdog_monitored_callers), and aborted_kill (notification_log). But
-- no_action paths (any signal unobservable) and no_kill paths (substrate-
-- native carve-out + panic + registered_no_kill) compute signal_a yet
-- discard the result, leaving the calibration window blind.
--
-- This migration adds a single audit table that the watchdog sweep writes
-- to UNCONDITIONALLY for every evaluated caller, capturing the full
-- (signal_a, signal_b, signal_c, action) tuple. At day 30, the calibration
-- script reads 30 days of these rows + the existing hard_kill / monitored
-- logs to produce the FP/TP + near-miss distribution for the
-- CC-WATCHDOG-CALIBRATION-001 filing.

BEGIN;

CREATE TABLE IF NOT EXISTS watchdog_calibration_observations (
    id                      BIGSERIAL PRIMARY KEY,
    observed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    cc_identity             TEXT NOT NULL,
    caller_name             TEXT NOT NULL,
    sessions_24h            INTEGER NOT NULL,
    cadence_seconds         INTEGER,
    parent_pid              INTEGER,
    -- signal_a is median file size in bytes (typical 50KB–3MB; INTEGER fine)
    signal_a_value          INTEGER,
    -- signal_b/c are richer structures (cadence band detail / first-prompt text)
    signal_b_value          JSONB,
    signal_c_value          JSONB,
    -- match booleans (NULL when unobservable)
    signal_a_match          BOOLEAN,
    signal_b_match          BOOLEAN,
    signal_c_match          BOOLEAN,
    -- unobservable flags (>50% files unreadable) — explicit so queries don't
    -- conflate "no match" with "couldn't check"
    signal_a_unobservable   BOOLEAN NOT NULL DEFAULT false,
    signal_b_unobservable   BOOLEAN NOT NULL DEFAULT false,
    signal_c_unobservable   BOOLEAN NOT NULL DEFAULT false,
    -- registry + decision context
    registered              BOOLEAN NOT NULL,
    registered_policy       TEXT,
    action                  TEXT NOT NULL,  -- 'hard_kill'|'monitored'|'no_kill'|'no_action'
    decision_reason         TEXT,
    -- near-miss flag for fast queries: signal_a in [80KB ± 15%] band per CAI-RESP-168 §5
    signal_a_near_miss      BOOLEAN GENERATED ALWAYS AS (
        signal_a_value IS NOT NULL
        AND signal_a_value >= 68 * 1024
        AND signal_a_value <= 92 * 1024
    ) STORED
);

COMMENT ON TABLE watchdog_calibration_observations IS
    'CAI-RESP-168 §5 + CC-WATCHDOG-CALIBRATION-001: one row per watchdog '
    'sweep evaluation regardless of action. Powers the 30-day FP/TP + '
    'signal_a-near-miss distribution analysis for threshold lock-in.';

COMMENT ON COLUMN watchdog_calibration_observations.signal_a_near_miss IS
    'Generated: TRUE when signal_a_value lands in the 15% band around the '
    '80KB threshold (68KB ≤ value ≤ 92KB). Tracks operator-product workload '
    'proximity to false-positive territory.';

CREATE INDEX IF NOT EXISTS idx_wco_observed_at
    ON watchdog_calibration_observations (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_wco_cc_identity_observed
    ON watchdog_calibration_observations (cc_identity, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_wco_near_miss
    ON watchdog_calibration_observations (observed_at DESC)
    WHERE signal_a_near_miss = true;
CREATE INDEX IF NOT EXISTS idx_wco_action
    ON watchdog_calibration_observations (action, observed_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_name='watchdog_calibration_observations'
    ) THEN
        RAISE EXCEPTION 'watchdog_calibration_observations table missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name='watchdog_calibration_observations'
           AND column_name='signal_a_near_miss'
    ) THEN
        RAISE EXCEPTION 'signal_a_near_miss generated column missing';
    END IF;
    RAISE NOTICE 'CC-WATCHDOG-CALIBRATION-001 observation table verified';
END $$;

COMMIT;

INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES (
    '20260603120000',
    'watchdog_calibration_observations',
    ARRAY[
        $stmt$CREATE TABLE IF NOT EXISTS watchdog_calibration_observations (id BIGSERIAL PRIMARY KEY, observed_at TIMESTAMPTZ NOT NULL DEFAULT now(), cc_identity TEXT NOT NULL, caller_name TEXT NOT NULL, sessions_24h INTEGER NOT NULL, cadence_seconds INTEGER, parent_pid INTEGER, signal_a_value INTEGER, signal_b_value JSONB, signal_c_value JSONB, signal_a_match BOOLEAN, signal_b_match BOOLEAN, signal_c_match BOOLEAN, signal_a_unobservable BOOLEAN NOT NULL DEFAULT false, signal_b_unobservable BOOLEAN NOT NULL DEFAULT false, signal_c_unobservable BOOLEAN NOT NULL DEFAULT false, registered BOOLEAN NOT NULL, registered_policy TEXT, action TEXT NOT NULL, decision_reason TEXT, signal_a_near_miss BOOLEAN GENERATED ALWAYS AS (signal_a_value IS NOT NULL AND signal_a_value >= 68 * 1024 AND signal_a_value <= 92 * 1024) STORED)$stmt$
    ]::text[]
)
ON CONFLICT (version) DO NOTHING;
