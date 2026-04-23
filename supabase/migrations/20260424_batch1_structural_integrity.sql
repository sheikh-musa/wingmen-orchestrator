-- Batch 1 Structural Integrity Bundle
-- Ships 4 P1 fixes per CAI msg #620 consolidated briefing:
--   BUG-024 Phase 1B — agent_status.base_agent_id FK + backfill + prefix CHECK
--   BUG-024 Phase 1C = BUG-032 — strategic_decisions provenance + trigger
--   BUG-031 — is_test on strategic_decisions + enforcer test_mode parameter
--   BUG-029 Part A — is_test on agent_messages
--
-- Parent decisions: BUG-024, BUG-029, BUG-031, BUG-032.
-- Rulings: CAI-RESP-059 (Phase 1B clearance), CAI-RESP-072 (Phase 1 patterns),
--          CAI-RESP-077 (test-mutates-prod root cause), msg #620 (bundling),
--          CAI-RESP-080 (Open Q1/Q2/Q3 + Refinements 1/2).
--
-- Sections:
--   1. agent_status + base_agent_id FK column
--   2. agent_status backfill from agent_id pattern
--   3. agent_status base_agent_id NOT NULL + prefix CHECK
--   4. strategic_decisions provenance columns
--   5. populate_strategic_decisions_provenance trigger
--   6. is_test columns on strategic_decisions + agent_messages
--   7. enforce_challenge_window_timeouts REPLACE with test_mode parameter
--   8. boot_briefing view extension — unverified_decisions section
--
-- Pre-flight verified (see plan Task 1):
--   * identity_allowlist exists (Phase 1A shipped, zero-seed)
--   * agent_messages.sub_tag CHECK name = agent_messages_sub_tag_family_prefix_chk
--   * agents table has 7 rows (broadcast, cai, cc-cosem, cc-ihsanos, cc-scholar,
--     cc-web, musa) — FK target
--   * agent_status has 5 rows (cc-cosem-1, cc-ihsanos-1/2/3, cc-scholar-1) — backfill
--     predictable via regexp_replace

-- ============================================================
-- SECTION 1: agent_status + base_agent_id FK column (nullable initial)
-- ============================================================

-- Reverse: ALTER TABLE agent_status DROP COLUMN base_agent_id;
-- Add nullable first so existing rows don't violate NOT NULL during CREATE.
-- Section 2 backfills; Section 3 enforces NOT NULL + prefix CHECK.
ALTER TABLE agent_status
  ADD COLUMN base_agent_id TEXT REFERENCES agents(id);

-- ============================================================
-- SECTION 2: agent_status backfill base_agent_id from agent_id pattern
-- ============================================================

-- Parse sub-identity suffix `-N` where N is numeric. E.g., cc-ihsanos-3 → cc-ihsanos.
-- Current 5 rows: cc-cosem-1, cc-ihsanos-1, cc-ihsanos-2, cc-ihsanos-3, cc-scholar-1.
-- All parse cleanly to known agent IDs (cc-cosem, cc-ihsanos, cc-scholar) which
-- exist in agents table.
--
-- NOTE: trg_agent_status_identity enforces NEW.agent_id = current_setting('app.current_agent_id')
-- which is impossible for a multi-row UPDATE in a single session. Disable for the
-- backfill, then re-enable. Wrapped in atomic transaction — external observers
-- never see a window where the trigger is disabled.
ALTER TABLE agent_status DISABLE TRIGGER trg_agent_status_identity;

UPDATE agent_status
   SET base_agent_id = regexp_replace(agent_id, '-[0-9]+$', '')
 WHERE base_agent_id IS NULL;

ALTER TABLE agent_status ENABLE TRIGGER trg_agent_status_identity;

-- CAI-RESP-081 CHECK 2: explicit assertion before SET NOT NULL.
-- Belt-and-suspenders over transaction atomicity — Postgres would raise
-- "column contains null values" on SET NOT NULL anyway, but this block
-- surfaces the exact row count for diagnostic clarity if Section 2 regexp
-- somehow fails to match any agent_id (shouldn't happen with current 5
-- rows but guards against edge cases).
DO $batch1_section2_assert$
BEGIN
  IF (SELECT count(*) FROM agent_status WHERE base_agent_id IS NULL) > 0 THEN
    RAISE EXCEPTION 'Batch 1 Section 2 backfill incomplete: % rows still have NULL base_agent_id. Migration rolling back.',
      (SELECT count(*) FROM agent_status WHERE base_agent_id IS NULL);
  END IF;
END
$batch1_section2_assert$;

-- ============================================================
-- SECTION 3: agent_status base_agent_id NOT NULL + prefix CHECK
-- ============================================================

