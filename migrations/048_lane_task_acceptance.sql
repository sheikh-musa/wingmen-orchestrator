-- 048_lane_task_acceptance.sql
-- "Done" becomes an ACCEPTED claim, not a self-declared one.
--
-- OPERATOR, 2026-08-16 (op#13584/13589, his design, his "go" at op#13591):
--   "isnt it the coordinators job to know when a lane is done with its task? the simple flow is
--    3 agents right - planner, builder, auditor. when a task goes through the 3 until it meets
--    the requirements of the planner then its done."
--   and: "cant the lanes auto drain their own backlogs until truly completed?"
--   The answer to the second is NO, and it is the whole of 2026-08-16: a lane draining its own
--   queue still DECLARES ITS OWN DONENESS. That night alone produced an SSRF test that passed on
--   the vulnerable code, a readiness board reading 10/10 green off a measurer that was never
--   scheduled, and a migration whose EXERCISED state was unreachable — written and re-read three
--   times by its author without seeing it. Every one was self-declared done. Every one was wrong.
--   The auditor is what turns a claim into a fact.
--
-- THIS IS CAI-RESP-978/986 APPLIED TO WORK ITEMS. §1: NEVER GREEN ON ABSENCE-OF-SIGNAL. A control
--   is not satisfied until it has EXECUTED and been OBSERVED. A task is not done until someone
--   OTHER than the doer has accepted it against criteria written BEFORE the work.
--
-- WHAT THIS DOES NOT DO, deliberately:
--   * It does NOT add a status value. `lane_tasks_status_check` allows exactly
--     queued|active|done|blocked, and inventing a token that the CHECK rejects is precisely the
--     bug that made migration 047's EXERCISED state UNREACHABLE. Schema first, vocabulary second.
--   * It does NOT block writes. Nothing is forced to fill these in; the view simply stops
--     reporting an unaccepted claim as done. Additive and reversible.
--   * It does NOT impose a 3-agent ceremony on every task. Three ROLES, one hard rule: the
--     ACCEPTOR IS NEVER THE DOER. Same body accepting its own work is recorded as SELF-ACCEPTED
--     and is NOT done — because an auditor who is the builder inherits the builder's blind spot.
--
-- Expected reading on the day it lands: 31 of 60 rows say status='done', and every one of them
--   will read CLAIMED-UNACCEPTED, because none was ever accepted by anyone. That is not a bug in
--   this view — it is the measurement, the same way invariant_registry read 34/34 UNEXERCISED.
--
-- APPLY: scripts/apply_migration.py 048 --silo tscuymavysscrvoberrr (historical
-- applier: apply_lane_task_acceptance.py, deleted 2026-09-05 PR #89). NEVER `supabase db push`
--   (decision 962). Target: the SUBSTRATE coordination-plane DB, not any client silo.

ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS acceptance_criteria text;
ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS planner              text;
ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS accepted_by          text;
ALTER TABLE lane_tasks ADD COLUMN IF NOT EXISTS accepted_at          timestamptz;

COMMENT ON COLUMN lane_tasks.acceptance_criteria IS
    'What DONE means for this task, written by the planner BEFORE the work starts. Criteria '
    'written afterwards describe what was built, not what was required. A task with no criteria '
    'cannot be meaningfully accepted -- the view flags it (no_criteria), it does not pretend.';
COMMENT ON COLUMN lane_tasks.planner IS
    'Who owns acceptance for this task. Not necessarily who created the row.';
COMMENT ON COLUMN lane_tasks.accepted_by IS
    'The body that ACCEPTED the work against acceptance_criteria. MUST NOT be the doer: a lane '
    'accepting its own task is recorded SELF-ACCEPTED and does NOT count as done (an auditor who '
    'is the builder inherits the builder''s blind spot). Never hand-set to close a row.';
COMMENT ON COLUMN lane_tasks.accepted_at IS
    'When acceptance happened. NULL means NOT ACCEPTED -- and status=''done'' alone is only a '
    'CLAIM (CAI-RESP-986: never green on absence-of-signal). Judge completion via '
    'lane_tasks_state.is_truly_done, never via status alone.';

CREATE OR REPLACE VIEW lane_tasks_state AS
SELECT
    -- Columns enumerated explicitly, never `t.*`: with `t.*` a future ADD COLUMN re-expands into
    -- the MIDDLE of the output list and CREATE OR REPLACE VIEW (append-only) then FAILS, making
    -- this migration un-re-appliable. Confirmed empirically on 047.
    t.id,
    t.lane,
    t.title,
    t.detail,
    t.priority_rank,
    t.status,
    t.source_msg_id,
    t.created_at,
    t.updated_at,
    t.started_at,
    t.sla_minutes,
    t.sla_breached_at,
    t.acceptance_criteria,
    t.planner,
    t.accepted_by,
    t.accepted_at,
    CASE
        WHEN t.status <> 'done'                        THEN 'OPEN'
        -- Claimed done, nobody accepted it. The default state of every legacy row, and the
        -- honest reading of "the doer said so".
        WHEN t.accepted_at IS NULL                     THEN 'CLAIMED-UNACCEPTED'
        -- cc-quality Q3: a timestamp with no acceptor is acceptance by NOBODY. Named rather than
        -- folded into CLAIMED-UNACCEPTED, because a row that looks acted-on is more misleading
        -- than one that was never touched.
        WHEN t.accepted_by IS NULL                     THEN 'ACCEPTED-BY-NOBODY'
        -- Accepted by the body that did it. Recorded, NEVER counted.
        WHEN n.self_accepted                           THEN 'SELF-ACCEPTED'
        ELSE 'ACCEPTED'
    END AS completion_state,
    -- Affirmative boolean: green requires a TRUE here, never the absence of a flag. Built from
    -- the SAME single-sourced terms as the label above so the two cannot be tuned apart.
    (
        t.status = 'done'
        AND t.accepted_at IS NOT NULL
        AND t.accepted_by IS NOT NULL
        AND NOT n.self_accepted
    ) AS is_truly_done,
    -- The analogue of 047's DECLARED-SILENT: a task whose "done" was never DEFINED. Surfaced as
    -- its own signal rather than left to be inferred from a NULL, because the tasks nobody wrote
    -- criteria for are exactly the ones whose completion nobody can check.
    (t.acceptance_criteria IS NULL OR btrim(t.acceptance_criteria) = '') AS no_criteria,
    n.acceptor_norm,
    -- APPENDED AT THE END DELIBERATELY. `CREATE OR REPLACE VIEW` is append-only: putting this
    -- new column mid-list threw `cannot change name of view column "no_criteria" to
    -- "self_accepted"` on re-apply. That is cc-quality's F3 on 047 -- the same constraint, hit
    -- live while fixing a different finding from the same reviewer.
    --
    -- EXPOSED so the apply-script guard CONSUMES this definition instead of RE-IMPLEMENTING it.
    -- The sharpest half of F-CRIT was not the bypass but that post-condition (1) carried its own
    -- copy of the normalisation and so shared the identical blind spot: a guard structurally
    -- incapable of catching the bug it exists to catch. One definition, referenced twice.
    n.self_accepted
FROM lane_tasks t
-- Self-acceptance is decided ONCE here and referenced by every arm above.
-- Normalisation: agent ids are `cc-<lane>` for lane bodies (cc-quality -> quality), and the
-- console body `orch-console` is the `orchestrator` lane. Anything else compares as-is.
CROSS JOIN LATERAL (
    SELECT
        norm.v AS acceptor_norm,
        -- cc-quality F-CRIT, and it was the common case rather than an edge: the first cut
        -- EXACT-matched the bare lane, but EVERY real agent id is SUFFIXED --
        -- cc-shipforge-1, cc-ihsanos-qa-1, cc-irsyad-coord-1 -- which normalise to
        -- `<lane>-<suffix>` and therefore never equalled `<lane>`. Measured against the 22 live
        -- agent ids: 9 real lane-doers could self-accept UNDETECTED, so the gate was effectively
        -- OFF for every multi-instance lane, failing in the one direction it must never fail
        -- (the mirror of 047, where EXERCISED was unreachable -- same class, opposite sign).
        -- COMPONENT match, not exact. It is longest-prefix on a '-' boundary, so cosem-video-1
        -- can never match lane cosem-adcda, and it fails SAFE: an over-match reads SELF-ACCEPTED
        -- and BLOCKS, never silently certifies.
        -- GRANULARITY IS LANE-LEVEL, and that is a decision, not a fallout: cc-quality correctly
        -- escalated whether an in-lane QA body (cc-ihsanos-qa-1) should be allowed to accept its
        -- own lane's work. It should NOT. The auditor exists to not share the builder's blind
        -- spot, and a body inside the same lane shares its context, tooling and worktree. The
        -- evidence is this very review: cc-quality caught what cc-shipforge could not see about
        -- its own code, and caught the invented allowlist its author had re-read three times.
        -- Lane-level over-blocks (costs a message); agent-level would under-block (silently
        -- certifies unchecked work). Over-blocking is the affordable error.
        (
            norm.v IS NOT NULL
            AND (norm.v = t.lane OR norm.v LIKE t.lane || '-%')
        ) AS self_accepted
    FROM (
        SELECT CASE
            WHEN t.accepted_by IS NULL THEN NULL
            WHEN lower(t.accepted_by) = 'orch-console' THEN 'orchestrator'
            WHEN lower(t.accepted_by) LIKE 'cc-%' THEN substring(lower(t.accepted_by) from 4)
            ELSE lower(t.accepted_by)
        END AS v
    ) norm
) n;

COMMENT ON VIEW lane_tasks_state IS
    'CAI-RESP-986 applied to work items: DONE IS AN ACCEPTED CLAIM, NOT A SELF-DECLARED ONE. '
    'is_truly_done requires status=done AND an acceptance AND an acceptor who is not the doer. '
    'status=''done'' alone reads CLAIMED-UNACCEPTED; a lane accepting its own work reads '
    'SELF-ACCEPTED. Neither counts. Read THIS, not lane_tasks.status, anywhere completion is '
    'judged -- including any future lane-is-idle/wind-down decision, where a wrong ''done'' '
    'destroys work.';
