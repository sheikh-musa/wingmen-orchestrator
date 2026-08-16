-- 050_stale_audit_escalation.sql
-- CAI-RESP-988 §4: the backstop that stops migration 049 from rotting into its own failure mode.
--
-- cai's words: a stale-audit escalation is "the one way this mechanism could rot into its own
--   unexercised-control failure". 049 made FULL-tier decisions stop closing on a timeout. The
--   deliberate cost of that is ACCUMULATION -- an assigned audit nobody runs now holds its
--   decision open forever, silently. Silent accumulation is the same disease as silent closure
--   wearing the opposite coat: in both cases nothing is looking and nothing says so.
--
-- WHAT IT DOES, and what it deliberately does NOT do:
--   * An open assigned audit older than its SLA (default 24h) becomes VISIBLE -- `is_stale` on
--     the board -- and raises ONE bus row to cai, who can re-assign it.
--   * It NEVER auto-closes the decision and NEVER writes a verdict. An escalation that could
--     close things would reintroduce accepted_by_timeout under a new name.
--   * It escalates ONCE per audit row (escalated_at), so a stale audit does not spam the bus
--     daily. Re-assignment creates a NEW row with a fresh SLA, which is the intended path.
--
-- SLIGHTLY BROADER THAN CAI-RESP-988'S WORDING, NAMED RATHER THAN SLIPPED IN: cai wrote "a
--   FULL-tier audit with no verdict within its SLA". This escalates ANY open assigned audit past
--   SLA, not only FULL-tier ones. Reason: in 049 an ASSIGNMENT already blocks the timeout close
--   regardless of tier (decision_audit_required), so a stale audit on an untiered decision
--   accumulates exactly the same way and would be the one case the backstop misses. Widening a
--   safety escalation costs a message; narrowing it costs the thing it exists to catch. If cai
--   wants it narrowed to FULL, that is a one-line predicate.
--
-- SCHEDULED ON pg_cron, DELIBERATELY, AND VERIFIED FIRING. `nervous_system/pipeline_clock.py` is
--   correct code that has NO launchd job -- never scheduled, not stopped -- and that is why
--   bug_pipeline_readiness read 10/10 green off one seed INSERT for 39 days. A backstop that is
--   never scheduled is worse than no backstop, because the board implies it is watching. This
--   rides pg_cron, the same mechanism already proven live for the window enforcer (jobid 7), and
--   the apply script asserts the job row EXISTS and is ACTIVE rather than trusting the schedule.
--
-- ALSO CARRIES cc-quality's THREE FINDINGS from its PR #76 review (#23757), which cleared the
-- mechanism but named three things that decide whether it audits anything at all:
--
--   F1 THE GUARD FAILED *OPEN* ON A MISSING DECIDER, which is the one direction I claimed it
--      never fails. `enforce_decision_audit_not_self` did `SELECT decided_by INTO v_decided_by`
--      and then relied on decision_audit_conflict(), which returns FALSE when either side is
--      NULL -- so a decision whose row is absent or whose decided_by is NULL was auditable BY
--      ANYONE, including its own author. Not exploitable today (decided_by is NOT NULL and the
--      FK rejects an unknown ref), so this is depth, not a live hole -- but "not reachable today"
--      is exactly the argument that made 047's EXERCISED unreachable and CAI-978's control
--      unexercised. Now RAISES instead. A guard whose safe answer depends on a NOT NULL
--      constraint two tables away is not a guard, it is a coincidence.
--
--   F2 A FULL-TIER DECISION WITH NO AUDITOR ASSIGNED never closes and nothing says so -- correct
--      fail-safe, silent accumulation. Given its own board state, AUDIT-OWED-NO-AUDITOR, because
--      "nobody was ever asked" and "somebody was asked and has not answered" need different
--      actions and only one of them has a name to chase.
--
--   F3 THE COMPLETENESS-LINT, and it is the finding that decides whether any of this matters:
--      audit_tier reads 1351 NULL / 2 FULL, so the mechanism routes 2 of 1353 decisions into
--      audit and the other 1351 take the passive window. That is the same 99.85%-NULL
--      classification trap cc-quality named on lane_tasks #60. Its synthesis is better than
--      either of our positions: I was right to refuse the keyword pass as the GATE (88%
--      precision, it false-tiers CAI-986 as money), but wrong to discard it entirely -- run it as
--      a CANDIDATE DETECTOR. Explicit tier stays the authority; the keyword pass just means an
--      untiered money/residency decision cannot SILENTLY take the passive window.
--      HONEST LIMIT, unchanged from my measurement: the detector's recall is also poor (it misses
--      CAI-924, CAI-732, CAI-854). It is a floor, not a proof, and a clean board here does NOT
--      mean nothing was missed.
--
-- APPLY: direct psycopg only -- scripts/apply_stale_audit_escalation.py. NEVER `supabase db push`.

