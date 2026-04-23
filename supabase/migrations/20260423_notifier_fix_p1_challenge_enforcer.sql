-- ORCHESTRATOR-NOTIFIER-FIX-001 Fix 2 — challenge_window timeout enforcer (DRY_RUN default)
--
-- Parents: ORCHESTRATOR-NOTIFIER-FIX-001 + ORCHESTRATOR-NOTIFIER-FIX-001-AMEND.
-- Rulings: CAI-RESP-074 (C1-C7 folded), CAI-RESP-075 (N1-N4 folded — Fix 4 separate).
--
-- Sections:
--   1. challenge_status CHECK expansion (+ accepted_by_timeout) [C6]
--   2. orchestrator_runtime_config table + initial enforcer_mode='dry_run' row
--   3. challenge_enforcer_dryrun_log staging table
--   4. NOT NULL challengeable_until trigger (INSERT OR UPDATE OF) [C4]
--   5. enforce_challenge_window_timeouts() plpgsql function [C1 + C5]
--   6. pg_cron job scheduling (every 5 min)
--
-- Rollback: each CREATE has an inline DROP comment. Single atomic transaction
-- via Supabase migration tooling; rollback is re-migrate with DROP statements.
--
-- Pre-flight verified 2026-04-23: pg_cron v1.6.4 installed, 2 existing jobs
-- (no name conflict expected with 'notifier-fix-enforcer').

-- ============================================================
-- SECTION 1: challenge_status CHECK expansion
-- ============================================================

-- Reverse: ALTER TABLE strategic_decisions DROP CONSTRAINT strategic_decisions_challenge_status_check;
--          Original CHECK (for rollback):
--            CHECK (challenge_status IN ('unchallenged', 'challenge_window', 'challenged',
--              'accepted', 'overridden', 'cai_review_requested', 'informational',
--              'implemented', 'superseded'))
-- Adds 'accepted_by_timeout' to the enumeration. Per CAI-RESP-074 C6: distinct
-- from 'accepted' to preserve epistemic distinction between silent-consent and
-- explicit agreement.
ALTER TABLE strategic_decisions
  DROP CONSTRAINT IF EXISTS strategic_decisions_challenge_status_check;

ALTER TABLE strategic_decisions
  ADD CONSTRAINT strategic_decisions_challenge_status_check
  CHECK (challenge_status IN (
    'unchallenged',
    'challenge_window',
    'challenged',
    'accepted',
    'accepted_by_timeout',
    'overridden',
    'cai_review_requested',
    'informational',
    'implemented',
    'superseded'
  ));

-- ============================================================
-- SECTION 2: orchestrator_runtime_config + dry_run flag
-- ============================================================

-- Reverse: DROP TABLE orchestrator_runtime_config;
-- Flag table for toggleable enforcer behavior. Phase 1 ships 'dry_run' default.
-- Musa UPDATEs to 'write_mode' after manual backlog review cycle per CAI-RESP-074 C1.
CREATE TABLE orchestrator_runtime_config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- CHECK constraint catches typos; extend when new flags added.
  CONSTRAINT orchestrator_runtime_config_value_check
  CHECK (
    (key = 'challenge_enforcer_mode' AND value IN ('dry_run', 'write_mode'))
    OR key != 'challenge_enforcer_mode'
  )
);

INSERT INTO orchestrator_runtime_config (key, value)
VALUES ('challenge_enforcer_mode', 'dry_run');

-- ============================================================
-- SECTION 3: challenge_enforcer_dryrun_log staging table
-- ============================================================

-- Reverse: DROP TABLE challenge_enforcer_dryrun_log;
-- Staging table for DRY_RUN enforcer proposals. Unique on decision_ref so
-- re-runs don't accumulate duplicate rows. processed=true after Musa reviews
-- and applies an outcome.
CREATE TABLE challenge_enforcer_dryrun_log (
  id                       BIGSERIAL PRIMARY KEY,
  decision_ref             TEXT NOT NULL UNIQUE,
  current_challenge_status TEXT NOT NULL,
  challengeable_until      TIMESTAMPTZ,
  proposed_new_status      TEXT NOT NULL DEFAULT 'accepted_by_timeout',
  logged_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed                BOOLEAN NOT NULL DEFAULT false,
  review_outcome           TEXT,  -- nullable: 'timeout_accepted' | 'reopened_as_challenge' | 'manual'
  review_notes             TEXT   -- nullable: free-form reviewer notes
);

CREATE INDEX idx_dryrun_log_unprocessed ON challenge_enforcer_dryrun_log (logged_at)
  WHERE processed = false;

-- ============================================================
-- SECTION 4: NOT NULL challengeable_until trigger
-- ============================================================

