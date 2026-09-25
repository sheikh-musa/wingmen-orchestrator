-- 068_hub_self_recovery.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Hub self-supervision for the WEDGED-but-ALIVE gap (op#42896 follow-on).
-- PR #145 made hub_reach.py's gzb remedy honestly "escalate to a human" -- there is
-- no automated recovery left for a live-but-stuck hub composer. CAI-RESP-1439
-- approved a gzb-LOCAL systemd-timer answer with conditions; the hub consented
-- (bus #43157); orch-console authorized building it OBSERVE-FIRST (bus #43161).
--
-- Two brand-new tables, purely additive, nothing else touched:
--
--   hub_self_recovery_settings -- ONE row, the DB half of the kill switch
--     (orch-console's amendment: the switch is (gzb flag FILE present) OR
--     (this row's enabled=false) -- EITHER can disable; BOTH must allow for the
--     recovery script to ever act). Seeded enabled=true (default-on, matching
--     this fleet's "configured-and-monitored, not silently-absent" convention).
--
--   hub_self_recovery_log -- append-only detection/action audit trail. Every
--     GENUINE wedge detection (never a healthy-idle tick -- those are not
--     logged, to avoid a 2-minute-cadence timer flooding this table on a
--     healthy hub) gets one row: mode ('observe'|'act'), action
--     ('would-nudge'|'nudged'|'rate-limited'|'ceiling-reached'),
--     triggering_row_id (the SPECIFIC unread operator_messages/agent_messages
--     row id that made this a genuine detection -- orch-console's "pass the
--     triggering row id for a lifetime ceiling" amendment: repeated detections
--     on the SAME underlying stuck item are capped per-row, not just per-hour).
--     This table is also the durable evidence orch-console reads to judge the
--     observe-first graduation criterion (>=72h AND >=3 genuine detections,
--     whichever is longer, per bus #43161) -- never delete rows from it as a
--     cleanup; it is the record that criterion is judged against.
--
-- REVERT: DROP TABLE IF EXISTS public.hub_self_recovery_log;
--         DROP TABLE IF EXISTS public.hub_self_recovery_settings;
-- Full revert -- neither table is read by anything else in this migration set,
-- so dropping both is a true zero-residue rollback (unlike migration 067's
-- in-place backfill, this migration creates nothing pre-existing code depends on).

CREATE TABLE IF NOT EXISTS public.hub_self_recovery_settings (
    id          boolean PRIMARY KEY DEFAULT true CHECK (id),  -- singleton-row pattern: exactly one row, ever
    enabled     boolean NOT NULL DEFAULT true,
    updated_by  text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.hub_self_recovery_settings IS
    'Singleton settings row (DB half of the hub self-recovery kill switch, alongside a flag FILE on gzb -- orch-console amendment, bus #43161). enabled=false disables the recovery script''s ACT step regardless of the file; the file being present ALSO disables it regardless of this row -- either can kill it, both must allow it to act.';

INSERT INTO public.hub_self_recovery_settings (id, enabled, updated_by)
    VALUES (true, true, 'migration-068-seed')
    ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.hub_self_recovery_log (
    id                 bigserial PRIMARY KEY,
    detected_at        timestamptz NOT NULL DEFAULT now(),
    mode               text NOT NULL CHECK (mode IN ('observe', 'act')),
    action             text NOT NULL CHECK (action IN ('would-nudge', 'nudged', 'rate-limited', 'ceiling-reached')),
    pending_kind       text CHECK (pending_kind IN ('operator_log', 'bus_p1')),
    triggering_row_id  bigint,
    detail             text
);

COMMENT ON TABLE public.hub_self_recovery_log IS
    'Append-only audit trail for the gzb-local hub wedge-recovery timer (op#42896, CAI-RESP-1439, bus #43161). One row per GENUINE wedge detection only (never per healthy-idle tick). This is the evidence orch-console reads to judge the observe-first graduation criterion (>=72h AND >=3 genuine detections) -- never truncate/delete as routine cleanup.';

CREATE INDEX IF NOT EXISTS hub_self_recovery_log_triggering_row_idx
    ON public.hub_self_recovery_log (pending_kind, triggering_row_id);

CREATE INDEX IF NOT EXISTS hub_self_recovery_log_detected_at_idx
    ON public.hub_self_recovery_log (detected_at);

-- This migration only creates tables/indexes and seeds one row -- it touches no
-- privilege or function, so apply_migration.py's CAI-RESP-1397 post-apply
-- assertion requirement does not apply here. Correctness is verified via
-- --dry-run plus the accompanying test suite (tests/test_hub_self_recovery.py)
-- against the real table shapes before this is ever sent to orch-console's gate.
