-- Batch 1 Structural Integrity Bundle
-- Ships 4 P1 fixes per CAI msg #620 consolidated briefing:
--   BUG-024 Phase 1B — agent_status.base_agent_id FK + backfill + prefix CHECK
--   BUG-024 Phase 1C = BUG-032 — strategic_decisions provenance + trigger
--   BUG-031 — is_test on strategic_decisions + enforcer test_mode parameter
--   BUG-029 Part A — is_test on agent_messages
--
-- Parent decisions: BUG-024, BUG-029, BUG-031, BUG-032.
-- Rulings: CAI-RESP-059 (Phase 1B clearance), CAI-RESP-072 (Phase 1 patterns),
--          CAI-RESP-077 (test-mutates-prod root cause), msg #620 (bundling),
--          CAI-RESP-080 (Open Q1/Q2/Q3 + Refinements 1/2).
--
-- Sections:
--   1. agent_status + base_agent_id FK column
--   2. agent_status backfill from agent_id pattern
--   3. agent_status base_agent_id NOT NULL + prefix CHECK
--   4. strategic_decisions provenance columns
--   5. populate_strategic_decisions_provenance trigger
--   6. is_test columns on strategic_decisions + agent_messages
--   7. enforce_challenge_window_timeouts REPLACE with test_mode parameter
--   8. boot_briefing view extension — unverified_decisions section
--
-- Pre-flight verified (see plan Task 1):
--   * identity_allowlist exists (Phase 1A shipped, zero-seed)
--   * agent_messages.sub_tag CHECK name = agent_messages_sub_tag_family_prefix_chk
--   * agents table has 7 rows (broadcast, cai, cc-cosem, cc-ihsanos, cc-scholar,
--     cc-web, musa) — FK target
--   * agent_status has 5 rows (cc-cosem-1, cc-ihsanos-1/2/3, cc-scholar-1) — backfill
--     predictable via regexp_replace

-- ============================================================
-- SECTION 1: agent_status + base_agent_id FK column (nullable initial)
-- ============================================================

-- (Task 3 will fill this in)

-- ============================================================
-- SECTION 2: agent_status backfill base_agent_id from agent_id pattern
-- ============================================================

-- (Task 3 will fill this in)

-- ============================================================
-- SECTION 3: agent_status base_agent_id NOT NULL + prefix CHECK
-- ============================================================

-- (Task 3 will fill this in)

-- ============================================================
-- SECTION 4: strategic_decisions provenance columns
-- ============================================================

-- (Task 4 will fill this in)

-- ============================================================
-- SECTION 5: populate_strategic_decisions_provenance trigger
-- ============================================================

-- (Task 5 will fill this in)

-- ============================================================
-- SECTION 6: is_test columns on strategic_decisions + agent_messages
-- ============================================================

-- (Task 6 will fill this in)

-- ============================================================
-- SECTION 7: enforce_challenge_window_timeouts REPLACE with test_mode parameter
-- ============================================================

-- (Task 7 will fill this in)

-- ============================================================
-- SECTION 8: boot_briefing view extension
-- ============================================================

-- (Task 8 will fill this in)
