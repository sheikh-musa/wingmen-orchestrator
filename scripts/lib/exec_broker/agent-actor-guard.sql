-- =============================================================================
-- agent-actor-guard.sql  —  CANONICAL reusable agent-actor guard wrapper.
--
-- WHY THIS EXISTS (console #46298a / #46299, op#23344):
--   Agent/script prod mgmt-API executions were setting request.jwt.claim.sub to a
--   REAL person's account (Abdul Sukur, 75712b38-9547-4f08-95dd-522187a51769,
--   @gazzabyte.sg) instead of the sanctioned agent actor
--   ca2a4a9c-f745-40c3-9536-7a021fd42bb9 ("Wingmen Orchestrator (agent)",
--   orchestrator@ihsanos.com). Result: ~215 minors'/parents' NRIC decrypts were
--   misattributed to a real client user.
--
-- THREAT MODEL (be honest — this is not a sandbox):
--   This wrapper is fail-closed for ACCIDENTAL misattribution — the incident class,
--   where the auth context was wrongly set at connect for the whole exec. Every prod
--   exec runs inside it (at REPEATABLE READ) and it fails CLOSED at four points
--   (A–D below). DELIBERATE evasion via crafted inner SQL (re-setting the sub or the
--   guard GUCs mid-txn to neutralise the (D) assert) is NOT closed by this SQL alone
--   — inner SQL shares the txn and could overwrite txn-local GUCs. That vector is
--   closed by the scripts/db/exec_prod.py entrypoint screen, which refuses inner SQL
--   that writes request.jwt.* / app.agent_guard_*; gate-review + the append-only
--   audit trail are the compensating controls. The (D) postamble additionally
--   re-checks request.jwt.claim.sub, but the GUC integrity it relies on is
--   guaranteed by that entrypoint screen — run every exec via exec_prod.py.
--
-- HOW TO USE:
--   This is a WRAPPER, not a DB object — NOTHING is installed in the database.
--   An exec pastes its work at the `<<< INNER EXEC WORK GOES HERE >>>` placeholder
--   and runs the whole thing as ONE transaction via the mgmt-API (BEGIN implicit).
--   Prefer generating it programmatically: scripts/db/lib/agent_actor_guard.py
--   wrap() reads THIS file and splices at the placeholder — do NOT inline-fork the
--   guard text into individual scripts (a forked copy drifts and re-opens the hole).
--
-- INVARIANTS (all fail-closed, RAISE = whole txn rolls back):
--   (A) allowlist    — v_actor MUST be a sanctioned agent actor.
--   (B) person-reject — v_actor MUST NOT be a client/person login.
--   (C) sub          — request.jwt.claim.sub is set (txn-local) to v_actor.
--   (D) post-assert  — 0 audit_log rows created in this txn may carry a non-agent
--                      actor; plus (D2) request.jwt.claim.sub is still v_actor at
--                      the end (catches an inner reset of the sub).
--
-- TXN-SCOPE OF THE (D) POST-ASSERT — why REPEATABLE READ (first statement below):
--   The post-assert watermark is v_before = MAX(audit_log.id) captured in the
--   preamble; the assert then counts rows with `id > v_before AND actor_id
--   IS DISTINCT FROM v_actor`. Under the default READ COMMITTED isolation this
--   would FALSE-TRIP: each statement takes a fresh snapshot, so a concurrent
--   OTHER transaction's audit write (e.g. a human using the UI) that commits
--   during this exec — with id > v_before and a person actor_id — would become
--   visible to the post-assert SELECT and be wrongly counted, aborting a clean
--   exec. FIX: run the whole guarded exec at REPEATABLE READ. The txn's snapshot
--   is fixed at its first statement, so:
--     * a concurrent other-txn commit is INVISIBLE to every read in this txn →
--       it can neither be counted (no false-trip) …
--     * … while a wrong-actor row THIS txn itself writes is ALWAYS visible to
--       this txn → it can never be missed.
--   So `id > v_before AND actor_id != v_actor` matches EXACTLY this txn's own
--   audit rows. The count includes trigger-written audit rows too — they run
--   inside this txn and inherit its request.jwt.claim.sub actor, so a compliant
--   exec sees 0 and a misattributing one is caught.
--
-- 🔴 THE ALLOWLIST NEVER CONTAINS A PERSON ACCOUNT. Only agent service accounts
--    (currently just the Wingmen Orchestrator). Adding one is a deliberate,
--    reviewed one-line edit to v_allowlist below (and to SANCTIONED_AGENT_ACTORS
--    in agent_actor_guard.py — keep the two in lockstep).
-- =============================================================================

