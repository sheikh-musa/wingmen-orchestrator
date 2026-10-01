-- 087_idle_with_work_blocked_on.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Idle-with-work watchdog (Musa op#24477/#24479, orch-console #48417/#48432): a lane must
-- never sit idle while it owns ACTIONABLE work. "Blocked" is EXPLICIT, not inferred — a
-- work item counts as blocked ONLY if it carries blocked_on (an external dependency: a
-- person / client / another lane) together with blocked_since. No blocked_on => actionable
-- => the owning lane gets nudged. This adds that convention to the three owned-work tables
-- (held_commitments, coord_dispatch_queue, operator_backlog) as nullable columns + a CHECK
-- that the pair is set together and blocked_on is non-empty, plus the watchdog's per-lane
-- escalation state. All additive (nullable ADD COLUMN; new table) — existing rows are
-- blocked_on=NULL => actionable, which is the correct default.

ALTER TABLE held_commitments    ADD COLUMN IF NOT EXISTS blocked_on text,    ADD COLUMN IF NOT EXISTS blocked_since timestamptz;
ALTER TABLE coord_dispatch_queue ADD COLUMN IF NOT EXISTS blocked_on text,   ADD COLUMN IF NOT EXISTS blocked_since timestamptz;
ALTER TABLE operator_backlog    ADD COLUMN IF NOT EXISTS blocked_on text,    ADD COLUMN IF NOT EXISTS blocked_since timestamptz;

-- Re-run-safe: DROP IF EXISTS before ADD, so a retry can't half-fail on an already-present
-- constraint. Existing rows (blocked_on NULL) satisfy the CHECK, so no NOT VALID needed.
ALTER TABLE held_commitments    DROP CONSTRAINT IF EXISTS held_commitments_blocked_ck;
ALTER TABLE held_commitments    ADD  CONSTRAINT held_commitments_blocked_ck
  CHECK ((blocked_on IS NULL AND blocked_since IS NULL)
      OR (blocked_on IS NOT NULL AND btrim(blocked_on) <> '' AND blocked_since IS NOT NULL));
ALTER TABLE coord_dispatch_queue DROP CONSTRAINT IF EXISTS coord_dispatch_queue_blocked_ck;
ALTER TABLE coord_dispatch_queue ADD  CONSTRAINT coord_dispatch_queue_blocked_ck
  CHECK ((blocked_on IS NULL AND blocked_since IS NULL)
      OR (blocked_on IS NOT NULL AND btrim(blocked_on) <> '' AND blocked_since IS NOT NULL));
ALTER TABLE operator_backlog    DROP CONSTRAINT IF EXISTS operator_backlog_blocked_ck;
ALTER TABLE operator_backlog    ADD  CONSTRAINT operator_backlog_blocked_ck
  CHECK ((blocked_on IS NULL AND blocked_since IS NULL)
      OR (blocked_on IS NOT NULL AND btrim(blocked_on) <> '' AND blocked_since IS NOT NULL));

-- Per-lane escalation state for the ladder (nudge -> page -> page+console-mark). items_hash
-- lets the watchdog tell "still idle on the SAME items" from "new items"; consecutive_ticks
-- drives the ladder; it resets when the lane acts or the actionable set changes.
CREATE TABLE IF NOT EXISTS idle_with_work_state (
  lane             text PRIMARY KEY,
  items_hash       text NOT NULL,
  items            text,
  consecutive_ticks integer NOT NULL DEFAULT 0,
  last_nudge_at    timestamptz,
  updated_at       timestamptz NOT NULL DEFAULT now()
);

-- RLS on the new table (orch-console #48443): a public table without RLS is reachable via
-- the anon/authenticated API key. service_role-only (the console does not read this table,
-- so no console_readonly policy); REVOKE the default anon/authenticated grants. Re-run-safe.
-- assert: no_table_privilege anon public.idle_with_work_state SELECT
-- assert: no_table_privilege authenticated public.idle_with_work_state SELECT
-- assert: no_table_privilege authenticated public.idle_with_work_state INSERT
-- assert: no_table_privilege authenticated public.idle_with_work_state UPDATE
-- assert: no_table_privilege authenticated public.idle_with_work_state DELETE
ALTER TABLE idle_with_work_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS idle_with_work_state_service_only ON idle_with_work_state;
CREATE POLICY idle_with_work_state_service_only ON idle_with_work_state
  FOR ALL TO service_role USING (true) WITH CHECK (true);
REVOKE ALL ON idle_with_work_state FROM anon, authenticated;