ALTER TABLE decision_audits ADD COLUMN IF NOT EXISTS sla_hours    integer NOT NULL DEFAULT 24;
ALTER TABLE decision_audits ADD COLUMN IF NOT EXISTS escalated_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'decision_audits'::regclass
                      AND conname  = 'decision_audits_sla_positive') THEN
        ALTER TABLE decision_audits
            ADD CONSTRAINT decision_audits_sla_positive CHECK (sla_hours > 0);
    END IF;
END $$;

COMMENT ON COLUMN decision_audits.sla_hours IS
    'How long this audit may sit open before it escalates. Per-audit and tunable -- a full '
    'at-source money audit may legitimately need longer than a light look, and a fixed global '
    'number would either rush the deep ones or hide the shallow ones.';
COMMENT ON COLUMN decision_audits.escalated_at IS
    'When this audit was escalated as stale. Set ONCE, so a neglected audit does not spam the bus '
    'daily. NULL is not "healthy" -- read is_stale on decision_audit_state, which is computed '
    'from the clock rather than from whether anything happened to fire.';

-- ---------------------------------------------------------------------------------------------
-- F1 (cc-quality #23757): the self-audit guard now FAILS CLOSED on a missing/NULL decider.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.enforce_decision_audit_not_self()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_decided_by text;
    v_found      boolean;
BEGIN
    SELECT decided_by, true INTO v_decided_by, v_found
      FROM strategic_decisions
     WHERE decision_ref = NEW.decision_ref;

    -- FAIL CLOSED. decision_audit_conflict() answers FALSE when either side is NULL, so without
    -- this the absent-decision and NULL-decider cases were silently PERMITTED -- the guard's only
    -- fail-open direction, and the whole point of the guard is that it fails toward refusing.
    IF NOT COALESCE(v_found, false) THEN
        RAISE EXCEPTION 'cannot assign an audit for unknown decision_ref %', NEW.decision_ref
            USING HINT = 'The decision must exist before it can be audited.';
    END IF;
    IF v_decided_by IS NULL OR btrim(v_decided_by) = '' THEN
        RAISE EXCEPTION 'cannot audit %: it has no decided_by, so auditor!=decider is unprovable',
            NEW.decision_ref
            USING HINT = 'An unprovable separation is not a separation. Set decided_by first.';
    END IF;

    IF decision_audit_conflict(NEW.auditor_agent, v_decided_by) THEN
        RAISE EXCEPTION
            'auditor % may not audit %: the decider is % (auditor and decider are the same lane)',
            NEW.auditor_agent, NEW.decision_ref, v_decided_by
            USING HINT = 'CAI-RESP-987 guard 1: AUDITOR != DECIDER. Assign a different body.';
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- F3 (cc-quality #23757): the completeness-lint. CANDIDATE DETECTOR, never the gate.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.decision_audit_tier_candidate(
    p_title text, p_decision text, p_reasoning text)
RETURNS boolean
LANGUAGE sql IMMUTABLE
AS $$
    -- Money / residency vocabulary. MEASURED, and its numbers are why it is a detector and not a
    -- gate: 449/508 (88%) of the last 30 days match, and it still misses CAI-924, CAI-732,
    -- CAI-854. As a GATE that is useless. As a "you have not tiered this and it smells like
    -- money" flag it is worth exactly what it costs -- an untiered money decision cannot slip
    -- into the passive window unremarked.
    -- NOTE \y, not \b: Postgres regex uses \y for a word boundary. My first pass wrote \b and
    -- silently failed to match 'PII' at all -- a measurement bug that would have understated the
    -- candidate set.
    SELECT coalesce(p_title,'') || ' ' || coalesce(p_decision,'') || ' ' || coalesce(p_reasoning,'')
           ~* ('(money|payment|payout|invoice|billing|refund|pricing|riba|tabung|donation|donor'
               || '|revenue|wallet|stripe|nets|paynow|residency|tenant|silo|pdpa|\ypii\y'
               || '|personal data|emirates.id|commingl|cross.tenant)')
$$;

COMMENT ON FUNCTION public.decision_audit_tier_candidate(text, text, text) IS
    'cc-quality #23757 completeness-lint: does this decision LOOK like money/residency? A '
    'CANDIDATE DETECTOR, never the tier itself -- the explicit audit_tier is the authority. '
    'Measured precision is poor (88% match rate) and so is recall (misses CAI-924/732/854), so a '
    'clean board here does NOT mean nothing was missed. It exists so an untiered money decision '
    'cannot silently take the passive window.';

-- ---------------------------------------------------------------------------------------------
-- The escalator. Returns what it did, so a caller can never conclude "it worked" from silence.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.escalate_stale_decision_audits()
RETURNS TABLE(decision_ref text, auditor_agent text, action text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
DECLARE
    rec RECORD;
    v_hours numeric;
BEGIN
    FOR rec IN
        SELECT da.id, da.decision_ref, da.auditor_agent, da.assigned_at, da.sla_hours,
               sd.title, sd.audit_tier
          FROM decision_audits da
          JOIN strategic_decisions sd ON sd.decision_ref = da.decision_ref
         WHERE da.completed_at IS NULL
           AND da.escalated_at IS NULL
           AND da.assigned_at < now() - make_interval(hours => da.sla_hours)
           AND COALESCE(sd.is_test, false) = false
    LOOP
        v_hours := round(EXTRACT(EPOCH FROM (now() - rec.assigned_at)) / 3600.0, 1);

        INSERT INTO agent_messages
            (from_agent, to_agent, message_type, subject, body, requires_response, priority)
        VALUES (
            'substrate', 'cai', 'blocker',
            'STALE AUDIT: ' || rec.decision_ref || ' has sat with ' || rec.auditor_agent
                || ' for ' || v_hours || 'h (SLA ' || rec.sla_hours || 'h) — re-assign or extend',
            'CAI-RESP-988 §4 backstop firing.' || E'\n\n'
                || 'Decision: ' || rec.decision_ref || ' — ' || COALESCE(rec.title, '(no title)') || E'\n'
                || 'Tier: ' || COALESCE(rec.audit_tier, 'UNTIERED') || E'\n'
                || 'Auditor: ' || rec.auditor_agent || ', assigned ' || rec.assigned_at
                || ' (' || v_hours || 'h ago, SLA ' || rec.sla_hours || 'h)' || E'\n\n'
                || 'This decision is NOT closing on its own -- 049 stops the passive timeout for '
                || 'anything that owes an audit, which is deliberate. That means it sits here '
                || 'until someone acts. Re-assign to another body, extend sla_hours, or complete '
                || 'the audit.' || E'\n\n'
                || 'NOTHING WAS AUTO-CLOSED and nothing will be. This is a visibility backstop, '
                || 'not an escape hatch -- an escalation that could close things would be '
                || 'accepted_by_timeout under a new name.',
            true, 'P2'
        );

        UPDATE decision_audits SET escalated_at = now(), updated_at = now() WHERE id = rec.id;

        RETURN QUERY SELECT rec.decision_ref, rec.auditor_agent, 'escalated'::TEXT;
    END LOOP;
END;
$function$;

COMMENT ON FUNCTION public.escalate_stale_decision_audits() IS
    'CAI-RESP-988 §4. Makes a neglected audit VISIBLE and raises one bus row to cai. Never closes '
    'a decision, never writes a verdict. Scheduled on pg_cron -- see the apply script, which '
    'asserts the job exists and is active rather than trusting that somebody scheduled it.';

-- ---------------------------------------------------------------------------------------------
-- Board: append-only. New columns go at the END -- CREATE OR REPLACE VIEW cannot rename or
-- reorder existing output columns, which threw `cannot change name of view column` live on 048.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE VIEW decision_audit_state AS
SELECT
    sd.decision_ref,
    sd.title,
    sd.domain,
    sd.category,
    sd.decided_by,
    sd.decided_at,
    sd.challenge_status,
    sd.challengeable_until,
    sd.audit_tier,
    a.n_assigned,
    a.n_open,
    a.n_accepted,
    a.n_rejected,
    a.n_could_not_verify,
    a.auditors,
    decision_audit_required(sd.decision_ref) AS audit_required,
    CASE
        WHEN a.n_rejected > 0                              THEN 'AUDIT-REJECTED'
        WHEN a.n_could_not_verify > 0                      THEN 'COULD-NOT-VERIFY'
        -- Stale is checked BEFORE plain in-flight: an audit nobody has touched in over a day is
        -- not "in progress", and rendering it as in-progress is the absence-of-signal read.
        WHEN a.n_stale > 0                                 THEN 'AUDIT-STALE'
        WHEN a.n_open > 0                                  THEN 'AUDIT-IN-FLIGHT'
        WHEN sd.challenge_status = 'accepted_by_audit'     THEN 'AUDITED-ACCEPTED'
        WHEN sd.challenge_status = 'accepted_by_timeout'   THEN 'CLOSED-ON-SILENCE'
        -- cc-quality F2: tiered FULL and nobody was ever ASKED. Distinct from AUDIT-OWED, which
        -- means somebody was asked and has not answered. Different actions, and only one of them
        -- has a name to chase -- folding them together is how the first one waits forever.
        WHEN sd.audit_tier = 'FULL' AND a.n_assigned = 0    THEN 'AUDIT-OWED-NO-AUDITOR'
        WHEN sd.audit_tier = 'FULL'                        THEN 'AUDIT-OWED'
        -- cc-quality F3 completeness-lint: still open, nobody tiered it, and it reads like
        -- money/residency. NOT a claim that it IS money -- a claim that nobody has decided, on a
        -- decision where that omission would matter. Ranked ABOVE plain WINDOW-OPEN on purpose:
        -- the whole failure being replaced is a money decision quietly riding a clock.
        WHEN sd.audit_tier IS NULL
             AND sd.challenge_status IN ('challenge_window', 'unchallenged')
             AND decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning)
                                                           THEN 'UNTIERED-CANDIDATE'
        WHEN sd.challenge_status = 'challenge_window'      THEN 'WINDOW-OPEN'
        WHEN sd.audit_tier = 'NONE'                        THEN 'AUDIT-NOT-REQUIRED'
        WHEN sd.challenge_status IN ('accepted', 'implemented', 'superseded', 'informational',
                                     'overridden', 'challenged', 'cai_review_requested')
                                                           THEN 'CLOSED-OTHER'
        ELSE 'UNTIERED'
    END AS audit_state,
    (
        sd.challenge_status = 'accepted_by_audit'
        AND a.n_accepted > 0
        AND a.n_open = 0
        AND a.n_rejected = 0
        AND a.n_could_not_verify = 0
    ) AS is_audit_closed,
    (sd.audit_tier IS NULL) AS untiered,
    -- APPENDED AT THE END (CREATE OR REPLACE VIEW is append-only; 048 hit this live).
    a.n_stale,
    a.n_escalated,
    a.oldest_open_hours,
    -- Exposed as its own column so the lint can be COUNTED and watched, not just read off a
    -- label -- and so a consumer can act on "untiered but smells like money" without re-deriving
    -- the rule. One definition, referenced twice (the 048 F-CRIT lesson).
    decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning) AS tier_candidate
