-- 052_audit_builder_independence.sql
-- CAI-RESP-989/990: the guard knew who DECIDED and not who BUILT — close the axis.
--
-- THE FAILURE THIS FIXES ACTUALLY HAPPENED, to the people who wrote the rule. cai appointed the
--   HUB (cc-orchestrator) as the second auditor of CAI-987, I argued FOR it in writing, and the
--   trigger ACCEPTED it. All three of us were wrong the same way:
--       decision_audit_actor_norm('orch-console')    -> 'orchestrator'   (the BUILDER: me)
--       decision_audit_actor_norm('cc-orchestrator') -> 'orchestrator'   (the "independent" auditor)
--       decision_audit_conflict('cc-orchestrator','cai')          -> FALSE  (not the decider — passes)
--       decision_audit_conflict('cc-orchestrator','orch-console') -> TRUE   (IS the builder — nobody asked)
--   The guard passed it because it only ever compared the auditor against `decided_by`. A second
--   auditor sitting in the BUILDER's lane inherits the builder's blind spot, which is the precise
--   thing the second auditor exists to not do. cc-quality caught it in one pass.
--
-- WHY A BODY-DIFFERENCE FELT LIKE INDEPENDENCE AND ISN'T: PR #75 already settled that the unit of
--   independence is the LANE, not the process -- a body in the same lane shares context, tooling
--   and worktree. I had that rule, wrote it, and still reasoned from "different body" an hour
--   later. The rule now lives in the trigger instead of in whoever is paying attention.
--
-- THE DESIGN, cai's steer in CAI-RESP-990, and the reasoning is worth keeping because it is a
-- general principle rather than a one-off:
--   * BUILDER = COALESCE(strategic_decisions.built_by, decision_audits.assigned_by).
--     `assigned_by` is a FREE, ALWAYS-PRESENT FLOOR. It is not merely convenient -- in the real
--     CAI-988 failure the builder WAS the assigner (me), so the floor CATCHES the actual case,
--     whereas an explicit-only `built_by` would have been NULL and slept straight through it.
--   * `built_by` is an OPTIONAL override on top of that floor, not a required field.
--     cai's principle, stated so future-us does not read it as inconsistent with her
--     tier-at-creation ruling: MANDATORY-VS-OPTIONAL TURNS ON WHETHER A SAFE NON-NULL FLOOR
--     EXISTS. `audit_tier` had no safe floor (untiered defaults to useless), so it is mandatory.
--     The builder fact has one, so an optional override on a real floor is fine.
--   * IT BLOCKS, it does not merely record. A recorded-but-permitted conflict is not a control --
--     it is a note. Over-blocking costs a message and has an escape valve (`built_by`);
--     under-blocking silently certifies work by someone who shares the builder's blind spot.
--
-- FAILS SAFE IN BOTH THE OBVIOUS DIRECTIONS: the floor is never NULL (assigned_by is NOT NULL),
--   so the builder axis can never silently switch itself off the way the decider axis could
--   before 050's F1 fix. And an over-match REFUSES an assignment, never accepts one.
--
-- ⚠ THIS IS DELIBERATELY *NOT* FOLDED INTO 049. cai ruled it FULL-tier and independently audited:
--   049 is the mechanism, and this is the rule that governs the mechanism's own author. Editing
--   049 in place would have moved the target under two live auditors and let me quietly widen the
--   rule that constrains me.
--
-- APPLY: direct psycopg only -- scripts/apply_audit_builder_independence.py.

ALTER TABLE strategic_decisions ADD COLUMN IF NOT EXISTS built_by text;

COMMENT ON COLUMN strategic_decisions.built_by IS
    'Who IMPLEMENTED this decision, when that is not the same body that assigned its audit. '
    'OPTIONAL override on a floor, never a required field: the effective builder is '
    'COALESCE(built_by, decision_audits.assigned_by), and assigned_by always exists -- so leaving '
    'this NULL degrades to a real, usually-correct answer rather than to no answer. Set it when '
    'the assigner is NOT the builder (e.g. a coordinator routes an audit of someone else''s '
    'work), which is exactly the case the floor gets wrong.';