-- (0) Fix the txn snapshot at start (see TXN-SCOPE note above). MUST be the FIRST
-- statement, before the preamble DO block takes its first read/snapshot. The
-- mgmt-API /database/query path runs this whole file as one implicit txn, so this
-- SET applies to that txn. If a caller path cannot honour SET TRANSACTION as the
-- first statement, it MUST fix the snapshot before the first read some other way
-- (e.g. BEGIN ISOLATION LEVEL REPEATABLE READ) and document it — never fall back
-- to READ COMMITTED, which re-opens the false-trip.
-- [F9] A serialization failure (SQLSTATE 40001) under REPEATABLE READ is
-- fail-closed and ACCEPTABLE: the txn aborts and nothing is written. There is NO
-- auto-retry here (a silent retry could re-run partial work) — the operator simply
-- re-runs the exec via exec_prod.py.
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;

DO $agent_guard_pre$
DECLARE
  -- The actor this exec runs as. Default = Wingmen Orchestrator (agent).
  v_actor uuid := 'ca2a4a9c-f745-40c3-9536-7a021fd42bb9';
  -- Sanctioned agent-actor allowlist. ONE LINE PER ACTOR — never a person account.
  v_allowlist uuid[] := ARRAY[
    'ca2a4a9c-f745-40c3-9536-7a021fd42bb9'   -- Wingmen Orchestrator (agent), orchestrator@ihsanos.com
  ]::uuid[];
BEGIN
  -- (A) allowlist check — fail CLOSED if the actor is not a sanctioned agent actor.
  IF NOT (v_actor = ANY(v_allowlist)) THEN
    RAISE EXCEPTION 'REFUSING: actor % is NOT in the sanctioned agent-actor allowlist', v_actor;
  END IF;

  -- (B) person-login reject — fail CLOSED if the actor is a real client/person
  -- login (@gazzabyte.sg / @irsyad.edu.sg). This is the exact fault that misattributed
  -- ~215 NRIC decrypts to Abdul Sukur (@gazzabyte.sg).
  IF EXISTS (
    SELECT 1 FROM auth.users
    WHERE id = v_actor
      AND (email LIKE '%@gazzabyte.sg' OR email LIKE '%@irsyad.edu.sg')
  ) THEN
    RAISE EXCEPTION 'REFUSING: actor % is a client/person login, not an agent service account', v_actor;
  END IF;

  -- (C) set the JWT sub (txn-local) to the sanctioned AGENT actor, so any RLS /
  -- auth.uid()-dependent path (receipts, hash-chain, audit_log) attributes to it.
  PERFORM set_config('request.jwt.claim.sub', v_actor::text, true);

  -- HIGH-WATER capture: remember the max audit_log id BEFORE the inner work, so the
  -- postamble can assert on exactly the rows this txn creates. Stored txn-local.
  PERFORM set_config('app.agent_guard_actor', v_actor::text, true);
  PERFORM set_config('app.agent_guard_al_before',
                     (SELECT COALESCE(MAX(id), 0) FROM audit_log)::text, true);
END
$agent_guard_pre$;

-- <<< INNER EXEC WORK GOES HERE >>>

DO $agent_guard_post$
DECLARE
  -- v_actor / v_before are read from txn-local GUCs. Their INTEGRITY (that inner SQL
  -- did not overwrite them mid-txn) is guaranteed by the exec_prod.py entrypoint
  -- screen, which refuses inner SQL writing request.jwt.* / app.agent_guard_*.
  v_actor  uuid   := current_setting('app.agent_guard_actor')::uuid;
  v_before bigint := current_setting('app.agent_guard_al_before')::bigint;
  v_badactor int;
  v_sub_now  text := current_setting('request.jwt.claim.sub', true);
BEGIN
  -- (D) post-exec assert — 0 new audit_log rows may carry a non-agent actor.
  -- Catches any inner path that re-set request.jwt.claim.sub back to a person.
  -- NARROW EXEMPTION (console #47721): a row with actor_id IS NULL AND
  -- entity_type = 'schema' is the repo's standard migration "genesis" marker (a
  -- DDL change has no human actor); it is NOT misattribution to a person, so it is
  -- allowed. Everything else still aborts: a NON-NULL actor that is not the agent
  -- (the misattribution case, unchanged), AND a NULL actor on any NON-schema row
  -- (an unattributed DATA audit row hides who made a change — not exempt).
  SELECT count(*) INTO v_badactor
    FROM audit_log
   WHERE id > v_before
     AND actor_id IS DISTINCT FROM v_actor
     AND NOT (actor_id IS NULL AND entity_type = 'schema');
  IF v_badactor > 0 THEN
    RAISE EXCEPTION 'ABORT: % audit_log row(s) created in this txn carry a non-agent actor (expected all = %)',
      v_badactor, v_actor;
  END IF;

  -- (D2) sub-integrity — the JWT sub must still be the agent actor at the end of the
  -- exec. Catches an inner reset of the sub even if it produced no audit rows here.
  IF v_sub_now IS DISTINCT FROM v_actor::text THEN
    RAISE EXCEPTION 'ABORT: request.jwt.claim.sub was changed away from the agent actor % during the exec (now=%)',
      v_actor, COALESCE(v_sub_now, '<null>');
  END IF;
END
$agent_guard_post$;
