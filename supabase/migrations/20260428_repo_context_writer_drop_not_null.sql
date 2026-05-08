-- repo_context_writer (CAI-RESP-093) shipped with a schema/spec mismatch:
-- cai's spec said semantic + mechanical fields "stay NULL on missing/malformed
-- STATUS.md ... do not overwrite seeded values with garbage". Live schema had
-- NOT NULL on current_phase, architecture_summary, recent_changes — writer's
-- upsert with NULL on those fields rejected with `null value in column violates
-- not-null constraint` on every sweep (8/8 active repos failed).
--
-- Fix: drop NOT NULL on the three columns the writer can leave blank when
-- source is missing. Aligns schema with spec; writer code requires no change
-- on these columns. Existing seeded values are preserved (DROP NOT NULL is
-- a relaxation, not a content change).
--
-- Idempotent: ALTER COLUMN ... DROP NOT NULL is safe to re-run (PostgreSQL
-- treats it as a no-op if already nullable).
--
-- Parent decisions: CAI-RESP-093 (writer spec), implicitly CAI-PROCESS-CODEGREP-001
-- (code-ground before shipping — caught here post-ship instead of pre-ship; my fault).
--
-- NOTE: This migration pre-dates scripts/check_additive_migration.py (CAI-RESP-102 safety net).
-- The DROP NOT NULL operations below are schema relaxations (existing data preserved, only the
-- contract for new inserts loosens). They were approved at original ship time. Each DROP is
-- explicitly allowlisted with a reason; do not strip the linter-allow comments without consultation.

BEGIN;

-- linter-allow: schema relaxation (NOT NULL → NULL), pre-existing pre-CAI-RESP-102 linter, approved at original ship time
ALTER TABLE repo_context ALTER COLUMN current_phase         DROP NOT NULL;
-- linter-allow: schema relaxation (NOT NULL → NULL), pre-existing pre-CAI-RESP-102 linter, approved at original ship time
ALTER TABLE repo_context ALTER COLUMN architecture_summary  DROP NOT NULL;
-- linter-allow: schema relaxation (NOT NULL → NULL), pre-existing pre-CAI-RESP-102 linter, approved at original ship time
ALTER TABLE repo_context ALTER COLUMN recent_changes        DROP NOT NULL;

-- Assertion gate: confirm post-ALTER state matches expectation.
DO $$
DECLARE
  bad_count INTEGER;
BEGIN
  SELECT count(*) INTO bad_count
    FROM information_schema.columns
   WHERE table_name = 'repo_context'
     AND column_name IN ('current_phase', 'architecture_summary', 'recent_changes')
     AND is_nullable = 'NO';
  IF bad_count > 0 THEN
    RAISE EXCEPTION 'CAI-RESP-093 schema-alignment: % columns still NOT NULL after ALTER', bad_count;
  END IF;
END $$;

COMMIT;
