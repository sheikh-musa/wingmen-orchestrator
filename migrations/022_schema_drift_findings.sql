-- 022_schema_drift_findings.sql
-- Cross-silo drift-detector findings store (CAI-RESP-420, task #50, Deliverable B).
-- Each run writes one row per detected divergence of a tenant silo from the
-- canonical reference (ceayj). 'expected' rows are allowlisted whole-table/module
-- presence differences (intentional module scoping, each with a reason);
-- everything else (column/index/policy/grant/SECDEF drift in a shared table) is
-- the 092 class and is NEVER expected. Service-role-only substrate posture.
-- NOT drift_audits (that is build-spec QA — a different concern). Apply via
-- direct-psycopg (never db push).

CREATE TABLE IF NOT EXISTS schema_drift_findings (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id       TEXT NOT NULL,                    -- one detector run (e.g. drift-20260712T...-daily)
  reason_run   TEXT,                             -- why the run fired: daily | on-apply | pre-live | manual
  detected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  silo         TEXT NOT NULL,                    -- silo alias diffed vs reference, e.g. goumlyne
  silo_ref     TEXT,                             -- project ref
  dimension    TEXT NOT NULL,                    -- tables|columns|indexes|policies|grants|functions
  kind         TEXT NOT NULL,                    -- table_missing|column_type_diff|grant_extra|policy_missing|fn_secdef_diff|...
  object       TEXT NOT NULL,                    -- table, table.column, policy/index/fn name
  severity     TEXT NOT NULL CHECK (severity IN ('CRITICAL','NOTABLE','INFO')),
  expected     BOOLEAN NOT NULL DEFAULT false,   -- allowlisted intentional presence divergence
  is_money     BOOLEAN NOT NULL DEFAULT false,
  detail       JSONB,
  reason       TEXT,                             -- allowlist reason when expected=true
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS schema_drift_findings_run_idx
  ON schema_drift_findings (run_id, severity);
CREATE INDEX IF NOT EXISTS schema_drift_findings_recent_idx
  ON schema_drift_findings (detected_at DESC);

ALTER TABLE schema_drift_findings ENABLE ROW LEVEL SECURITY;
-- rls-policy-exempt: schema_drift_findings select/insert/update/delete (service-role-only substrate table)
DROP POLICY IF EXISTS deny_all_schema_drift_findings ON schema_drift_findings;
CREATE POLICY deny_all_schema_drift_findings ON schema_drift_findings FOR ALL TO public USING (false);
REVOKE ALL ON schema_drift_findings FROM PUBLIC, anon, authenticated;
