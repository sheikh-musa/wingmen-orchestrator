-- 053_audit_unresolved_and_lens.sql
-- cc-quality's REJECTION of CAI-RESP-987/988 as built. Its top finding, and one of its MEDIUMs.
--
-- IT REJECTED THE BUILD, NOT THE RULINGS, and it said so explicitly: "I am NOT rejecting cai's
--   ruling... I am rejecting the claim that 049 as applied implements CAI-987's intent AND its
--   four binding guards." First real use of the mechanism on its own creation, and it worked.
--
-- ---------------------------------------------------------------------------------------------
-- F3 (its top finding, HIGH, and it had a LIVE instance sitting in it when filed)
-- ---------------------------------------------------------------------------------------------
-- `escalate_stale_decision_audits` filtered on `completed_at IS NULL`. But a verdict of
--   `could_not_verify` or `rejected` SETS completed_at -- the audit is COMPLETE, it is merely
--   UNRESOLVED. So the backstop could never see the two outcomes that actually block a close.
--
-- The state that creates: `decision_audit_required()` returns TRUE for a FULL-tier row, so
--   * the timeout enforcer skips it forever      (correct),
--   * close_decision_by_audit refuses it forever (correct),
--   * and NOTHING escalates or re-surfaces it    (the hole).
--   No timer, no backstop, no sink. It just sits there.
--
-- LIVE INSTANCE AT FILING TIME: **CAI-RESP-985 -- the money decision, tier FULL** -- had been in
--   exactly that state since cc-quality filed `could_not_verify` on it. Board read
--   COULD-NOT-VERIFY with n_stale=0, n_escalated=0.
--
-- CAI-RESP-988 §3's binding words were "it never silently accumulates and never silently
--   auto-closes". The auto-close half was honoured rigorously. The ACCUMULATE half was not, and
--   cc-quality named the shape precisely: this is the SAME CLASS of defect the backstop exists to
--   prevent, arrived at from the other side -- the first version watched for the ABSENCE OF A
--   LOOK and missed A LOOK THAT CAME BACK INCONCLUSIVE. An unresolved verdict is not a resolved
--   one, and only a human/agent decision can move it.
--
-- THE FIX, its recommendation and it is the right one: escalate on NOT-RESOLVED rather than on
--   NO-VERDICT-YET, and track resolution SEPARATELY so that cai's ACTION clears the item rather
--   than the VERDICT clearing it. A verdict that clears its own escalation would be the auditor
--   silencing the alarm by answering the door.
--
-- ---------------------------------------------------------------------------------------------
-- F5 (MEDIUM): the complementary split existed only in prose.
-- ---------------------------------------------------------------------------------------------
-- CAI-RESP-988 §1 assigned two auditors with DIFFERENT lenses (design fidelity vs implementation
--   correctness) on the stated basis that perspective diversity is "the stronger form". The
--   mechanism stored only the COUNT. Nothing recorded which lens an auditor took, nothing could
--   detect two auditors covering the same one, and a reader of a closed record could not tell
--   whether a decision got diverse review or the same review twice. Two auditors who both did
--   design fidelity satisfy "both cleared" while delivering one lens.
--
-- APPLY: direct psycopg only -- scripts/apply_audit_unresolved_and_lens.py.

ALTER TABLE decision_audits ADD COLUMN IF NOT EXISTS lens          text;
ALTER TABLE decision_audits ADD COLUMN IF NOT EXISTS resolved_at   timestamptz;
ALTER TABLE decision_audits ADD COLUMN IF NOT EXISTS resolved_by   text;
ALTER TABLE decision_audits ADD COLUMN IF NOT EXISTS resolution_note text;

COMMENT ON COLUMN decision_audits.lens IS
    'WHICH ANGLE this auditor was asked to take (e.g. governance-design-fidelity, '
    'implementation-correctness, grant-posture). Set at ASSIGNMENT, by the assigner. '
    'CAI-RESP-988 §1 justified two auditors on perspective DIVERSITY being the stronger form -- '
    'without this the mechanism stored only the count, so two auditors doing the same lens '
    'satisfied "both cleared" while delivering one look (cc-quality F5).';
COMMENT ON COLUMN decision_audits.resolved_at IS
    'When a NON-ACCEPTED verdict (could_not_verify / rejected) was ACTED ON -- re-assigned, '
    'answered, or superseded. Deliberately SEPARATE from completed_at: the auditor completes, '
    'somebody else resolves. The escalator watches THIS, so an unresolved could_not_verify keeps '
    'surfacing until a human/agent decision moves it (cc-quality F3). A verdict must never clear '
    'its own escalation -- that is the auditor silencing the alarm by answering the door.';
