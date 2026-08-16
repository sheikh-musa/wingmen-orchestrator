-- 058 — the escalator was still publishing the argument I withdrew and cai overruled.
--
-- FOUND BY cc-quality (#23886, MEDIUM) auditing CAI-994/997. PATH 3's message ended with:
--
--     "It is NOT closed automatically, deliberately: a decision closing with nobody deciding
--      to close it is accepted_by_timeout wearing a better coat."
--
-- That reasoning is REFUTED. I withdrew it and cai overruled it on the record (CAI-997): a
-- timeout closes on the ABSENCE of a signal; auto-close closes on the PRESENCE of N affirmative
-- verdicts each carrying stated checks. Opposite properties. "Nobody deciding" was false too —
-- the accepts ARE the deciding acts.
--
-- WHY IT MATTERS MORE THAN A STALE COMMENT, which is cc-quality's actual point: this string is
-- ADDRESSED TO THE DECIDER and re-published into the bus on EVERY firing. A fresh body reading it
-- has no way to know the premise was overruled — the correction lives in a decision row it may
-- never open. I corrected the record with cai within minutes; the artifact never got the same
-- correction. An argument that survives only in the code that prints it is still being made.
--
-- THE REASON THAT ACTUALLY SURVIVES, and it is cc-quality's, not mine: AUTO-CLOSE DELETES THE LAST
-- MOMENT WHEN ANYONE *READS* THE AUDIT. The no-rubber-stamp control is a human reading
-- checks_performed, and that happens at exactly one point — the close.
--
-- ALSO FIXES cc-quality's LOW from the same review: the escalation prompts a close but did not
-- carry WHO audited or WHAT THEY CHECKED — the very thing the close exists to make someone read.
-- close_decision_by_audit's own message already says "read checks_performed"; the escalation that
-- prompts it did not. Now it names each auditor with the lens they took.
--
-- Body text only. No signature change, no behavioural change to WHICH rows escalate. Paths 1 and 2
-- are reproduced verbatim from the applied 057 definition.
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
    -- PATH 1 + 2: no verdict yet, or a verdict nobody has resolved (053). UNCHANGED from 057.
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

    -- PATH 3 (cc-quality N2): every audit came back ACCEPTED and nobody pulled the lever.
    FOR rec IN
        SELECT s.decision_ref, s.title, s.audit_tier,
               max(da.completed_at) AS last_completed,
               min(da.sla_hours)    AS sla_hours,
               -- 058 / cc-quality LOW: carry WHO looked and with WHICH lens, so the message that
               -- prompts the close also tells the closer what there is to read.
               string_agg(da.auditor_agent || ' (' || COALESCE(da.lens, 'lens not recorded') || ')',
                          ', ' ORDER BY da.auditor_agent) AS who
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
                || E'\nAudited by: ' || rec.who
                || E'\n\nEvery assigned audit came back ACCEPTED ' || v_hours || 'h ago and '
                || 'close_decision_by_audit was never called, so this decision is still open with '
                || 'nothing blocking it and nothing chasing it. The close is a manual step and it '
                || 'was missed.' || E'\n\nBEFORE you close it, read what they actually checked: '
                || E'\n  SELECT auditor_agent, lens, verdict, checks_performed, findings'
                || E'\n    FROM decision_audits WHERE decision_ref = ''' || rec.decision_ref || ''';'
                || E'\n\nThen: SELECT close_decision_by_audit('''
                || rec.decision_ref || ''', ''<your agent id>'');'
                || E'\n\nIt is NOT closed automatically, deliberately — and the reason is NOT that '
                || 'auto-close resembles accepted_by_timeout (that argument was refuted and '
                || 'overruled in CAI-997: a timeout fires on the ABSENCE of a signal, auto-close on '
                || 'the PRESENCE of affirmative verdicts; opposite properties). The surviving reason '
                || 'is that AUTO-CLOSE WOULD DELETE THE LAST MOMENT ANYONE READS THE AUDIT. The close '
                || 'is the one point where a human reads checks_performed; automate it and the '
                || 'mechanism keeps producing audits nobody consumes.',
            true, 'P2');
        UPDATE decision_audits SET escalated_at = now(), updated_at = now()
         WHERE decision_ref = rec.decision_ref AND escalated_at IS NULL;
        RETURN QUERY SELECT rec.decision_ref, '(all auditors)'::TEXT, 'escalated_never_closed'::TEXT;
    END LOOP;
END;
$function$;

COMMENT ON FUNCTION public.escalate_stale_decision_audits() IS
 'CAI-988 §4 backstop + cc-quality N2 third path. 058: PATH 3 no longer republishes the refuted '
 'accepted_by_timeout argument (withdrawn by orch-console, overruled in CAI-997) and now names each '
 'auditor with its lens and tells the closer to read checks_performed first.';
