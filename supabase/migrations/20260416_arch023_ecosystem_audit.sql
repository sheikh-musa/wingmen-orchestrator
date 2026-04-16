-- ARCH-023: Self-maintaining ecosystem — ecosystem_audit_log table
-- Also adds 'informational' to challenge_status CHECK constraint (GATE 4 needs it).

-- ── ecosystem_audit_log ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ecosystem_audit_log (
  id            BIGSERIAL PRIMARY KEY,
  gate_name     TEXT NOT NULL,
  ran_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dry_run       BOOLEAN NOT NULL DEFAULT TRUE,
  rows_affected INT NOT NULL DEFAULT 0,
  actions_taken JSONB NOT NULL DEFAULT '[]',
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE ecosystem_audit_log ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename='ecosystem_audit_log' AND policyname='service role full access'
  ) THEN
    CREATE POLICY "service role full access" ON ecosystem_audit_log
      USING (true) WITH CHECK (true);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_eal_gate_name ON ecosystem_audit_log(gate_name);
CREATE INDEX IF NOT EXISTS idx_eal_ran_at ON ecosystem_audit_log(ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_eal_dry_run ON ecosystem_audit_log(dry_run);

-- ── Add 'informational' to challenge_status (GATE 4) ─────────────────────────
-- Drop existing constraint and recreate with expanded set
ALTER TABLE strategic_decisions
  DROP CONSTRAINT IF EXISTS strategic_decisions_challenge_status_check;

ALTER TABLE strategic_decisions
  ADD CONSTRAINT strategic_decisions_challenge_status_check
  CHECK (challenge_status IN (
    'unchallenged', 'challenge_window', 'challenged', 'accepted',
    'overridden', 'cai_review_requested', 'informational', 'implemented'
  ));
