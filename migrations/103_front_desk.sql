-- 103_front_desk.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Front desk v1 (op#29128, bus #63359/#63381/#63383, orch-console gate fixes
-- bus #63427): a narrow whitelist-answer layer in front of Nazim's
-- nazim-console DMs, running as its own process
-- (nervous_system/front_desk_runner.py), same isolation pattern as
-- responder_runner.py — it reads operator_messages, never gates/blocks
-- ingest.py's own path.
--
-- front_desk_acked_at is DELIBERATELY SEPARATE from handled_at (CAI-RESP-277's
-- cursor): touching it never marks a row handled — Nazim still owns and must
-- answer every row himself (operator_log.unprocessed() keeps surfacing it,
-- since that only filters on handled_at). This holds for BOTH the plain-ack
-- and the whitelist-answer path (#63427 item 7 — an auto-answer is a
-- courtesy, not a resolution): front_desk_runner.py never sets handled_at at
-- all, in either branch.
--
-- front_desk_latency is a thin audit trail (not used for any gating), one row
-- per message front desk touched (either kind), for the "<10s" latency report.
--
-- All additive (new column + new table + index only). REVERT:
--   ALTER TABLE operator_messages DROP COLUMN front_desk_acked_at;
--   DROP TABLE front_desk_latency;
--
-- CAI-RESP-1397 #5: the REVOKE ALL below needs assertions or
-- apply_migration.py refuses outright (a REVOKE with no way to check it
-- stuck is unverifiable by construction) -- same shape as migration 068's
-- own REVOKE assertions.
-- assert: no_table_privilege anon public.front_desk_latency SELECT
-- assert: no_table_privilege anon public.front_desk_latency INSERT
-- assert: no_table_privilege anon public.front_desk_latency UPDATE
-- assert: no_table_privilege anon public.front_desk_latency DELETE
-- assert: no_table_privilege authenticated public.front_desk_latency SELECT
-- assert: no_table_privilege authenticated public.front_desk_latency INSERT
-- assert: no_table_privilege authenticated public.front_desk_latency UPDATE
-- assert: no_table_privilege authenticated public.front_desk_latency DELETE

BEGIN;

ALTER TABLE public.operator_messages
  ADD COLUMN IF NOT EXISTS front_desk_acked_at timestamptz;

CREATE TABLE IF NOT EXISTS public.front_desk_latency (
  id                 bigserial PRIMARY KEY,
  operator_message_id bigint      NOT NULL,
  kind               text        NOT NULL CHECK (kind IN ('ack', 'answer')),
  received_at        timestamptz NOT NULL,
  replied_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS front_desk_latency_replied_idx
  ON public.front_desk_latency (replied_at);

ALTER TABLE public.front_desk_latency ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS front_desk_latency_service_only ON public.front_desk_latency;
CREATE POLICY front_desk_latency_service_only ON public.front_desk_latency
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS front_desk_latency_console_ro ON public.front_desk_latency;
CREATE POLICY front_desk_latency_console_ro ON public.front_desk_latency
  FOR SELECT TO console_readonly USING (true);

REVOKE ALL ON public.front_desk_latency FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.front_desk_latency TO console_readonly;
GRANT ALL ON public.front_desk_latency TO service_role;

COMMIT;