FROM strategic_decisions sd
CROSS JOIN LATERAL (
    SELECT
        count(*)                                                   AS n_assigned,
        count(*) FILTER (WHERE da.completed_at IS NULL)             AS n_open,
        count(*) FILTER (WHERE da.verdict = 'accepted')             AS n_accepted,
        count(*) FILTER (WHERE da.verdict = 'rejected')             AS n_rejected,
        count(*) FILTER (WHERE da.verdict = 'could_not_verify')     AS n_could_not_verify,
        array_remove(array_agg(da.auditor_agent ORDER BY da.assigned_at), NULL) AS auditors,
        -- Computed from the CLOCK, never from whether the escalator happened to run. If the cron
        -- job dies, the board still reads AUDIT-STALE -- the measurer being dead must not make
        -- the cell look healthy (CAI-RESP-986 §1, and the whole pipeline_clock lesson).
        count(*) FILTER (
            WHERE da.completed_at IS NULL
              AND da.assigned_at < now() - make_interval(hours => da.sla_hours)
        )                                                          AS n_stale,
        count(*) FILTER (WHERE da.escalated_at IS NOT NULL)         AS n_escalated,
        round(EXTRACT(EPOCH FROM (now() - min(da.assigned_at) FILTER (WHERE da.completed_at IS NULL)))
              / 3600.0, 1)                                         AS oldest_open_hours
      FROM decision_audits da
     WHERE da.decision_ref = sd.decision_ref
) a
WHERE COALESCE(sd.is_test, false) = false;

-- ---------------------------------------------------------------------------------------------
-- SCHEDULE IT. This is the half that pipeline_clock.py never got, and the reason a readiness
-- board read 10/10 green off one seed INSERT for 39 days. pg_cron is used deliberately over
-- launchd: the window enforcer (jobid 7) already proves the mechanism fires here, and a job row
-- in cron.job is verifiable from the same connection that applies the migration.
-- Hourly, not every 5 minutes: this measures a 24h SLA, so a finer schedule buys nothing.
-- ---------------------------------------------------------------------------------------------

SELECT cron.schedule('escalate-stale-decision-audits', '0 * * * *',
                     'SELECT escalate_stale_decision_audits();');

COMMENT ON VIEW decision_audit_state IS
    'CAI-RESP-987/988: what actually happened to each decision, as opposed to what its status '
    'claims. CLOSED-ON-SILENCE is the honest name for accepted_by_timeout. COULD-NOT-VERIFY is '
    'its own state and is not a pass. AUDIT-STALE is computed from the clock, so it stays true '
    'even if the escalator dies -- a dead measurer must never make a cell read healthy. UNTIERED '
    'means nobody judged whether an audit was needed. Read is_audit_closed, never '
    'challenge_status alone.';