-- After Section 2 backfill (verified by the DO block above), enforce NOT NULL.
-- Any future INSERT must supply base_agent_id (auto_agent_id.py populates
-- explicitly — see Task 12).
-- Reverse: ALTER TABLE agent_status ALTER COLUMN base_agent_id DROP NOT NULL;
ALTER TABLE agent_status
  ALTER COLUMN base_agent_id SET NOT NULL;

-- CAI-RESP-080 Open Q2 ruling: FK alone allows cross-family prefix mismatch
-- (e.g., agent_id='cc-ihsanos-99' with base_agent_id='cc-scholar' — both exist
-- in agents table, so FK accepts; CHECK catches). Structural enforcement
-- preserves the family-identity invariant at INSERT time rather than relying on
-- auto_agent_id.py app-layer discipline.
-- Reverse: ALTER TABLE agent_status DROP CONSTRAINT agent_status_base_agent_id_prefix_chk;
ALTER TABLE agent_status
  ADD CONSTRAINT agent_status_base_agent_id_prefix_chk
  CHECK (base_agent_id = regexp_replace(agent_id, '-[0-9]+$', ''));

-- ============================================================
-- SECTION 4: strategic_decisions provenance columns
-- ============================================================

-- BUG-032 / BUG-024 Phase 1C: parallel pattern to agent_messages Phase 1A.
-- Reverse: ALTER TABLE strategic_decisions DROP COLUMN posted_by_identity;
--          ALTER TABLE strategic_decisions DROP COLUMN decided_by_verified;
ALTER TABLE strategic_decisions
  ADD COLUMN posted_by_identity TEXT;

-- Nullable: NULL = unverified (Phase 1 default), true = allowlist match,
-- false = explicit admin mark (reserved; no auto-false via trigger).
ALTER TABLE strategic_decisions
  ADD COLUMN decided_by_verified BOOLEAN;

-- ============================================================
-- SECTION 5: populate_strategic_decisions_provenance trigger
-- ============================================================

-- BUG-032 per CAI-RESP-077: parallel pattern to populate_agent_messages_provenance.
-- Fires on INSERT OR UPDATE OF decided_by, challenge_status — UPDATE coverage is
-- critical because the bulk-flip failure mode is UPDATE not INSERT.
-- Reuses identity_allowlist from BUG-024 Phase 1A (zero-seed in Phase 1; Phase 2
-- per-key auth populates).
-- Reverse: DROP TRIGGER IF EXISTS trg_strategic_decisions_provenance ON strategic_decisions;
--          DROP FUNCTION IF EXISTS populate_strategic_decisions_provenance();

CREATE OR REPLACE FUNCTION populate_strategic_decisions_provenance()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  -- Only populate if caller did not explicitly set it (admin backfills preserved).
  -- PHASE 2 REWRITE: when per-agent API keys land, replace `current_user` with
  --   current_setting('request.jwt.claims', true)::json ->> 'agent_id'
  -- Trigger signature stays the same; only this one line changes.
  IF NEW.posted_by_identity IS NULL THEN
    NEW.posted_by_identity := current_user;
  END IF;

  IF NEW.decided_by_verified IS NULL THEN
    IF EXISTS (
      SELECT 1 FROM identity_allowlist
       WHERE posted_by = NEW.posted_by_identity
         AND allowed_from_agent = NEW.decided_by
    ) THEN
      NEW.decided_by_verified := true;
    ELSE
      NEW.decided_by_verified := NULL;
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_strategic_decisions_provenance
  BEFORE INSERT OR UPDATE OF decided_by, challenge_status ON strategic_decisions
  FOR EACH ROW
  EXECUTE FUNCTION populate_strategic_decisions_provenance();

-- ============================================================
-- SECTION 6: is_test columns on strategic_decisions + agent_messages
-- ============================================================

-- BUG-031 (strategic_decisions) + BUG-029 Part A (agent_messages).
-- Identical pattern on both tables: BOOLEAN NOT NULL DEFAULT FALSE.
-- Test suites set is_test=TRUE on fixtures; production rows default FALSE.
-- Reverse: ALTER TABLE strategic_decisions DROP COLUMN is_test;
--          ALTER TABLE agent_messages DROP COLUMN is_test;

ALTER TABLE strategic_decisions
  ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE agent_messages
  ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT FALSE;

-- ============================================================
-- SECTION 7: enforce_challenge_window_timeouts REPLACE with test_mode parameter
-- ============================================================

