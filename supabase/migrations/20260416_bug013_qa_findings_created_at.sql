-- BUG-013: qa_findings.created_at column missing in live DB.
-- The QA bridge (nervous_system/qa_bridge.py) orders findings by created_at,
-- but the live table (legacy schema) has only found_at. Add created_at with
-- DEFAULT now() so existing rows backfill in a single statement, and add an
-- index to support the bridge's ORDER BY created_at DESC reads.

ALTER TABLE qa_findings
  ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS qa_findings_created_at_idx
  ON qa_findings (created_at DESC);
