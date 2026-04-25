-- BUG-033: restore NOT NULL on agent_status.base_agent_id after auto_agent_id.py fix.
--
-- Background: Batch 1 (20260424_batch1_structural_integrity.sql) added
-- agent_status.base_agent_id NOT NULL. App-layer coverage in the shipped
-- cc94b3e commit populated the UPSERT-existing branch of
-- allocate_sub_tag_and_register, but the INSERT-new-row branch did not.
-- First spawn of cc-orchestrator hit the gap (NotNullViolation), so
-- temporary migration bug033_temp_relax_base_agent_id_not_null dropped
-- the constraint to unblock bootstrapping. That migration is a documented
-- degradation window per BUG-033 Scope Amendment (2026-04-24 02:42 SGT).
--
-- This migration closes the window:
--   Section 1: defence-in-depth backfill for any NULL rows using the
--              existing regexp pattern (matches agent_status_base_agent_id_prefix_chk).
--              Wrapped in DISABLE/ENABLE TRIGGER per Batch 1 Section 2 precedent
--              — trg_agent_status_identity enforces GUC-per-row which breaks
--              multi-row UPDATE.
--   Section 2: explicit assertion gate before SET NOT NULL (CAI-RESP-081
--              CHECK 2 pattern — clearer failure than PG's generic
--              "column contains null values" on SET NOT NULL directly).
--   Section 3: SET NOT NULL restoration. Defence-in-depth CHECK + FK were
--              preserved during the degradation window; only NOT NULL was
--              relaxed. This restores the third pillar.
--
-- Paired Python fix: scripts/lib/auto_agent_id.py:allocate_sub_tag_and_register
-- INSERT column list now includes base_agent_id + ON CONFLICT branch uses
-- EXCLUDED.base_agent_id (idempotent refresh).
--
-- Regression tests:
--   tests/test_auto_agent_id.py::test_allocate_sub_tag_registers_fresh_base_agent_without_existing_rows (AC-BUG033-2)
--   tests/test_auto_agent_id.py::test_allocate_respects_base_agent_id_prefix_check_constraint (AC-BUG033-3)
--
-- Parent decision: BUG-033. AC covered: AC-BUG033-6.

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1: defence-in-depth backfill for any NULL base_agent_id rows
-- ─────────────────────────────────────────────────────────────────────────────
-- Source of truth: agent_id + regexp_replace pattern (matches the CHECK).
-- Reverse: not applicable — backfill is idempotent data repair.

ALTER TABLE agent_status DISABLE TRIGGER trg_agent_status_identity;

UPDATE agent_status
   SET base_agent_id = regexp_replace(agent_id, '-[0-9]+$', '')
 WHERE base_agent_id IS NULL;

ALTER TABLE agent_status ENABLE TRIGGER trg_agent_status_identity;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2: explicit assertion before SET NOT NULL
-- ─────────────────────────────────────────────────────────────────────────────
-- Mirrors Batch 1 CAI-RESP-081 CHECK 2 pattern.

DO $$
BEGIN
  IF (SELECT count(*) FROM agent_status WHERE base_agent_id IS NULL) > 0 THEN
    RAISE EXCEPTION 'BUG-033 restoration: % rows still have NULL base_agent_id after Section 1 backfill. Migration rolling back.',
      (SELECT count(*) FROM agent_status WHERE base_agent_id IS NULL);
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3: SET NOT NULL restoration
-- ─────────────────────────────────────────────────────────────────────────────
-- Closes BUG-033 degradation window. auto_agent_id.py:316 INSERT fix ships
-- in the same PR so future fresh-family allocations satisfy this constraint.
-- Reverse: ALTER TABLE agent_status ALTER COLUMN base_agent_id DROP NOT NULL;

ALTER TABLE agent_status
  ALTER COLUMN base_agent_id SET NOT NULL;

COMMIT;
