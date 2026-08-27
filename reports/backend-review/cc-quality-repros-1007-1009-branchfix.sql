-- ============================================================================
-- cc-quality REPRO + ACCEPTANCE SUITE
-- For: CAI-1007 (PATH 3 escalated_at overload), CAI-1009 (audit_tier guard),
--      and the completed_at-vs-checks_performed branch fix.
-- Handed to orch-console at his request (he declined to write his own against a
-- defect I characterised). These are the SAME savepoint tests I used to prove
-- each defect, plus the controls, plus the assertions I will audit against.
--
-- EVERYTHING ROLLS BACK, and that is now enforced rather than intended: ON_ERROR_STOP is ON
-- in-file, every section sits ABOVE the final ROLLBACK, and a read-only INVARIANT CHECK at
-- the very end fails loudly if anything escaped. Verify with: tail -n 3 (must be the ROLLBACK
-- and the invariant block, nothing else).
-- Expected-BEFORE lines are what I measured on 2026-08-16/17; expected-AFTER
-- lines are the acceptance bar. Published in advance: these are the conditions,
-- and I will not add new ones after the build (CAI-998 fairness principle).
-- ============================================================================
-- ON_ERROR_STOP is set to ON *in the file* deliberately: an in-file \set OVERRIDES the
-- command-line -v, so telling a caller to pass -v ON_ERROR_STOP=1 could never have worked
-- while this line said 'off' (proven: effective value came back 'off'). The file must guard
-- itself rather than depend on how it is invoked. This suite has NO deliberate-error tests,
-- so 'on' is correct: any unexpected error aborts psql and the open transaction rolls back.
\set ON_ERROR_STOP on
BEGIN;

-- ---------------------------------------------------------------------------
-- A. CAI-1007 — PATH 3 stamps escalated_at on rows it did not escalate for,
--    consuming paths 1+2 eligibility. Suppression must be per (decision, STATE)
--    and RE-ARM on state change; paths 1+2 keep per-ROW semantics untouched.
-- ---------------------------------------------------------------------------

\echo '=== A-CONTROL: unresolved verdict, NO prior PATH 3 escalation -> MUST escalate (both before and after) ==='
SAVEPOINT a0;
UPDATE decision_audits SET verdict='could_not_verify', resolved_at=NULL,
       checks_performed='control: unresolved verdict, no prior path-3 escalation',
       completed_at = now() - interval '30 hours'
 WHERE id = 7;
UPDATE decision_audits SET completed_at = now() - interval '30 hours' WHERE id = 19;
SELECT 'A-CONTROL' AS t, count(*) AS fired, 'expect 1 (escalated_unresolved)' AS bar
  FROM escalate_stale_decision_audits();
ROLLBACK TO SAVEPOINT a0;

\echo '=== A-FACE1: PATH 3 fires, stamps all rows, THEN a verdict is revised to could_not_verify ==='
SAVEPOINT a1;
UPDATE decision_audits SET verdict='accepted', resolved_at=NULL,
       checks_performed='setup: all-accepted so PATH 3 fires and stamps every row',
       completed_at = now() - interval '30 hours'
 WHERE id = 7;
UPDATE decision_audits SET completed_at = now() - interval '30 hours' WHERE id = 19;
SELECT 'A-FACE1 step1' AS t, action, 'expect escalated_never_closed' AS bar
  FROM escalate_stale_decision_audits();
SELECT 'A-FACE1 stamping' AS t, id, verdict, escalated_at IS NOT NULL AS stamped,
       'BEFORE: both stamped though neither was the reason' AS note
  FROM decision_audits WHERE decision_ref='CAI-RESP-987' ORDER BY id;
-- The state now CHANGES: an auditor revises to an unresolved verdict (a re-audit).
UPDATE decision_audits SET verdict='could_not_verify', resolved_at=NULL,
       checks_performed='state transition: auditor revises to could_not_verify on new information',
       completed_at = now() - interval '30 hours'
 WHERE id = 7;
SELECT 'A-FACE1 board' AS t, audit_state, n_unresolved, n_stale
  FROM decision_audit_state WHERE decision_ref='CAI-RESP-987';
SELECT 'A-FACE1 RESULT' AS t, count(*) AS fired,
       'BEFORE: 0 (the defect). AFTER: >=1, escalated_unresolved' AS bar
  FROM escalate_stale_decision_audits();
ROLLBACK TO SAVEPOINT a1;

\echo '=== A-FACE2: after PATH 3, a THIRD auditor accepts and nobody closes -> never-closed must re-arm ==='
SAVEPOINT a2;
UPDATE decision_audits SET verdict='accepted', resolved_at=NULL,
       checks_performed='setup: all-accepted so PATH 3 fires and stamps every row',
       completed_at = now() - interval '30 hours'
 WHERE id = 7;