-- BUG-031 per CAI-RESP-077: eliminate test-mutates-prod by gating iteration on
-- is_test flag. Single function, two modes:
--   test_mode=FALSE (default, production): predicate requires is_test = FALSE
--   test_mode=TRUE  (test harness only):    predicate requires is_test = TRUE
-- Production callers (pg_cron, manual admin) do NOT pass test_mode → defaults
-- FALSE. Test suites explicitly pass test_mode=TRUE.
-- Race guard + dry_run/write_mode branching from Fix 2 preserved verbatim.
-- Reverse: DROP FUNCTION IF EXISTS enforce_challenge_window_timeouts(BOOLEAN);
-- Note: Postgres allows overloaded functions; the OR REPLACE here replaces by
-- signature. The prior 0-arg version must be dropped first if it still exists.

DROP FUNCTION IF EXISTS enforce_challenge_window_timeouts();

CREATE OR REPLACE FUNCTION enforce_challenge_window_timeouts(test_mode BOOLEAN DEFAULT FALSE)
RETURNS TABLE(decision_ref TEXT, action TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
-- #variable_conflict directive from Fix 2: plpgsql OUT variable `decision_ref`
-- shadows table column in INSERT ... ON CONFLICT. Prefer column when names clash.
DECLARE
  mode TEXT;
  rec RECORD;
BEGIN
  SELECT value INTO mode
    FROM orchestrator_runtime_config
   WHERE key = 'challenge_enforcer_mode';
  IF mode IS NULL THEN
    mode := 'dry_run';
  END IF;

  FOR rec IN
    SELECT sd.decision_ref, sd.challenge_status, sd.challengeable_until
      FROM strategic_decisions sd
     WHERE sd.challenge_status = 'challenge_window'
       AND sd.challengeable_until IS NOT NULL
       AND sd.challengeable_until < now()
       AND sd.decided_at < now() - interval '1 hour'
       -- BUG-031: is_test predicate inverts based on test_mode parameter.
       -- test_mode=FALSE (prod) → matches is_test=FALSE rows only.
       -- test_mode=TRUE  (test) → matches is_test=TRUE rows only.
       AND sd.is_test = test_mode
  LOOP
    IF mode = 'dry_run' THEN
      INSERT INTO challenge_enforcer_dryrun_log
        (decision_ref, current_challenge_status, challengeable_until, proposed_new_status)
      VALUES
        (rec.decision_ref, rec.challenge_status, rec.challengeable_until, 'accepted_by_timeout')
      ON CONFLICT (decision_ref) DO NOTHING;
      RETURN QUERY SELECT rec.decision_ref, 'logged'::TEXT;
    ELSE
      -- Race guard (from Fix 2 B5 amendment): concurrent challenge flip protection
      UPDATE strategic_decisions
         SET challenge_status = 'accepted_by_timeout',
             updated_at = now()
       WHERE strategic_decisions.decision_ref = rec.decision_ref
         AND strategic_decisions.challenge_status = 'challenge_window';
      IF FOUND THEN
        RETURN QUERY SELECT rec.decision_ref, 'flipped'::TEXT;
      ELSE
        RETURN QUERY SELECT rec.decision_ref, 'skipped_raced'::TEXT;
      END IF;
    END IF;
  END LOOP;
END;
$$;

-- Permissions: same as Fix 2 — only postgres (pg_cron invoker) should execute.
REVOKE EXECUTE ON FUNCTION enforce_challenge_window_timeouts(BOOLEAN) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION enforce_challenge_window_timeouts(BOOLEAN) TO postgres;

-- pg_cron continues to call the zero-arg form (defaults test_mode=FALSE).
-- No schedule change required — existing schedule command 'SELECT enforce_challenge_window_timeouts();'
-- resolves via DEFAULT to the 1-arg form with test_mode=FALSE.

-- ============================================================
-- SECTION 8: boot_briefing view extension
-- ============================================================

-- BUG-032 AC-6: add 'unverified_decisions' section to boot_briefing for
-- per-decided_by count of unverified rows (decided_by_verified IS NULL).
-- In Phase 1 zero-allowlist era, this is effectively every cai-authored
-- decision — the view is observability, not alerting. Phase 2 per-key
-- auth populates allowlist → values fall.
--
-- CRITICAL: CREATE OR REPLACE VIEW drops any section not re-included.
-- The 7 pre-existing sections from Fix 2 (20260422) must be copied
-- byte-for-byte from the current view definition.
-- Reverse: restore the 7-section view from prior migration.

CREATE OR REPLACE VIEW boot_briefing AS
 SELECT 'repo_context'::text AS source,
    rc.repo AS key,
    json_build_object('phase', rc.current_phase, 'blockers', rc.blockers, 'test_health', rc.test_health, 'updated_at', rc.updated_at) AS context
   FROM repo_context rc
UNION ALL
 SELECT 'repo_snapshot'::text AS source,
    rs.repo_name AS key,
    json_build_object('commit_sha', "left"(rs.commit_sha, 8), 'commit_timestamp', rs.commit_timestamp, 'branch', rs.branch, 'file_count', rs.file_count, 'total_loc', rs.total_loc, 'test_count', rs.test_count, 'migration_count', rs.migration_count, 'route_count', rs.route_count, 'schema_tables', rs.schema_tables) AS context
   FROM ( SELECT DISTINCT ON (repo_snapshot.repo_name) repo_snapshot.repo_name,
            repo_snapshot.commit_sha,
            repo_snapshot.commit_timestamp,
            repo_snapshot.branch,
            repo_snapshot.file_count,
            repo_snapshot.total_loc,
            repo_snapshot.test_count,
            repo_snapshot.migration_count,
            repo_snapshot.route_count,
            repo_snapshot.schema_tables
           FROM repo_snapshot
          ORDER BY repo_snapshot.repo_name, repo_snapshot.commit_timestamp DESC) rs
UNION ALL
 SELECT 'active_decision'::text AS source,
    sd.decision_ref AS key,
        CASE
            WHEN sd.decided_at >= (now() - '14 days'::interval) THEN json_build_object('title', "left"(sd.title, 80), 'domain', sd.domain, 'category', sd.category, 'repos', sd.repos_affected, 'source', sd.source, 'challenge_status', sd.challenge_status, 'execution_status', sd.execution_status, 'decided_at', sd.decided_at, 'cai_session_id', sd.cai_session_id, 'decision', sd.decision, 'reasoning', sd.reasoning)
            ELSE json_build_object('title', "left"(sd.title, 80), 'domain', sd.domain, 'category', sd.category, 'repos', sd.repos_affected, 'source', sd.source, 'challenge_status', sd.challenge_status, 'execution_status', sd.execution_status, 'decided_at', sd.decided_at, 'cai_session_id', sd.cai_session_id, 'stub_reason', 'older_than_14_days_fetch_full_via_decision_ref')
        END AS context
   FROM strategic_decisions sd
  WHERE sd.status = 'active'::text
UNION ALL
 SELECT 'open_qa_failure'::text AS source,
    (((qf.repo || '/'::text) || qf.role) || '/'::text) || qf.flow AS key,
    json_build_object('role', qf.role, 'flow', qf.flow, 'error', qf.error, 'found_at', qf.found_at) AS context
   FROM qa_findings qf
  WHERE qf.status = 'fail'::text AND qf.resolved_at IS NULL
UNION ALL
 SELECT 'latest_cc_session'::text AS source,
    sub.repo_name AS key,
    json_build_object('narrative', "left"(sub.narrative, 500), 'outcome', sub.outcome, 'commit_sha', sub.commit_sha, 'created_at', sub.created_at) AS context
   FROM ( SELECT DISTINCT ON (cws.repo_name) cws.repo_name,
            cws.narrative,
            cws.outcome,
            cws.commit_sha,
            cws.created_at
           FROM cc_work_sessions cws
          ORDER BY cws.repo_name, cws.created_at DESC) sub
UNION ALL
 SELECT 'latest_digest'::text AS source,
    dig.title AS key,
    json_build_object('topics', dig.topics_covered, 'open_questions', dig.open_questions, 'action_items', dig.action_items, 'session_date', dig.session_date) AS context
   FROM ( SELECT sd.session_date,
            sd.title,
            sd.topics_covered,
            sd.open_questions,
            sd.action_items
           FROM session_digests sd
          ORDER BY sd.created_at DESC
         LIMIT 1) dig
UNION ALL
 SELECT 'last_cai_session'::text AS source,
    lc.cai_session_id AS key,
    json_build_object('cai_session_id', lc.cai_session_id, 'last_decided_at', lc.last_decided_at, 'gap_days', EXTRACT(day FROM now() - lc.last_decided_at)::integer) AS context
   FROM ( SELECT strategic_decisions.cai_session_id,
            max(strategic_decisions.decided_at) AS last_decided_at
           FROM strategic_decisions
          WHERE strategic_decisions.decided_by = 'cai'::text AND strategic_decisions.cai_session_id IS NOT NULL
          GROUP BY strategic_decisions.cai_session_id
          ORDER BY (max(strategic_decisions.decided_at)) DESC
         LIMIT 1) lc
UNION ALL
-- NEW 8th section: unverified_decisions (BUG-032 AC-6)
SELECT
    'unverified_decisions'::text  AS source,
    COALESCE(sd.decided_by, 'unknown') AS key,
    json_build_object(
        'count',           count(*),
        'oldest_decided',  min(sd.decided_at),
        'newest_decided',  max(sd.decided_at)
    ) AS context
FROM strategic_decisions sd
WHERE sd.decided_by_verified IS NULL
  AND sd.status = 'active'
GROUP BY sd.decided_by;