COMMENT ON COLUMN decision_audits.resolved_by IS 'Who resolved it. Never the auditor''s own act of judging.';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid='decision_audits'::regclass
                      AND conname='decision_audits_resolution_coherent') THEN
        ALTER TABLE decision_audits ADD CONSTRAINT decision_audits_resolution_coherent
            CHECK (resolved_at IS NULL OR (verdict IS NOT NULL AND resolved_by IS NOT NULL));
    END IF;
END $$;

-- ---------------------------------------------------------------------------------------------
-- ONE definition of "this audit still needs somebody" -- consumed by the escalator AND the board.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.decision_audit_unresolved(
    p_verdict text, p_completed_at timestamptz, p_resolved_at timestamptz)
RETURNS boolean
LANGUAGE sql IMMUTABLE
AS $$
    SELECT CASE
        -- Never looked at yet.
        WHEN p_completed_at IS NULL THEN true
        -- Looked at, came back inconclusive or negative, and nobody has acted on it since.
        WHEN p_verdict IN ('could_not_verify', 'rejected') AND p_resolved_at IS NULL THEN true
        ELSE false
    END
$$;

COMMENT ON FUNCTION public.decision_audit_unresolved(text, timestamptz, timestamptz) IS
    'cc-quality F3: an audit needs somebody if it has NO verdict yet, OR if its verdict was '
    'could_not_verify/rejected and nobody has resolved it. The first version only knew the first '
    'case, so a look that came back inconclusive accumulated in silence -- the same defect class '
    'the backstop exists to prevent, from the other side. ONE definition, used by the escalator '
    'and the board so they cannot disagree about what is still owed.';

-- ---------------------------------------------------------------------------------------------
-- The escalator, now watching UNRESOLVED rather than merely UNVERDICTED.
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
    v_kind  text;
BEGIN
    FOR rec IN
        SELECT da.id, da.decision_ref, da.auditor_agent, da.assigned_at, da.completed_at,
               da.sla_hours, da.verdict, sd.title, sd.audit_tier
          FROM decision_audits da
          JOIN strategic_decisions sd ON sd.decision_ref = da.decision_ref
         WHERE decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at)
           AND da.escalated_at IS NULL
           AND COALESCE(sd.is_test, false) = false
           -- The clock starts when the auditor STOPPED being the blocker: at assignment for an
           -- un-started audit, at completion for one that came back unresolved.
           AND COALESCE(da.completed_at, da.assigned_at) < now() - make_interval(hours => da.sla_hours)
    LOOP
        v_hours := round(EXTRACT(EPOCH FROM (now() - COALESCE(rec.completed_at, rec.assigned_at))) / 3600.0, 1);
        v_kind  := CASE WHEN rec.completed_at IS NULL THEN 'NOT STARTED'
                        ELSE 'UNRESOLVED ' || upper(rec.verdict) END;

        INSERT INTO agent_messages
            (from_agent, to_agent, message_type, subject, body, requires_response, priority)
        VALUES (
            'substrate', 'cai', 'blocker',
            'STALE AUDIT (' || v_kind || '): ' || rec.decision_ref || ' — ' || rec.auditor_agent
                || ', ' || v_hours || 'h (SLA ' || rec.sla_hours || 'h)',
            'CAI-RESP-988 §4 backstop firing.' || E'\n\n'
                || 'Decision: ' || rec.decision_ref || ' — ' || COALESCE(rec.title, '(no title)') || E'\n'
                || 'Tier: ' || COALESCE(rec.audit_tier, 'UNTIERED') || E'\n'
                || 'Auditor: ' || rec.auditor_agent || E'\n'
                || 'State: ' || v_kind || ', ' || v_hours || 'h (SLA ' || rec.sla_hours || 'h)' || E'\n\n'
                || CASE WHEN rec.completed_at IS NULL
                        THEN 'Nobody has looked at this yet.'
                        ELSE 'It WAS looked at and came back ' || rec.verdict || '. That BLOCKS the '
                             || 'close and nothing else will move it -- the timeout enforcer '
                             || 'correctly skips it and close_decision_by_audit correctly refuses '
                             || 'it, so it will sit here until somebody acts.' END
                || E'\n\nResolve by re-assigning, answering the auditor''s findings, or superseding '
                || 'the decision, then set resolved_at/resolved_by on the audit row. '
                || E'\n\nNOTHING WAS AUTO-CLOSED and nothing will be.',
            true, 'P2'
        );

        UPDATE decision_audits SET escalated_at = now(), updated_at = now() WHERE id = rec.id;
        RETURN QUERY SELECT rec.decision_ref, rec.auditor_agent,
                            CASE WHEN rec.completed_at IS NULL THEN 'escalated_not_started'
                                 ELSE 'escalated_unresolved' END::TEXT;
    END LOOP;
END;
$function$;