-- Reverse: DROP TRIGGER IF EXISTS trg_challenge_window_requires_expiry ON strategic_decisions;
--          DROP FUNCTION IF EXISTS enforce_challenge_window_requires_expiry();
--
-- CAI-RESP-074 C4: enforce NOT NULL challengeable_until whenever a row is in
-- challenge_window state. Fires on INSERT AND UPDATE OF challenge_status,
-- challengeable_until — covers both "new row with NULL expiry" and "UPDATE
-- flipping an existing row to challenge_window with NULL expiry" paths.

CREATE OR REPLACE FUNCTION enforce_challenge_window_requires_expiry()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.challenge_status = 'challenge_window' AND NEW.challengeable_until IS NULL THEN
    RAISE EXCEPTION 'strategic_decisions.challenge_status=challenge_window requires challengeable_until IS NOT NULL (decision_ref=%)',
      NEW.decision_ref
      USING HINT = 'Set challengeable_until to a future timestamp when filing a challenge_window decision.';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_challenge_window_requires_expiry
  BEFORE INSERT OR UPDATE OF challenge_status, challengeable_until ON strategic_decisions
  FOR EACH ROW
  EXECUTE FUNCTION enforce_challenge_window_requires_expiry();

-- ============================================================
-- SECTION 5: enforce_challenge_window_timeouts function
-- ============================================================

-- Reverse: DROP FUNCTION IF EXISTS enforce_challenge_window_timeouts();
--
-- CAI-RESP-074 C1: ships with DRY_RUN default. Reads orchestrator_runtime_config
-- flag 'challenge_enforcer_mode'. In dry_run: logs proposals to dryrun_log.
-- In write_mode: flips strategic_decisions.challenge_status to accepted_by_timeout.
-- Race guard: skips rows decided_at within last 1 hour (not-yet-notified rows).
-- Returns a table of (decision_ref, action) for each row processed.

CREATE OR REPLACE FUNCTION enforce_challenge_window_timeouts()
RETURNS TABLE(decision_ref TEXT, action TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
-- The RETURNS TABLE declares plpgsql OUT variable `decision_ref` which shadows
-- the table column in INSERT ... ON CONFLICT (decision_ref). Directive
-- use_column tells plpgsql to prefer column references when names clash;
-- explicit `rec.decision_ref` still refers to the loop record.
DECLARE
  mode TEXT;
  rec RECORD;
BEGIN
  SELECT value INTO mode
    FROM orchestrator_runtime_config
   WHERE key = 'challenge_enforcer_mode';
  IF mode IS NULL THEN
    mode := 'dry_run';
  END IF;

  FOR rec IN
    SELECT sd.decision_ref, sd.challenge_status, sd.challengeable_until
      FROM strategic_decisions sd
     WHERE sd.challenge_status = 'challenge_window'
       AND sd.challengeable_until IS NOT NULL
       AND sd.challengeable_until < now()
       AND sd.decided_at < now() - interval '1 hour'
  LOOP
    IF mode = 'dry_run' THEN
      INSERT INTO challenge_enforcer_dryrun_log
        (decision_ref, current_challenge_status, challengeable_until, proposed_new_status)
      VALUES
        (rec.decision_ref, rec.challenge_status, rec.challengeable_until, 'accepted_by_timeout')
      ON CONFLICT (decision_ref) DO NOTHING;
      RETURN QUERY SELECT rec.decision_ref, 'logged'::TEXT;
    ELSE
      -- Race guard: a concurrent txn may have flipped the row from
      -- challenge_window to challenged between the SELECT snapshot above and
      -- this UPDATE. The AND challenge_status='challenge_window' predicate
      -- prevents silently clobbering the challenge. GET DIAGNOSTICS detects
      -- the zero-row-affected case so the caller sees 'skipped_raced'.
      UPDATE strategic_decisions
         SET challenge_status = 'accepted_by_timeout',
             updated_at = now()
       WHERE strategic_decisions.decision_ref = rec.decision_ref
         AND strategic_decisions.challenge_status = 'challenge_window';
      IF FOUND THEN
        RETURN QUERY SELECT rec.decision_ref, 'flipped'::TEXT;
      ELSE
        RETURN QUERY SELECT rec.decision_ref, 'skipped_raced'::TEXT;
      END IF;
    END IF;
  END LOOP;
END;
$$;

-- Lock down SECURITY DEFINER surface: only postgres (the pg_cron invoker) may
-- execute. Prevents anon/authenticated roles from triggering the enforcer via
-- direct SQL should any RLS policy expose the function.
REVOKE EXECUTE ON FUNCTION enforce_challenge_window_timeouts() FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION enforce_challenge_window_timeouts() TO postgres;

-- ============================================================
-- SECTION 6: pg_cron schedule
-- ============================================================

-- Reverse: SELECT cron.unschedule('notifier-fix-enforcer');
-- Runs every 5 minutes. Calls enforcer function which self-branches on config flag.
SELECT cron.schedule(
  'notifier-fix-enforcer',
  '*/5 * * * *',
  $cron$SELECT enforce_challenge_window_timeouts();$cron$
);
