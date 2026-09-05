-- 022_opportunities_ledger.sql — Head of Revenue Phase 1 (LEDGER + DIGEST).
-- The revenue ledger: one row per live money thread, the durable source of
-- truth for the pre-sale revenue pipeline. Spec: reports/head-of-revenue-spec.md
-- (§9 Phase 1 — "LEDGER + DIGEST, zero risk"). This ships the STATE half only;
-- there is NO loop, NO enforcement, NO outbound comms — a read-only reader
-- (nervous_system/revenue_pipeline.py) renders a weekly digest over these rows.
--
-- Additive + idempotent (CREATE TABLE IF NOT EXISTS; seed is ON CONFLICT DO
-- NOTHING keyed on `slug`). Apply via scripts/apply_migration.py 022
-- --silo tscuymavysscrvoberrr (historical applier: apply_opportunities_ledger.py,
-- deleted 2026-09-05 PR #90; decision-962: never `supabase db push` against prod).
-- Nullable-friendly: only slug + name + stage are required.

CREATE TABLE IF NOT EXISTS opportunities (
    id                  bigserial PRIMARY KEY,
    slug                text NOT NULL UNIQUE,              -- stable natural key (idempotent seed)
    name                text NOT NULL,                    -- the thread / opportunity name
    stage               text NOT NULL DEFAULT 'lead'
        CHECK (stage IN (
            'lead','qualified','scoped','priced','proposed','closing',
            'won','paid','delivered','channel','parked','lost')),
    value_model         text                              -- outcome-billed | saas | retainer | rev-share | grant | partner
        CHECK (value_model IS NULL OR value_model IN (
            'outcome-billed','saas','retainer','rev-share','grant','partner')),
    est_value           numeric(12,2),                    -- estimated deal value (nullable)
    currency            text NOT NULL DEFAULT 'SGD',
    next_action         text,
    next_action_owner   text,                             -- who drives the next step
    due                 date,                             -- hard deadline, if any
    owner               text,                             -- who holds the relationship (usually the operator)
    blocker             text,                             -- shortest-path obstacle / first-dollar blocker
    operator_is_blocker boolean NOT NULL DEFAULT false,   -- surfaced in the "what needs you" digest section
    gate_status         text NOT NULL DEFAULT 'none'      -- ADVISORY money/residency gate state (never enforced in Phase 1)
        CHECK (gate_status IN ('none','n/a','open','cleared')),
    gate_note           text,                             -- free-text detail on the gate (e.g. "cai — cross-border money")
    partner             text,                             -- channel/partner in the thread
    sort_priority       int,                              -- nearest-to-first-dollar hint (lower = sooner); nullable
    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Substrate discipline: service-role only, like finance_subscriptions.
ALTER TABLE opportunities ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON opportunities FROM anon, authenticated;

-- Keep updated_at honest on every mutation (the ledger must tell the truth —
-- a stale row is a lie in a revenue pipeline).
CREATE OR REPLACE FUNCTION opportunities_touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_opportunities_touch ON opportunities;
CREATE TRIGGER trg_opportunities_touch
    BEFORE UPDATE ON opportunities
    FOR EACH ROW EXECUTE FUNCTION opportunities_touch_updated_at();

CREATE INDEX IF NOT EXISTS idx_opportunities_stage ON opportunities (stage);
CREATE INDEX IF NOT EXISTS idx_opportunities_sort  ON opportunities (sort_priority NULLS LAST);