COMMENT ON FUNCTION public.escalate_stale_decision_audits() IS
    'CAI-RESP-988 §4, widened per cc-quality F3. Escalates an audit that is UNRESOLVED -- no '
    'verdict yet, OR a could_not_verify/rejected nobody has acted on. Never closes a decision, '
    'never writes a verdict. The SLA clock starts when the auditor stopped being the blocker.';

-- ---------------------------------------------------------------------------------------------
-- Board: append-only, new columns at the END.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE VIEW decision_audit_state AS
SELECT
    sd.decision_ref, sd.title, sd.domain, sd.category, sd.decided_by, sd.decided_at,
    sd.challenge_status, sd.challengeable_until, sd.audit_tier,
    a.n_assigned, a.n_open, a.n_accepted, a.n_rejected, a.n_could_not_verify, a.auditors,
    decision_audit_required(sd.decision_ref) AS audit_required,
    CASE
        WHEN a.n_rejected > 0                              THEN 'AUDIT-REJECTED'
        WHEN a.n_could_not_verify > 0                      THEN 'COULD-NOT-VERIFY'
        WHEN a.n_stale > 0                                 THEN 'AUDIT-STALE'
        WHEN a.n_open > 0                                  THEN 'AUDIT-IN-FLIGHT'
        WHEN sd.challenge_status = 'accepted_by_audit'     THEN 'AUDITED-ACCEPTED'
        WHEN sd.challenge_status = 'accepted_by_timeout'   THEN 'CLOSED-ON-SILENCE'
        WHEN sd.audit_tier = 'FULL' AND a.n_assigned = 0    THEN 'AUDIT-OWED-NO-AUDITOR'
        WHEN sd.audit_tier = 'FULL'                        THEN 'AUDIT-OWED'
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
        AND a.n_accepted > 0 AND a.n_open = 0
        AND a.n_rejected = 0 AND a.n_could_not_verify = 0
    ) AS is_audit_closed,
    (sd.audit_tier IS NULL) AS untiered,
    a.n_stale, a.n_escalated, a.oldest_open_hours,
    decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning) AS tier_candidate,
    sd.built_by, a.effective_builders,
    -- APPENDED (053). n_unresolved is the honest "still needs somebody" count -- it includes a
    -- completed-but-inconclusive audit, which n_open cannot see. lenses makes the diversity
    -- claim inspectable rather than asserted.
    a.n_unresolved,
    a.lenses
FROM strategic_decisions sd
CROSS JOIN LATERAL (
    SELECT
        count(*)                                                   AS n_assigned,
        count(*) FILTER (WHERE da.completed_at IS NULL)             AS n_open,
        count(*) FILTER (WHERE da.verdict = 'accepted')             AS n_accepted,
        count(*) FILTER (WHERE da.verdict = 'rejected')             AS n_rejected,
        count(*) FILTER (WHERE da.verdict = 'could_not_verify')     AS n_could_not_verify,
        array_remove(array_agg(da.auditor_agent ORDER BY da.assigned_at), NULL) AS auditors,
        -- Stale now means "unresolved past SLA", measured from when the auditor stopped being
        -- the blocker. Computed from the CLOCK, so it stays true even if the escalator dies.
        count(*) FILTER (
            WHERE decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at)
              AND COALESCE(da.completed_at, da.assigned_at) < now() - make_interval(hours => da.sla_hours)
        )                                                          AS n_stale,
        count(*) FILTER (WHERE da.escalated_at IS NOT NULL)         AS n_escalated,
        round(EXTRACT(EPOCH FROM (now() - min(da.assigned_at) FILTER (WHERE da.completed_at IS NULL)))
              / 3600.0, 1)                                         AS oldest_open_hours,
        array_remove(array_agg(DISTINCT
            decision_audit_effective_builder(sd.built_by, da.assigned_by)), NULL) AS effective_builders,
        count(*) FILTER (
            WHERE decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at)
        )                                                          AS n_unresolved,
        array_remove(array_agg(DISTINCT da.lens), NULL)             AS lenses
      FROM decision_audits da
     WHERE da.decision_ref = sd.decision_ref
) a
WHERE COALESCE(sd.is_test, false) = false;

COMMENT ON VIEW decision_audit_state IS
    'CAI-RESP-987/988/989 + cc-quality''s rejection findings. CLOSED-ON-SILENCE is the honest name '
    'for accepted_by_timeout. COULD-NOT-VERIFY is its own state and is not a pass. n_unresolved '
    'counts audits that still need SOMEBODY, including completed-but-inconclusive ones that '
    'n_open cannot see. AUDIT-STALE is computed from the clock, so a dead escalator cannot make a '
    'cell read healthy. security_invoker=on (051). Read is_audit_closed, never challenge_status.';