-- ---------------------------------------------------------------------------------------------
-- The effective-builder rule: ONE definition, consumed by the trigger AND the view.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.decision_audit_effective_builder(
    p_built_by text, p_assigned_by text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT COALESCE(NULLIF(btrim(COALESCE(p_built_by, '')), ''), p_assigned_by)
$$;

COMMENT ON FUNCTION public.decision_audit_effective_builder(text, text) IS
    'CAI-RESP-990: explicit built_by if set, else the assigner as a free non-null floor. ONE '
    'definition, referenced by the guard and the board -- the PR #75 lesson was that a guard '
    'carrying its own second copy of a rule is blind in exactly the same spot as the rule.';

-- ---------------------------------------------------------------------------------------------
-- The guard, now covering BOTH axes.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.enforce_decision_audit_not_self()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_decided_by text;
    v_built_by   text;
    v_builder    text;
    v_found      boolean;
BEGIN
    SELECT decided_by, built_by, true INTO v_decided_by, v_built_by, v_found
      FROM strategic_decisions
     WHERE decision_ref = NEW.decision_ref;

    -- FAIL CLOSED on a missing/blank decider (050 F1, cc-quality): decision_audit_conflict()
    -- answers FALSE when either side is NULL, so without this the guard silently PERMITTED.
    IF NOT COALESCE(v_found, false) THEN
        RAISE EXCEPTION 'cannot assign an audit for unknown decision_ref %', NEW.decision_ref
            USING HINT = 'The decision must exist before it can be audited.';
    END IF;
    IF v_decided_by IS NULL OR btrim(v_decided_by) = '' THEN
        RAISE EXCEPTION 'cannot audit %: it has no decided_by, so auditor!=decider is unprovable',
            NEW.decision_ref
            USING HINT = 'An unprovable separation is not a separation. Set decided_by first.';
    END IF;

    -- AXIS 1 — AUDITOR != DECIDER (CAI-RESP-987 guard 1).
    IF decision_audit_conflict(NEW.auditor_agent, v_decided_by) THEN
        RAISE EXCEPTION
            'auditor % may not audit %: the decider is % (auditor and decider are the same lane)',
            NEW.auditor_agent, NEW.decision_ref, v_decided_by
            USING HINT = 'CAI-RESP-987 guard 1: AUDITOR != DECIDER. Assign a different body.';
    END IF;

    -- AXIS 2 — AUDITOR != BUILDER (CAI-RESP-989/990). This is the axis that let the hub through.
    v_builder := decision_audit_effective_builder(v_built_by, NEW.assigned_by);
    IF decision_audit_conflict(NEW.auditor_agent, v_builder) THEN
        RAISE EXCEPTION
            'auditor % may not audit %: the builder is % (auditor and builder are the same lane)',
            NEW.auditor_agent, NEW.decision_ref, v_builder
            USING HINT = 'CAI-RESP-989: a second auditor must be lane-independent of the DECIDER '
                         'AND the BUILDER. If the assigner is not the builder, set '
                         'strategic_decisions.built_by to the real builder.';
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- Board: append-only, new column at the END (048 threw `cannot change name of view column` live).
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
    -- APPENDED (052). Exposed so anyone can SEE which body the builder axis is being judged
    -- against, rather than having to re-derive the COALESCE. NULL when no audit is assigned.
    sd.built_by,
    a.effective_builders
FROM strategic_decisions sd
CROSS JOIN LATERAL (
    SELECT
        count(*)                                                   AS n_assigned,
        count(*) FILTER (WHERE da.completed_at IS NULL)             AS n_open,
        count(*) FILTER (WHERE da.verdict = 'accepted')             AS n_accepted,
        count(*) FILTER (WHERE da.verdict = 'rejected')             AS n_rejected,
        count(*) FILTER (WHERE da.verdict = 'could_not_verify')     AS n_could_not_verify,
        array_remove(array_agg(da.auditor_agent ORDER BY da.assigned_at), NULL) AS auditors,
        count(*) FILTER (
            WHERE da.completed_at IS NULL
              AND da.assigned_at < now() - make_interval(hours => da.sla_hours)
        )                                                          AS n_stale,
        count(*) FILTER (WHERE da.escalated_at IS NOT NULL)         AS n_escalated,
        round(EXTRACT(EPOCH FROM (now() - min(da.assigned_at) FILTER (WHERE da.completed_at IS NULL)))
              / 3600.0, 1)                                         AS oldest_open_hours,
        array_remove(array_agg(DISTINCT
            decision_audit_effective_builder(sd.built_by, da.assigned_by)), NULL)
                                                                   AS effective_builders
      FROM decision_audits da
     WHERE da.decision_ref = sd.decision_ref
) a
WHERE COALESCE(sd.is_test, false) = false;

COMMENT ON VIEW decision_audit_state IS
    'CAI-RESP-987/988/989: what actually happened to each decision, as opposed to what its status '
    'claims. CLOSED-ON-SILENCE is the honest name for accepted_by_timeout. COULD-NOT-VERIFY is '
    'its own state and is not a pass. AUDIT-STALE is computed from the clock, so it stays true '
    'even if the escalator dies. security_invoker=on (051). effective_builders shows which body '
    'the auditor!=BUILDER axis is judged against (052). Read is_audit_closed, never '
    'challenge_status alone.';
