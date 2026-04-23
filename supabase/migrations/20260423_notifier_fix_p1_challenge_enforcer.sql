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

-- (Task B2 will fill this in)

-- ============================================================
-- SECTION 2: orchestrator_runtime_config + dry_run flag
-- ============================================================

-- (Task B2 will fill this in)

-- ============================================================
-- SECTION 3: challenge_enforcer_dryrun_log staging table
-- ============================================================

-- (Task B3 will fill this in)

-- ============================================================
-- SECTION 4: NOT NULL challengeable_until trigger
-- ============================================================

-- (Task B4 will fill this in)

-- ============================================================
-- SECTION 5: enforce_challenge_window_timeouts function
-- ============================================================

-- (Task B5 will fill this in)

-- ============================================================
-- SECTION 6: pg_cron schedule
-- ============================================================

-- (Task B5 will fill this in)
