-- 008: lane_tasks — per-lane task queue (operator-visible, manual priority order).
-- cc-orchestrator maintains it (one row per delegated task); the Fleet Console
-- DISPLAYS it read-only grouped by lane in priority_rank order. The operator
-- reorders via the bridge (cc-orchestrator updates priority_rank) — the console
-- stays read-only (no write path) per CAI-RESP-264. Applied live via psycopg.
CREATE TABLE IF NOT EXISTS lane_tasks (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  lane          text NOT NULL,
  title         text NOT NULL,
  detail        text,
  priority_rank int  NOT NULL DEFAULT 100,
  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','active','done','blocked')),
  source_msg_id bigint,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lane_tasks_lane_rank_idx ON lane_tasks (lane, priority_rank) WHERE status <> 'done';
GRANT SELECT ON lane_tasks TO console_readonly;
