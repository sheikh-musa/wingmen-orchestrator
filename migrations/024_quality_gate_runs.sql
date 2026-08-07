-- 024_quality_gate_runs.sql — Head of Quality, Phase 2b: the audit sink for the
-- shadow-mode quality gate (reports/head-of-quality-spec.md §8, Phase 1 CODIFY /
-- Phase 2 SHADOW). One row per gate evaluation of a diff SHA: the resolved change
-- class, the enforcement mode, whether the verdict WOULD block, whether it was
-- actually ENFORCED, and the full verdict JSON for the record.
--
-- ADDITIVE + INERT. This table only RECORDS what the evaluator already computes;
-- creating it changes NO existing behaviour and wires into NO ship path. Nothing
-- reads it to block. It is the observation ledger that lets thresholds prove out
-- against real ship traffic before anything is armed (spec §8: "ship shadow-first").
-- It is also the SHA-cache substrate (spec §6 lever 4): a verdict is scoped to a
-- diff SHA, so a re-run on an unchanged SHA can return the recorded result.
--
-- Mirrors the RLS lockdown shape of migrations/023_fleet_health_lease.sql.
-- Apply via a direct psycopg apply script (decision-962: never `supabase db push`).
-- NOT APPLIED HERE — this is a migration FILE only; applying it is a later step.

CREATE TABLE IF NOT EXISTS quality_gate_runs (
    id           bigserial PRIMARY KEY,
    sha          text,                       -- the diff/HEAD SHA that was gated
    change_class text,                       -- resolved change class (strictest on ambiguity)
    mode         text,                       -- off | shadow | block (enforcement mode)
    would_block  boolean,                    -- did the deterministic floor / mandatory reviews fail?
    enforced     boolean,                    -- was it actually enforced (block mode + would_block)?
    verdict      jsonb,                       -- the full GateVerdict.to_dict() for the record
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- SHA-cache lookup path (spec §6 lever 4): "return the cached green for an
-- unchanged SHA". Newest-run-per-SHA is the common read, so index (sha, created_at).
CREATE INDEX IF NOT EXISTS quality_gate_runs_sha_idx
    ON quality_gate_runs (sha, created_at DESC);

ALTER TABLE quality_gate_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON quality_gate_runs FROM anon, authenticated;
