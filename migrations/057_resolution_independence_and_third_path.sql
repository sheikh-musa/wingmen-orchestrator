-- 057_resolution_independence_and_third_path.sql
-- cc-quality N1 (HIGH, proven) + N2 (MEDIUM-HIGH, proven), both found INSIDE the 053 fix.
--
-- N1 -- THE AUDITOR COULD SILENCE ITS OWN ALARM, AND I HAD WRITTEN THE RULE TWICE IN PROSE.
--   cc-quality proved it: `UPDATE decision_audits SET resolved_at=now(), resolved_by='cc-quality'`
--   on its OWN rejected row succeeded, and decision_audit_unresolved went FALSE immediately. The
--   escalation built one hour earlier to surface unresolved verdicts could be switched off by the
--   body it was watching.
--   THE PART THAT IS MINE TO OWN: I wrote the principle into TWO column comments --
--     resolved_at: "A verdict must never clear its own escalation -- that is the auditor
--                   silencing the alarm by answering the door"
--     resolved_by: "Who resolved it. Never the auditor's own act of judging."
--   -- and enforced NEITHER. `decision_audits_resolution_coherent` required resolved_by NOT NULL
--   and nothing more. That is the same defect as the lens-in-prose and the 40-char floor, in the
--   same migration, by the same author, one hour after describing it. A control that lives in a
--   comment is a sentence, and the machinery to enforce it -- decision_audit_conflict() -- was
--   already sitting in the file.
--   cc-quality deliberately did NOT set resolved_at on its own rows, having proven it could.
--
-- N2 -- THE THIRD ACCUMULATION PATH. 053 closed "the look came back inconclusive". This is "the
--   look came back FINE and nobody pulled the lever": every audit accepted, close_decision_by_audit
--   never called. Board reads AUDIT-OWED with n_open=0, n_unresolved=0, n_stale=0, and the
--   escalator sees ZERO. cc-quality's judgement, and it is right: this is the MORE LIKELY
--   operational failure, because the close is a manual call a tired body forgets, whereas an
--   inconclusive verdict at least leaves someone thinking about it.
--
-- WHY BOTH ESCALATE RATHER THAN AUTO-RESOLVE: cc-quality offered "or have the last accepting
--   audit attempt the close itself". I did NOT take that. An automatic close is a decision
--   closing without anyone deciding to close it, which is accepted_by_timeout wearing a better
--   coat -- the exact thing CAI-987 exists to end. Surfacing it costs a message; closing it
--   automatically costs the property the whole mechanism is for.
--
-- N3 (builder floor misattributes when assigner != builder) is NOT fixed here: cai's CAI-991
--   already steers builder attribution to the lane_task doer, which is a build with its own
--   auditors, not a predicate tweak. Recorded, not patched -- patching it narrowly here would be
--   the "second half-built control" cai warned against on the verdict-auth question.
--
-- APPLY: direct psycopg only -- scripts/apply_resolution_independence.py.

CREATE OR REPLACE FUNCTION public.enforce_decision_audit_resolution_independence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.resolved_at IS NOT NULL
       AND (OLD.resolved_at IS NULL OR OLD.resolved_by IS DISTINCT FROM NEW.resolved_by) THEN
        IF NEW.resolved_by IS NULL OR btrim(NEW.resolved_by) = '' THEN
            RAISE EXCEPTION 'cannot resolve audit %: resolved_by is required', NEW.id
                USING HINT = 'An unattributed resolution is not a resolution.';
        END IF;
        -- THE RULE, now executable instead of narrated.
        IF decision_audit_conflict(NEW.resolved_by, NEW.auditor_agent) THEN
            RAISE EXCEPTION
                'auditor % may not resolve its own audit of % (resolved_by=%)',
                NEW.auditor_agent, NEW.decision_ref, NEW.resolved_by
                USING HINT = 'A verdict must never clear its own escalation -- that is the '
                             'auditor silencing the alarm by answering the door. Someone else '
                             'resolves it.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_decision_audits_resolution_independence ON decision_audits;
CREATE TRIGGER trg_decision_audits_resolution_independence
    BEFORE UPDATE ON decision_audits
    FOR EACH ROW EXECUTE FUNCTION enforce_decision_audit_resolution_independence();

COMMENT ON COLUMN decision_audits.resolved_by IS
    'Who resolved it. MUST NOT be the auditor or its lane -- ENFORCED by '
    'trg_decision_audits_resolution_independence, not by this comment. It was prose for one hour '
    'and cc-quality proved in that hour that it could silence its own alarm (N1).';