UPDATE decision_audits SET completed_at = now() - interval '30 hours' WHERE id = 19;
SELECT 'A-FACE2 step1' AS t, count(*) AS fired FROM escalate_stale_decision_audits();
INSERT INTO decision_audits (decision_ref,auditor_agent,assigned_by,lens,verdict,completed_at,
                             checks_performed,assigned_at)
VALUES ('CAI-RESP-987','cc-fleet-health','cai','grant-posture','accepted',
        now() - interval '30 hours',
        'third auditor accepts later; nobody closes. does never-closed re-arm on the new state?',
        now() - interval '40 hours');
SELECT 'A-FACE2 board' AS t, audit_state, n_accepted, n_open, n_unresolved
  FROM decision_audit_state WHERE decision_ref='CAI-RESP-987';
SELECT 'A-FACE2 RESULT' AS t, count(*) AS fired,
       'BEFORE: 0 (once-per-decision-forever). AFTER: >=1, the state changed' AS bar
  FROM escalate_stale_decision_audits();
ROLLBACK TO SAVEPOINT a2;

\echo '=== A-CONTROL2: an already-CLOSED decision must NEVER escalate (no false positive) ==='
SAVEPOINT a3;
UPDATE decision_audits SET verdict='accepted', resolved_at=NULL,
       checks_performed='control: audited clean, and the decision IS closed',
       completed_at = now() - interval '30 hours' WHERE id = 7;
UPDATE decision_audits SET completed_at = now() - interval '30 hours' WHERE id = 19;
UPDATE strategic_decisions SET challenge_status='accepted_by_audit' WHERE decision_ref='CAI-RESP-987';
SELECT 'A-CONTROL2' AS t, count(*) AS fired, 'expect 0, before AND after' AS bar
  FROM escalate_stale_decision_audits();
ROLLBACK TO SAVEPOINT a3;

-- ---------------------------------------------------------------------------
-- B. CAI-1009 — audit_tier is unguarded, so FULL->NONE mid-window lets the
--    enforcer close a FULL decision that never had an audit row.
-- ---------------------------------------------------------------------------

\echo '=== B-DEFECT: FULL + zero rows, tier dropped to NONE -> enforcer closes it ==='
SAVEPOINT b1;
DELETE FROM decision_audits WHERE decision_ref='CAI-RESP-1006';
SELECT 'B window state' AS t, audit_tier, audit_state, n_assigned, audit_required
  FROM decision_audit_state WHERE decision_ref='CAI-RESP-1006';
UPDATE strategic_decisions SET audit_tier='NONE',
       challengeable_until = now() - interval '1 hour',
       decided_at = now() - interval '2 hours'
 WHERE decision_ref='CAI-RESP-1006';
SELECT 'B enforcer' AS t, action,
       'BEFORE: flipped. AFTER: the tier drop is refused, or recorded and the close withheld' AS bar
  FROM enforce_challenge_window_timeouts() WHERE decision_ref='CAI-RESP-1006';
SELECT 'B final' AS t, challenge_status,
       'BEFORE: accepted_by_timeout with zero rows ever. AFTER: NOT closed' AS bar
  FROM strategic_decisions WHERE decision_ref='CAI-RESP-1006';
ROLLBACK TO SAVEPOINT b1;

\echo '=== B-CONTROL1: a LEGITIMATE re-tier must still work (no refuse-everything guard) ==='
SAVEPOINT b2;
UPDATE strategic_decisions SET audit_tier='FULL' WHERE decision_ref='CAI-RESP-992';
SELECT 'B-CONTROL1' AS t, audit_tier, 'expect the raise-to-FULL to succeed' AS bar
  FROM strategic_decisions WHERE decision_ref='CAI-RESP-992';
ROLLBACK TO SAVEPOINT b2;

\echo '=== B-CONTROL2: a genuinely untiered decision must still time out normally ==='
SAVEPOINT b3;
SELECT 'B-CONTROL2' AS t, count(*) AS untiered_closable,
       'the enforcer must keep closing untiered rows; the guard is about was-FULL only' AS bar
  FROM strategic_decisions
 WHERE audit_tier IS NULL AND challenge_status='challenge_window';
ROLLBACK TO SAVEPOINT b3;

-- ---------------------------------------------------------------------------
-- C. BRANCH FIX — escalate_stale_decision_audits branches on completed_at
--    (a proxy) when the evidence is checks_performed, in the same row.
-- ---------------------------------------------------------------------------

\echo '=== C-EXAMINED: checks_performed present + completed_at NULL -> EXAMINED-BLOCKED, never "nobody looked" ==='
SAVEPOINT c1;
UPDATE decision_audits SET assigned_at = now() - interval '30 hours'
 WHERE decision_ref='CAI-RESP-996' AND auditor_agent='cc-quality';
