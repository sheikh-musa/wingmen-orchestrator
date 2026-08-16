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
-- APPLY: direct psycopg only — scripts/apply_lane_task_acceptance.py. NEVER `supabase db push`
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
        -- Accepted by the body that did it. Recorded, NEVER counted.
        WHEN n.self_accepted                           THEN 'SELF-ACCEPTED'
        ELSE 'ACCEPTED'
    END AS completion_state,
    -- Affirmative boolean: green requires a TRUE here, never the absence of a flag. Built from
    -- the SAME single-sourced terms as the label above so the two cannot be tuned apart.
    (
        t.status = 'done'
        AND t.accepted_at IS NOT NULL
        AND NOT n.self_accepted
    ) AS is_truly_done,
    -- The analogue of 047's DECLARED-SILENT: a task whose "done" was never DEFINED. Surfaced as
    -- its own signal rather than left to be inferred from a NULL, because the tasks nobody wrote
    -- criteria for are exactly the ones whose completion nobody can check.
    (t.acceptance_criteria IS NULL OR btrim(t.acceptance_criteria) = '') AS no_criteria,
    n.acceptor_norm
FROM lane_tasks t
-- Self-acceptance is decided ONCE here and referenced by every arm above.
-- Normalisation: agent ids are `cc-<lane>` for lane bodies (cc-quality -> quality), and the
-- console body `orch-console` is the `orchestrator` lane. Anything else compares as-is.
CROSS JOIN LATERAL (
    SELECT
        norm.v AS acceptor_norm,
        (norm.v IS NOT NULL AND norm.v = t.lane) AS self_accepted
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
