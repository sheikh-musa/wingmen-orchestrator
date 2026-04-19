-- ARCH-035: Three-channel governance taxonomy.
--
-- Parent: ARCH-035 (decision_ref). References:
--   docs/superpowers/specs/2026-04-19-arch-035-three-channel-governance-taxonomy-design.md
--   CAI-RESP-036 (8-item amendment)
--   CAI-RESP-042 (3-item pre-write clarification)
--   CAI-RESP-043 (B1 ship blocker + B2 dblink violations log + C2/C3/C4 polish)
--   CAI-RESP-044 (dblink + pg_cron confirmed enabled on this project)
--
-- Shape:
--   1. agent_status        — current work state, one row per agent
--   2. agent_status_history — append-only forensic log, AFTER trigger snapshot
--   3. agent_status_identity_violations — forensic log of trigger rejections
--   4. enforce_agent_status_identity() — SECURITY DEFINER BEFORE trigger comparing
--      NEW.agent_id to app.current_agent_id GUC; rejections logged via dblink
--      autonomous transaction so log survives caller's rolled-back tx
--   5. stale_agents view  — drift detection (15-min heartbeat threshold)
--   6. updated_at auto-bump trigger on agent_status
--   7. agent_messages CHECK constraint — narrows message_type to 7 values
--
-- Forward-only migration. Apply ONCE via Supabase dashboard SQL editor
-- (CREATE TABLE / TRIGGER / INDEX will error on re-run — this is by repo
-- convention; see 20260419_bug025_* / 20260419_bug020_bug021_* neighbors).
-- Owner context: dashboard SQL editor runs as 'postgres' role on Supabase,
-- so SECURITY DEFINER functions below bind to 'postgres'. dblink_exec uses
-- implicit peer/trust auth available to 'postgres' on the same cluster
-- (CAI-RESP-044 confirmed dblink 1.2 enabled). If apply throws
-- 'could not establish connection', swap dblink_exec() for dblink_connect_u()
-- with explicit conninfo — but on Supabase managed postgres this is unneeded.