SELECT 'C-EXAMINED precondition' AS t,
       (checks_performed IS NOT NULL) AS has_evidence, (completed_at IS NULL) AS not_completed
  FROM decision_audits WHERE decision_ref='CAI-RESP-996' AND auditor_agent='cc-quality';
SELECT 'C-EXAMINED result' AS t, action FROM escalate_stale_decision_audits()
 WHERE decision_ref='CAI-RESP-996';
SELECT 'C-EXAMINED message' AS t, subject,
       (body LIKE '%Nobody has looked at this yet%') AS says_nobody_looked,
       'BEFORE: NOT STARTED / says_nobody_looked = TRUE (false claim). AFTER: EXAMINED-BLOCKED / FALSE' AS bar
  FROM agent_messages WHERE subject LIKE 'STALE AUDIT%' ORDER BY id DESC LIMIT 1;
ROLLBACK TO SAVEPOINT c1;

\echo '=== C-UNTOUCHED: checks_performed NULL + completed_at NULL -> must STILL say NOT STARTED ==='
SAVEPOINT c2;
UPDATE decision_audits SET checks_performed = NULL, assigned_at = now() - interval '30 hours'
 WHERE decision_ref='CAI-RESP-996' AND auditor_agent='cc-quality';
SELECT 'C-UNTOUCHED result' AS t, action FROM escalate_stale_decision_audits()
 WHERE decision_ref='CAI-RESP-996';
SELECT 'C-UNTOUCHED message' AS t, subject,
       (body LIKE '%Nobody has looked at this yet%') AS says_nobody_looked,
       'expect NOT STARTED / TRUE, before AND after — the honest case must not regress' AS bar
  FROM agent_messages WHERE subject LIKE 'STALE AUDIT%' ORDER BY id DESC LIMIT 1;
ROLLBACK TO SAVEPOINT c2;

\echo '=== C-STILL-ESCALATES: a blocked row must NOT go silent ==='
SAVEPOINT c3;
UPDATE decision_audits SET assigned_at = now() - interval '30 hours'
 WHERE decision_ref='CAI-RESP-996' AND auditor_agent='cc-quality';
SELECT 'C-STILL-ESCALATES' AS t, count(*) AS fired,
       'expect >=1 both before and after — the fix changes the WORDS, never the firing' AS bar
  FROM escalate_stale_decision_audits() WHERE decision_ref='CAI-RESP-996';
ROLLBACK TO SAVEPOINT c3;

-- ---------------------------------------------------------------------------
-- D. NON-REGRESSION on the folded migration (my CAI-975 condition for agreeing
--    to fold both changes into ONE CREATE OR REPLACE).
-- ---------------------------------------------------------------------------
\echo '=== D: paths 1+2 predicate and the escalation kinds must be unchanged ==='
SELECT 'D-baseline' AS t,
       (pg_get_functiondef(p.oid) ILIKE '%decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at)%') AS unresolved_predicate_intact,
       (pg_get_functiondef(p.oid) ILIKE '%escalated_at IS NULL%')      AS per_row_marker_present,
       (pg_get_functiondef(p.oid) ILIKE '%COALESCE(sd.is_test, false) = false%') AS is_test_filter_intact,
       (pg_get_functiondef(p.oid) ILIKE '%AUDITED CLEAN, NEVER CLOSED%') AS path3_present
  FROM pg_proc p WHERE p.proname='escalate_stale_decision_audits';


-- ============================================================================
-- ⚠ SAFETY: every section below MUST run inside the transaction opened at the
-- top. This file previously had section D2 appended AFTER the final ROLLBACK;
-- psql autocommits outside a transaction block, so the savepoints failed, the
-- setup UPDATEs COMMITTED against production, and the escalator fired for real
-- (it mutated an audit row and sent a false STALE AUDIT to cai, since repaired).
-- EDITING RULE, stated precisely because the loose version is what caused the incident:
--   * TEST SECTIONS (anything that writes) go ABOVE the final ROLLBACK. Always.
--   * BELOW the final ROLLBACK: READ-ONLY ASSERTIONS ONLY — never an INSERT, UPDATE,
--     DELETE, DDL or COMMIT. The invariant check at the end lives there LEGITIMATELY,
--     because it must run AFTER the rollback to prove the rollback worked.
--   * The earlier rule said 'never below the ROLLBACK'. That was wrong as written, and
--     dangerously so: an editor who reads 'never below' and then SEES something below
--     concludes the rule is soft and appends next to it — which is exactly how the
--     damaging append happened. The line is not position, it is READ-ONLY vs WRITE.
-- ON_ERROR_STOP is set ON in-file (line ~22) and must stay on.
-- ============================================================================

