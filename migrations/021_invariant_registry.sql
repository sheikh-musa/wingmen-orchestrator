-- 021_invariant_registry.sql
-- Invariant registry (CAI-RESP-420, task #51). cai STEWARDS the rows; cc-infra
-- builds the table. The substrate-resident, QUERYABLE enumeration of fleet
-- invariants — not a doc that rots.
--
-- HARD RULE (cai, the anti-takalluf guardrail): an invariant is 'COVERED' only
-- when a RUNNING executable gate asserts it and stamps last_asserted_at each pass.
-- A COVERED row whose last_asserted_at goes stale = a gate that stopped running =
-- surfaced debt, never silent false comfort. 'MANUAL' = human-tracked debt;
-- 'pending' = gate planned/in-progress, not yet asserting. gate flips a row to
-- COVERED via scripts/gates/registry.py mark_asserted().
--
-- Service-role-only substrate posture (migration 013/014 pattern). Apply via
-- direct-psycopg (NEVER db push — CLAUDE.md / decision 962). Seeding is done
-- PARAMETERIZED in scripts/apply_021_invariant_registry.py (statement texts carry
-- apostrophes), not as raw SQL here.

CREATE TABLE IF NOT EXISTS invariant_registry (
  invariant_ref     TEXT PRIMARY KEY,                 -- e.g. 'MIGRATION-1'
  domain            TEXT NOT NULL,                    -- money|schema|residency|deploy|authority|tokens|governance
  statement         TEXT NOT NULL,                    -- the invariant, in words
  gate_ref          TEXT,                             -- the executable gate that asserts it (script/CI/SQL)
  gate_status       TEXT NOT NULL DEFAULT 'MANUAL'
                      CHECK (gate_status IN ('COVERED','MANUAL','pending')),
  severity          TEXT,                             -- critical|high|medium
  origin_incident   TEXT,                             -- the near-miss/decision that birthed it
  last_asserted_at  TIMESTAMPTZ,                      -- stamped by a green gate run; staleness = dead gate
  stewarded_by      TEXT NOT NULL DEFAULT 'cai',
  seeded_by         TEXT,                             -- 'cc-infra-seeded' = review-and-adjust, then steward
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE invariant_registry ENABLE ROW LEVEL SECURITY;
-- rls-policy-exempt: invariant_registry select/insert/update/delete (service-role-only substrate table)
DROP POLICY IF EXISTS deny_all_invariant_registry ON invariant_registry;
CREATE POLICY deny_all_invariant_registry ON invariant_registry FOR ALL TO public USING (false);
REVOKE ALL ON invariant_registry FROM PUBLIC, anon, authenticated;
