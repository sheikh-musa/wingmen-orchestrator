-- BUG-024 Phase 1 — agent_messages provenance layer + CAI-RESP-060 amendment folded
--
-- Parent: BUG-024. References:
--   CAI-RESP-059 (Phase 1 = agent_messages provenance; Phase 1B = agent_status FK)
--   CAI-RESP-060 (amendment: fold cai_session_id + boot_briefing extension into same migration)
--   ARCH-040 (fragmented-cai governance drift — the problem cai_session_id solves)
--   cc-ihsanos msg #543 (minor challenge: time-window boot_briefing text at 14 days)
--
-- Sections:
--   1. agent_messages: posted_by_identity, from_agent_verified, cai_session_id
--   2. strategic_decisions: cai_session_id
--   3. identity_allowlist table + seed
--   4. Trigger: populate_agent_messages_provenance (BEFORE INSERT)
--   5. Indexes
--   6. boot_briefing view extension (time-windowed per msg #543)
--   7. Backfills (msg 252 from_agent_verified=false; cai_session_id NULL explicit)
--
-- Rollback: every ALTER / CREATE has a reverse operation documented inline
-- as a comment above each statement. No rollback script is committed
-- because we ship forward-only and re-migrate if needed.

-- ============================================================
-- SECTION 1: agent_messages provenance columns
-- ============================================================

-- Reverse: ALTER TABLE agent_messages DROP COLUMN posted_by_identity;
ALTER TABLE agent_messages
  ADD COLUMN posted_by_identity TEXT;

-- Reverse: ALTER TABLE agent_messages DROP COLUMN from_agent_verified;
-- Nullable: NULL = unverified (Phase 1 default), true = allowlist-matched,
-- false = explicit admin mark (e.g. msg 252 known impersonation).
ALTER TABLE agent_messages
  ADD COLUMN from_agent_verified BOOLEAN;

-- Reverse: ALTER TABLE agent_messages DROP COLUMN cai_session_id;
-- Nullable: NULL on pre-tracking rows, populated via app convention
-- (cai opens every chat with 'cai-YYYYMMDD-topic' identifier).
ALTER TABLE agent_messages
  ADD COLUMN cai_session_id TEXT;

-- ============================================================
-- SECTION 2: strategic_decisions cai_session_id
-- ============================================================

-- Reverse: ALTER TABLE strategic_decisions DROP COLUMN cai_session_id;
ALTER TABLE strategic_decisions
  ADD COLUMN cai_session_id TEXT;

-- ============================================================
-- SECTION 3: identity_allowlist table
-- ============================================================

-- Reverse: DROP TABLE identity_allowlist;
CREATE TABLE identity_allowlist (
  posted_by          TEXT NOT NULL,
  allowed_from_agent TEXT NOT NULL,
  note               TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (posted_by, allowed_from_agent)
);

-- Phase 1 starts with zero seed rows. Every message posted via service_role
-- will therefore land with from_agent_verified = NULL (untrusted).
-- Populate this table as part of Phase 2 per-key auth rollout once each
-- logical agent has its own key.
--
-- Example seed (commented — do NOT enable in Phase 1):
--   INSERT INTO identity_allowlist (posted_by, allowed_from_agent, note) VALUES
--     ('service_role', 'musa', 'Musa via Telegram relay'),
--     ('cai_supabase_mcp_key', 'cai', 'cai posting directly');

-- ============================================================
-- SECTION 4: provenance trigger
-- ============================================================

-- Reverse: DROP TRIGGER IF EXISTS trg_agent_messages_provenance ON agent_messages;
--          DROP FUNCTION IF EXISTS populate_agent_messages_provenance();
--
-- Behavior:
--   posted_by_identity <- current_user (Postgres session role — most stable
--     identity signal in Phase 1 before per-key auth lands).
--   from_agent_verified <- true iff identity_allowlist covers the pair,
--                          else NULL (never false via trigger; false is
--                          reserved for admin backfill markers).
--
-- The trigger does NOT reject the INSERT — Phase 1 is provenance, not gate.
-- Phase 2/3 will add RLS policy to enforce.

CREATE OR REPLACE FUNCTION populate_agent_messages_provenance()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  -- Only populate if caller did not explicitly set it (allows admin backfills).
  IF NEW.posted_by_identity IS NULL THEN
    NEW.posted_by_identity := current_user;
  END IF;

  -- Only populate if caller did not explicitly set it.
  IF NEW.from_agent_verified IS NULL THEN
    IF EXISTS (
      SELECT 1 FROM identity_allowlist
       WHERE posted_by = NEW.posted_by_identity
         AND allowed_from_agent = NEW.from_agent
    ) THEN
      NEW.from_agent_verified := true;
    ELSE
      NEW.from_agent_verified := NULL;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_agent_messages_provenance
  BEFORE INSERT ON agent_messages
  FOR EACH ROW
  EXECUTE FUNCTION populate_agent_messages_provenance();

-- ============================================================
-- SECTION 5: indexes
-- ============================================================

-- Reverse: DROP INDEX IF EXISTS idx_agent_messages_cai_session;
CREATE INDEX idx_agent_messages_cai_session
  ON agent_messages (cai_session_id, created_at DESC)
  WHERE from_agent = 'cai';

-- Reverse: DROP INDEX IF EXISTS idx_strategic_decisions_cai_session;
CREATE INDEX idx_strategic_decisions_cai_session
  ON strategic_decisions (cai_session_id, decided_at DESC)
  WHERE source = 'claude_ai_session';

-- ============================================================
-- SECTION 6: boot_briefing view extension
-- ============================================================

-- Extend boot_briefing view. Keep 5 pre-existing sections verbatim
-- (repo_context, repo_snapshot, open_qa_failure, latest_cc_session, latest_digest).
-- Augment active_decision with cai_session_id (always) + decision/reasoning
-- (within 14-day window only, per cc-ihsanos msg #543 challenge on CAI-RESP-060
-- Surface 2 — bounds view size regardless of backlog growth).
-- Add 7th section last_cai_session.
--
-- Columns unchanged: (source TEXT, key TEXT, context JSON).
--
-- Reverse: restore the CREATE OR REPLACE VIEW from
-- 20260416_arch024_boot_briefing_v2.sql verbatim.

CREATE OR REPLACE VIEW boot_briefing AS

-- repo_context: phase + blockers + test_health only (ARCH-019 lightweight)
SELECT
    'repo_context'::text AS source,
    rc.repo               AS key,
    json_build_object(
        'phase',      rc.current_phase,
        'blockers',   rc.blockers,
        'test_health',rc.test_health,
        'updated_at', rc.updated_at
    ) AS context
FROM repo_context rc

UNION ALL

-- repo_snapshot: latest structural metadata per repo (ARCH-024)
SELECT
    'repo_snapshot'::text AS source,
    rs.repo_name          AS key,
    json_build_object(
        'commit_sha',       LEFT(rs.commit_sha, 8),
        'commit_timestamp', rs.commit_timestamp,
        'branch',           rs.branch,
        'file_count',       rs.file_count,
        'total_loc',        rs.total_loc,
        'test_count',       rs.test_count,
        'migration_count',  rs.migration_count,
        'route_count',      rs.route_count,
        'schema_tables',    rs.schema_tables
    ) AS context
FROM (
    SELECT DISTINCT ON (repo_name)
        repo_name, commit_sha, commit_timestamp, branch,
        file_count, total_loc, test_count, migration_count,
        route_count, schema_tables
    FROM repo_snapshot
    ORDER BY repo_name, commit_timestamp DESC
) rs

UNION ALL

-- active_decision: index + cai_session_id always, + decision/reasoning iff within 14d.
-- BUG-024 Phase 1 extension (CAI-RESP-060 amendment; cc-ihsanos msg #543 time-window).
-- Pre-existing fields (title/domain/category/repos/source/challenge_status/
-- execution_status/decided_at) preserved exactly — no consumer breakage.
SELECT
    'active_decision'::text    AS source,
    sd.decision_ref            AS key,
    CASE
      WHEN sd.decided_at >= now() - interval '14 days' THEN
        json_build_object(
            'title',            LEFT(sd.title, 80),
            'domain',           sd.domain,
            'category',         sd.category,
            'repos',            sd.repos_affected,
            'source',           sd.source,
            'challenge_status', sd.challenge_status,
            'execution_status', sd.execution_status,
            'decided_at',       sd.decided_at,
            'cai_session_id',   sd.cai_session_id,
            'decision',         sd.decision,
            'reasoning',        sd.reasoning
        )
      ELSE
        json_build_object(
            'title',            LEFT(sd.title, 80),
            'domain',           sd.domain,
            'category',         sd.category,
            'repos',            sd.repos_affected,
            'source',           sd.source,
            'challenge_status', sd.challenge_status,
            'execution_status', sd.execution_status,
            'decided_at',       sd.decided_at,
            'cai_session_id',   sd.cai_session_id,
            'stub_reason',      'older_than_14_days_fetch_full_via_decision_ref'
        )
    END AS context
FROM strategic_decisions sd
WHERE sd.status = 'active'

UNION ALL

-- open_qa_failure: unchanged (already minimal)
SELECT
    'open_qa_failure'::text                                      AS source,
    ((((qf.repo || '/'::text) || qf.role) || '/'::text) || qf.flow) AS key,
    json_build_object(
        'role',     qf.role,
        'flow',     qf.flow,
        'error',    qf.error,
        'found_at', qf.found_at
    ) AS context
FROM qa_findings qf
WHERE qf.status = 'fail' AND qf.resolved_at IS NULL

UNION ALL

-- latest_cc_session: narrative truncated to 500 chars (ARCH-019)
SELECT
    'latest_cc_session'::text AS source,
    sub.repo_name             AS key,
    json_build_object(
        'narrative',  LEFT(sub.narrative, 500),
        'outcome',    sub.outcome,
        'commit_sha', sub.commit_sha,
        'created_at', sub.created_at
    ) AS context
FROM (
    SELECT DISTINCT ON (cws.repo_name)
        cws.repo_name, cws.narrative, cws.outcome,
        cws.commit_sha, cws.created_at
    FROM cc_work_sessions cws
    ORDER BY cws.repo_name, cws.created_at DESC
) sub

UNION ALL

-- latest_digest: summary format (unchanged)
SELECT
    'latest_digest'::text AS source,
    dig.title             AS key,
    json_build_object(
        'topics',         dig.topics_covered,
        'open_questions', dig.open_questions,
        'action_items',   dig.action_items,
        'session_date',   dig.session_date
    ) AS context
FROM (
    SELECT sd.session_date, sd.title, sd.topics_covered,
           sd.open_questions, sd.action_items
    FROM session_digests sd
    ORDER BY sd.created_at DESC
    LIMIT 1
) dig

UNION ALL

-- last_cai_session: most recent cai-authored session_id + gap_days (BUG-024 Phase 1, CAI-RESP-060).
-- Surfaces fragmented-cai problem: fresh cai chats see the gap since last cai decision.
SELECT
    'last_cai_session'::text  AS source,
    lc.cai_session_id          AS key,
    json_build_object(
        'cai_session_id',  lc.cai_session_id,
        'last_decided_at', lc.last_decided_at,
        'gap_days',        EXTRACT(DAY FROM (now() - lc.last_decided_at))::int
    ) AS context
FROM (
    SELECT
        cai_session_id,
        MAX(decided_at) AS last_decided_at
    FROM strategic_decisions
    WHERE decided_by = 'cai'
      AND cai_session_id IS NOT NULL
    GROUP BY cai_session_id
    ORDER BY MAX(decided_at) DESC
    LIMIT 1
) lc;

-- ============================================================
-- SECTION 7: backfills
-- ============================================================

-- Backfill msg 252: confirmed CAI impersonation incident (BUG-024).
-- No-op if msg 252 does not exist (e.g. fresh local dev DB).
UPDATE agent_messages
   SET from_agent_verified = false,
       posted_by_identity   = 'unknown_impersonator'
 WHERE id = 252
   AND from_agent_verified IS DISTINCT FROM false;

-- cai_session_id NULL backfill is implicit via column default (NULL).
-- No UPDATE needed — new column on existing rows starts NULL.
-- This comment is the explicit documentation for future readers.