-- ============================================================================
-- D2. BEHAVIOURAL NON-REGRESSION — SUPERSEDES SECTION D AS THE GATE.
--
-- WHY D2 EXISTS: Section D is a set of ILIKE matches on pg_get_functiondef.
-- That is a DEFINITION READ, which is precisely what CAI-1005 ratified against
-- ("a post-condition must EXERCISE the shipped path, not inspect it" — 059's
-- bug was post-conditions that read a function's text and never called it).
-- I wrote D and handed it over as a gate; that was the same mistake, from me.
--
-- CONCRETE FALSE-PASS CHANNEL, measured: 'escalated_at IS NULL' occurs TWICE in
-- the current definition — line ~20 (paths 1+2 per-row marker, the thing being
-- protected) and line ~96 (PATH 3's stamping UPDATE, the line the fix REMOVES).
-- An ILIKE cannot tell which occurrence satisfied it, and the fix edits one of
-- the two. So the assertion guarding the marker can pass on the strength of a
-- line that is supposed to disappear.
--
-- USE D2 AS THE POST-CONDITION. Keep D as a cheap extra tripwire only.
-- ============================================================================

\echo '=== D2-1: per-row marker still consulted — a row must escalate EXACTLY ONCE ==='
SAVEPOINT d21;
UPDATE decision_audits SET verdict='could_not_verify', resolved_at=NULL,
       checks_performed='D2-1: prove paths 1+2 still consult the per-row escalated_at marker',
       completed_at = now() - interval '30 hours'
 WHERE id = 7;
SELECT 'D2-1 first run' AS t, count(*) AS fired, 'expect 1' AS bar
  FROM escalate_stale_decision_audits() WHERE decision_ref='CAI-RESP-987';
SELECT 'D2-1 second run' AS t, count(*) AS fired,
       'expect 0 — if this is 1, the per-row marker is no longer gating and D would still have passed' AS bar
  FROM escalate_stale_decision_audits() WHERE decision_ref='CAI-RESP-987';
ROLLBACK TO SAVEPOINT d21;

\echo '=== D2-2: is_test exclusion, behaviourally (not by string) ==='
SAVEPOINT d22;
INSERT INTO strategic_decisions (decision_ref,title,decision,reasoning,domain,category,decided_by,
                                 audit_tier,challenge_status,is_test,source)
VALUES ('CC-QUALITY-ISTEST-PROBE','probe','probe','probe','architecture','governance','cai',
        'FULL','unchallenged',true,'claude_code_proposal');
INSERT INTO decision_audits (decision_ref,auditor_agent,assigned_by,lens,assigned_at)
VALUES ('CC-QUALITY-ISTEST-PROBE','cc-storefront','cai','implementation-correctness',
        now() - interval '40 hours');
SELECT 'D2-2' AS t, count(*) AS fired,
       'expect 0 — a stale is_test row must never escalate' AS bar
  FROM escalate_stale_decision_audits() WHERE decision_ref='CC-QUALITY-ISTEST-PROBE';
ROLLBACK TO SAVEPOINT d22;

\echo '=== D2-3: PATH 3 still reachable behaviourally (all-accepted, never closed) ==='
SAVEPOINT d23;
UPDATE decision_audits SET verdict='accepted', resolved_at=NULL,
       checks_performed='D2-3: prove PATH 3 still fires at all after the change',
       completed_at = now() - interval '30 hours' WHERE id = 7;
UPDATE decision_audits SET completed_at = now() - interval '30 hours' WHERE id = 19;
SELECT 'D2-3' AS t, count(*) AS fired, 'expect >=1 escalated_never_closed' AS bar
  FROM escalate_stale_decision_audits() WHERE decision_ref='CAI-RESP-987';
ROLLBACK TO SAVEPOINT d23;

ROLLBACK;
\echo '=== ALL ROLLED BACK ==='

-- ============================================================================
-- POST-RUN INVARIANT CHECK — deliberately OUTSIDE the transaction, READ-ONLY.
-- This exists because the harness itself failed once: a section appended below
-- the final ROLLBACK ran in autocommit and mutated production. A suite that can
-- damage what it tests must be able to TELL you that it did.
-- ============================================================================
SELECT 'INVARIANT' AS check,
       CASE WHEN (SELECT verdict FROM decision_audits WHERE id=7) = 'rejected'
             AND (SELECT length(checks_performed) FROM decision_audits WHERE id=7) = 5345
             AND (SELECT count(*) FROM decision_audits WHERE escalated_at IS NOT NULL) = 0
             AND (SELECT count(*) FROM strategic_decisions WHERE decision_ref LIKE 'CC-QUALITY-%') = 0
            THEN 'PASS — nothing escaped the transaction'
            ELSE '*** FAIL — THIS SUITE MUTATED PRODUCTION. Restore before doing anything else. ***'
       END AS result;
