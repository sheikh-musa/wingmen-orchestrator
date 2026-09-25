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
-- Two brand-new tables, purely additive:
--
--   hub_self_recovery_settings -- ONE row, the DB half of the kill switch
--     (orch-console's amendment: the switch is (gzb flag FILE present) OR
--     (this row's enabled=false) -- EITHER can disable; BOTH must allow for the
--     recovery script to ever act). Seeded enabled=true (default-on, matching
--     this fleet's "configured-and-monitored, not silently-absent" convention).
--     Also carries a per-tick liveness stamp (last_tick_at/last_tick_saw_session/
--     last_tick_reason, audit #43169 finding 6): without it, a tick that never
--     even reaches the decision core (e.g. the systemd unit's User= doesn't own
--     the hub's tmux session) leaves zero trace, indistinguishable from a
--     genuinely healthy observation window.
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
--     cleanup; it is the record that criterion is judged against. Genuinely
--     append-only: a BEFORE UPDATE OR DELETE trigger refuses both operations
--     unconditionally, for every role including the table owner (a plain
--     REVOKE UPDATE/DELETE does NOT bind the owner, and the applier's writer
--     role owns every table it creates -- verified empirically; a trigger is
--     the only mechanism that actually holds against the owner too).
--
-- PRIVILEGE (audit #43169 finding 1 -- disputes this migration's prior claim of
-- "touches no privilege"): this project's pg_default_acl on schema public
-- auto-grants anon=rxtm (SELECT/REFERENCES/TRIGGER/MAINTAIN) and
-- authenticated=arwdxtm (INSERT/SELECT/UPDATE/DELETE/REFERENCES/TRIGGER/MAINTAIN)
-- on every NEW table (same empirical finding as migration 065's header). Both
-- new tables here get RLS ENABLE + a deny-all policy (service-role-only reader/
-- writer, same shape as 065's vault tables -- nothing here is ever
-- console/PostgREST-readable), PLUS an explicit REVOKE ALL from anon/
-- authenticated on both tables and the log table's bigserial sequence (belt-
-- and-braces on top of RLS, closing the exact cosmetic gap 065's header flagged
-- as a future applier-tool iteration -- this migration is that iteration: see
-- apply_migration.py's new `no_table_privilege` assert kind). Post-apply
-- assertions below verify the REVOKEs actually stuck, not just that they ran.
--
-- REVERT: DROP TRIGGER IF EXISTS hub_self_recovery_log_append_only ON public.hub_self_recovery_log;
--         DROP FUNCTION IF EXISTS public._hub_self_recovery_log_append_only();
--         DROP TABLE IF EXISTS public.hub_self_recovery_log;
--         DROP TABLE IF EXISTS public.hub_self_recovery_settings;
-- Full, clean revert. `DROP TABLE` in Postgres already drops everything the
-- table itself OWNS -- its bigserial-backed sequence, its RLS policies, and any
-- trigger DEFINED ON it -- with no separate statement needed for any of those;
-- the two explicit lines above are only for the trigger FUNCTION (a schema-level
-- object, not owned by the table, so DROP TABLE alone would leave it behind) and
-- the trigger itself is named explicitly here purely for clarity/idempotency of
-- this revert recipe, not because DROP TABLE needs the help. Neither table is
-- read by anything else in this migration set, so this is a true zero-residue
-- rollback.
--
-- assert: no_table_privilege anon public.hub_self_recovery_settings SELECT
-- assert: no_table_privilege anon public.hub_self_recovery_log SELECT
-- assert: no_table_privilege authenticated public.hub_self_recovery_settings SELECT
-- assert: no_table_privilege authenticated public.hub_self_recovery_settings INSERT
-- assert: no_table_privilege authenticated public.hub_self_recovery_settings UPDATE
-- assert: no_table_privilege authenticated public.hub_self_recovery_settings DELETE
-- assert: no_table_privilege authenticated public.hub_self_recovery_log SELECT
-- assert: no_table_privilege authenticated public.hub_self_recovery_log INSERT
-- assert: no_table_privilege authenticated public.hub_self_recovery_log UPDATE
-- assert: no_table_privilege authenticated public.hub_self_recovery_log DELETE

CREATE TABLE IF NOT EXISTS public.hub_self_recovery_settings (
    id                     boolean PRIMARY KEY DEFAULT true CHECK (id),  -- singleton-row pattern: exactly one row, ever
    enabled                boolean NOT NULL DEFAULT true,
    updated_by             text,
    updated_at             timestamptz NOT NULL DEFAULT now(),
    last_tick_at           timestamptz,
    last_tick_saw_session  boolean,
    last_tick_reason       text
);

COMMENT ON TABLE public.hub_self_recovery_settings IS
    'Singleton settings row (DB half of the hub self-recovery kill switch, alongside a flag FILE on gzb -- orch-console amendment, bus #43161). enabled=false disables the recovery script''s ACT step regardless of the file; the file being present ALSO disables it regardless of this row -- either can kill it, both must allow it to act. last_tick_* is a per-tick liveness stamp (audit #43169 finding 6): a stale last_tick_at is the signal that the recovery timer has stopped reaching this far, not that the hub is healthy.';

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
    'Append-only audit trail for the gzb-local hub wedge-recovery timer (op#42896, CAI-RESP-1439, bus #43161). One row per GENUINE wedge detection only (never per healthy-idle tick). This is the evidence orch-console reads to judge the observe-first graduation criterion (>=72h AND >=3 genuine detections) -- never truncate/delete as routine cleanup; a BEFORE UPDATE OR DELETE trigger enforces this against every role, including the table owner.';

CREATE INDEX IF NOT EXISTS hub_self_recovery_log_triggering_row_idx
    ON public.hub_self_recovery_log (pending_kind, triggering_row_id);

CREATE INDEX IF NOT EXISTS hub_self_recovery_log_detected_at_idx
    ON public.hub_self_recovery_log (detected_at);

-- ---------------------------------------------------------------- append-only trigger (finding 1)
-- A plain REVOKE UPDATE/DELETE does not bind the table OWNER, and the applier's
-- writer role owns every table it creates -- verified empirically against this
-- silo. A trigger is the only mechanism that holds against the owner too.
CREATE OR REPLACE FUNCTION public._hub_self_recovery_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'hub_self_recovery_log is append-only -- % is not permitted', TG_OP;
END;
$$;

CREATE TRIGGER hub_self_recovery_log_append_only
    BEFORE UPDATE OR DELETE ON public.hub_self_recovery_log
    FOR EACH ROW EXECUTE FUNCTION public._hub_self_recovery_log_append_only();

-- ---------------------------------------------------------------- RLS: deny-all, no exceptions
-- Service-role-only, same shape as migration 065's vault tables: the recovery
-- script and any reconciliation tooling connect via the service DSN (bypasses
-- RLS); nothing here is ever console/PostgREST-readable.
ALTER TABLE public.hub_self_recovery_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hub_self_recovery_log      ENABLE ROW LEVEL SECURITY;

CREATE POLICY deny_all_hub_self_recovery_settings
    ON public.hub_self_recovery_settings FOR ALL TO public USING (false);
CREATE POLICY deny_all_hub_self_recovery_log
    ON public.hub_self_recovery_log FOR ALL TO public USING (false);

-- ---------------------------------------------------------------- explicit REVOKE (finding 1)
-- Belt-and-braces on top of RLS: pg_default_acl grants anon/authenticated
-- standing table privileges on every new table regardless of RLS (RLS blocks
-- ROW access, not the grant itself) -- take them back explicitly, table AND
-- the log's bigserial sequence, and assert (header above) that they actually
-- came off rather than trusting the REVOKE ran silently.
REVOKE ALL ON public.hub_self_recovery_settings FROM anon, authenticated;
REVOKE ALL ON public.hub_self_recovery_log      FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.hub_self_recovery_log_id_seq FROM anon, authenticated;
