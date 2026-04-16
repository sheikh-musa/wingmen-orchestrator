-- TASK-032: Normalize category + add CHECK constraint + parent_ref index
--
-- Columns already exist from a prior migration (no CHECK, no parent_ref index).
-- This migration normalizes existing values to the canonical set, backfills NULLs
-- by ref prefix pattern, adds the CHECK constraint, and adds the parent_ref index.

-- ── Step 1: Normalize non-standard category values ───────────────────────────

UPDATE strategic_decisions SET category = 'operations'
WHERE category IN ('status_update', 'update', 'orchestrator');

UPDATE strategic_decisions SET category = 'product'
WHERE category IN ('feature', 'qurban_gap', 'product_proposal');

UPDATE strategic_decisions SET category = 'qa'
WHERE category IN ('phase_0_hardening', 'pipeline_bug', 'quality');

UPDATE strategic_decisions SET category = 'governance'
WHERE category IN ('migration_review');

UPDATE strategic_decisions SET category = 'infra'
WHERE category IN ('security_remediation');

-- ── Step 2: Backfill NULL categories by ref prefix ───────────────────────────

-- CC-UPDATE-* and CAI-RESP-* are operational + governance communication
UPDATE strategic_decisions SET category = 'operations'
WHERE category IS NULL AND decision_ref ~ '^CC-UPDATE-';

UPDATE strategic_decisions SET category = 'governance'
WHERE category IS NULL AND decision_ref ~ '^CAI-RESP-';

-- ARCH-* are architecture/infrastructure decisions
UPDATE strategic_decisions SET category = 'infra'
WHERE category IS NULL AND decision_ref ~ '^ARCH-';

-- TASK-* and IMPL-* are product implementation tasks
UPDATE strategic_decisions SET category = 'product'
WHERE category IS NULL AND decision_ref ~ '^(TASK|IMPL|PROD|FEE)-';

-- Any remaining NULLs → 'governance' (safest fallback)
UPDATE strategic_decisions SET category = 'governance'
WHERE category IS NULL;

-- ── Step 3: Add CHECK constraint ─────────────────────────────────────────────

ALTER TABLE strategic_decisions
ADD CONSTRAINT strategic_decisions_category_check
CHECK (category IN ('governance', 'performance', 'qa', 'product', 'infra', 'pricing', 'islamic', 'operations'));

-- Step 4: parent_ref index already created in a prior migration (idx_strategic_decisions_parent)
-- Nothing to do here.