-- 1. agent_status — one row per agent, current work state.
CREATE TABLE agent_status (
  agent_id                TEXT PRIMARY KEY,
  status                  TEXT NOT NULL CHECK (status IN ('idle','working','blocked','offline')),
  current_task            TEXT,
  scope_repos             TEXT[],
  scope_paths             TEXT[],
  blocked_on_msg_id       BIGINT REFERENCES agent_messages(id) ON DELETE SET NULL,
  blocked_on_decision_ref TEXT REFERENCES strategic_decisions(decision_ref) ON DELETE SET NULL,
  blocked_on_description  TEXT,
  last_commit_sha         TEXT,
  last_commit_repo        TEXT,
  last_heartbeat          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_status_active ON agent_status (last_heartbeat DESC) WHERE status != 'offline';

-- 2. agent_status_history — append-only forensic snapshot (no FK to agent_status; deletes do not cascade).
CREATE TABLE agent_status_history (
  id                      BIGSERIAL PRIMARY KEY,
  agent_id                TEXT NOT NULL,
  status                  TEXT NOT NULL,
  current_task            TEXT,
  scope_repos             TEXT[],
  scope_paths             TEXT[],
  blocked_on_msg_id       BIGINT,
  blocked_on_decision_ref TEXT,
  last_commit_sha         TEXT,
  recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_status_history_agent_time ON agent_status_history (agent_id, recorded_at DESC);

-- 3. AFTER trigger — snapshot every change.
CREATE OR REPLACE FUNCTION snapshot_agent_status_to_history()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO agent_status_history (
    agent_id, status, current_task, scope_repos, scope_paths,
    blocked_on_msg_id, blocked_on_decision_ref, last_commit_sha
  ) VALUES (
    NEW.agent_id, NEW.status, NEW.current_task, NEW.scope_repos, NEW.scope_paths,
    NEW.blocked_on_msg_id, NEW.blocked_on_decision_ref, NEW.last_commit_sha
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agent_status_snapshot
  AFTER INSERT OR UPDATE ON agent_status
  FOR EACH ROW EXECUTE FUNCTION snapshot_agent_status_to_history();

-- 4a. Identity violations forensic log.
CREATE TABLE agent_status_identity_violations (
  id              BIGSERIAL PRIMARY KEY,
  claimed_agent   TEXT,
  guc_value       TEXT,
  violation_type  TEXT NOT NULL CHECK (violation_type IN ('guc_not_set','identity_mismatch')),
  attempted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  db_session_user TEXT NOT NULL DEFAULT session_user,  -- renamed to not shadow the builtin function
  operation       TEXT NOT NULL
);
CREATE INDEX idx_violations_recent ON agent_status_identity_violations (attempted_at DESC);

-- 4b. Autonomous-tx helper (dblink) — log survives caller's rolled-back transaction.
--     CAI-RESP-044 Q1 confirmed dblink 1.2 + pg_cron 1.6.4 enabled on this project.
--     pg_cron is created here so Task 7 (banned-prefix purge job) can register
--     jobs without a second migration. No cron jobs registered in THIS file.
CREATE EXTENSION IF NOT EXISTS dblink;
CREATE EXTENSION IF NOT EXISTS pg_cron;

CREATE OR REPLACE FUNCTION log_agent_status_identity_violation(
  p_claimed TEXT, p_guc TEXT, p_type TEXT, p_op TEXT
) RETURNS VOID AS $$
BEGIN
  PERFORM dblink_exec(
    'dbname=' || current_database(),
    format(
      $sql$INSERT INTO agent_status_identity_violations
           (claimed_agent, guc_value, violation_type, operation)
           VALUES (%L, %L, %L, %L)$sql$,
      p_claimed, p_guc, p_type, p_op
    )
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 4c. Identity tripwire — SECURITY DEFINER BEFORE trigger.
CREATE OR REPLACE FUNCTION enforce_agent_status_identity()
RETURNS TRIGGER AS $$
DECLARE
  v_guc_agent TEXT;
BEGIN
  v_guc_agent := current_setting('app.current_agent_id', true);
  IF v_guc_agent IS NULL OR v_guc_agent = '' THEN
    PERFORM log_agent_status_identity_violation(NEW.agent_id, v_guc_agent, 'guc_not_set', TG_OP);
    RAISE EXCEPTION 'agent_status write rejected: app.current_agent_id GUC not set (use SET LOCAL at session-start)'
      USING ERRCODE = '42501';
  END IF;
  IF NEW.agent_id IS DISTINCT FROM v_guc_agent THEN
    PERFORM log_agent_status_identity_violation(NEW.agent_id, v_guc_agent, 'identity_mismatch', TG_OP);
    RAISE EXCEPTION 'agent_status write rejected: NEW.agent_id=% but GUC app.current_agent_id=% (identity mismatch)', NEW.agent_id, v_guc_agent
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_agent_status_identity
  BEFORE INSERT OR UPDATE ON agent_status
  FOR EACH ROW EXECUTE FUNCTION enforce_agent_status_identity();

-- 5. stale_agents view — drift detection, not prevention.
--    15-min threshold is a magic number for v1. If per-agent thresholds diverge
--    later, promote to a configurable GUC or per-agent column.
CREATE OR REPLACE VIEW stale_agents AS
SELECT agent_id, status, current_task,
       last_heartbeat, (now() - last_heartbeat) AS heartbeat_age
FROM agent_status
WHERE status != 'offline' AND last_heartbeat < now() - interval '15 minutes';

-- 6. updated_at auto-bump.
CREATE OR REPLACE FUNCTION bump_agent_status_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_agent_status_updated_at BEFORE UPDATE ON agent_status
  FOR EACH ROW EXECUTE FUNCTION bump_agent_status_updated_at();

-- 7. agent_messages CHECK constraint (A2 + CAI-RESP-042 Q1 — 7 values including 'blocker').
ALTER TABLE agent_messages
  ADD CONSTRAINT agent_messages_message_type_check
  CHECK (message_type IN ('review_request','question','decision','agreed','challenge','update','blocker'));