-- ---------------------------------------------------------------------------------------------
-- N2: the escalator gains the third path -- audited clean, never closed.
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
    -- PATH 1 + 2: no verdict yet, or a verdict nobody has resolved (053).
    FOR rec IN
        SELECT da.id, da.decision_ref, da.auditor_agent, da.assigned_at, da.completed_at,
               da.sla_hours, da.verdict, sd.title, sd.audit_tier
          FROM decision_audits da
          JOIN strategic_decisions sd ON sd.decision_ref = da.decision_ref
         WHERE decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at)
           AND da.escalated_at IS NULL
           AND COALESCE(sd.is_test, false) = false
           AND COALESCE(da.completed_at, da.assigned_at) < now() - make_interval(hours => da.sla_hours)
    LOOP
        v_hours := round(EXTRACT(EPOCH FROM (now() - COALESCE(rec.completed_at, rec.assigned_at))) / 3600.0, 1);
        v_kind  := CASE WHEN rec.completed_at IS NULL THEN 'NOT STARTED'
                        ELSE 'UNRESOLVED ' || upper(rec.verdict) END;
        INSERT INTO agent_messages
            (from_agent, to_agent, message_type, subject, body, requires_response, priority)
        VALUES ('substrate', 'cai', 'blocker',
            'STALE AUDIT (' || v_kind || '): ' || rec.decision_ref || ' — ' || rec.auditor_agent
                || ', ' || v_hours || 'h (SLA ' || rec.sla_hours || 'h)',
            'CAI-RESP-988 §4 backstop.' || E'\n\nDecision: ' || rec.decision_ref || ' — '
                || COALESCE(rec.title,'(no title)') || E'\nTier: ' || COALESCE(rec.audit_tier,'UNTIERED')
                || E'\nAuditor: ' || rec.auditor_agent || E'\nState: ' || v_kind || ', ' || v_hours || 'h'
                || E'\n\n' || CASE WHEN rec.completed_at IS NULL THEN 'Nobody has looked at this yet.'
                     ELSE 'It WAS looked at and came back ' || rec.verdict || '. That BLOCKS the close '
                          || 'and nothing else will move it.' END
                || E'\n\nResolve by acting, then set resolved_at/resolved_by (NOT the auditor — 057 '
                || 'refuses a self-resolution). NOTHING WAS AUTO-CLOSED and nothing will be.',
            true, 'P2');
        UPDATE decision_audits SET escalated_at = now(), updated_at = now() WHERE id = rec.id;
        RETURN QUERY SELECT rec.decision_ref, rec.auditor_agent,
                            CASE WHEN rec.completed_at IS NULL THEN 'escalated_not_started'
                                 ELSE 'escalated_unresolved' END::TEXT;
    END LOOP;

    -- PATH 3 (cc-quality N2): every audit came back ACCEPTED and nobody pulled the lever. The
    -- board reads AUDIT-OWED while every counter reads zero, so nothing before this could see it.
    FOR rec IN
        SELECT s.decision_ref, s.title, s.audit_tier,
               max(da.completed_at) AS last_completed,
               min(da.sla_hours)    AS sla_hours
          FROM decision_audit_state s
          JOIN strategic_decisions sd ON sd.decision_ref = s.decision_ref
          JOIN decision_audits da     ON da.decision_ref = s.decision_ref
         WHERE s.audit_required
           AND s.n_assigned > 0
           AND s.n_open = 0 AND s.n_unresolved = 0
           AND s.n_accepted > 0
           AND sd.challenge_status <> 'accepted_by_audit'
           AND NOT EXISTS (SELECT 1 FROM decision_audits d2
                            WHERE d2.decision_ref = s.decision_ref AND d2.escalated_at IS NOT NULL)
         GROUP BY 1,2,3
        HAVING max(da.completed_at) < now() - make_interval(hours => min(da.sla_hours))
    LOOP
        v_hours := round(EXTRACT(EPOCH FROM (now() - rec.last_completed)) / 3600.0, 1);
        INSERT INTO agent_messages
            (from_agent, to_agent, message_type, subject, body, requires_response, priority)
        VALUES ('substrate', 'cai', 'blocker',
            'STALE AUDIT (AUDITED CLEAN, NEVER CLOSED): ' || rec.decision_ref
                || ' — ' || v_hours || 'h since the last verdict',
            'cc-quality N2, the third accumulation path.' || E'\n\nDecision: ' || rec.decision_ref
                || ' — ' || COALESCE(rec.title,'(no title)') || E'\nTier: '
                || COALESCE(rec.audit_tier,'UNTIERED')
                || E'\n\nEvery assigned audit came back ACCEPTED ' || v_hours || 'h ago and '
                || 'close_decision_by_audit was never called, so this decision is still open with '
                || 'nothing blocking it and nothing chasing it. The close is a manual step and it '
                || 'was missed.' || E'\n\nRun: SELECT close_decision_by_audit('''
                || rec.decision_ref || ''', ''<your agent id>'');'
                || E'\n\nIt is NOT closed automatically, deliberately: a decision closing with '
                || 'nobody deciding to close it is accepted_by_timeout wearing a better coat.',
            true, 'P2');
        UPDATE decision_audits SET escalated_at = now(), updated_at = now()
         WHERE decision_ref = rec.decision_ref AND escalated_at IS NULL;
        RETURN QUERY SELECT rec.decision_ref, '(all auditors)'::TEXT, 'escalated_never_closed'::TEXT;
    END LOOP;
END;
$function$;

COMMENT ON FUNCTION public.escalate_stale_decision_audits() IS
    'CAI-RESP-988 §4 + cc-quality F3/N2. Escalates THREE accumulation paths: never started; a '
    'verdict nobody resolved; and audited-clean-but-never-closed. Never closes a decision and '
    'never writes a verdict -- an automatic close would be accepted_by_timeout in a better coat.';
