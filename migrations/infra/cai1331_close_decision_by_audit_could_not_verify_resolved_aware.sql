-- migrations/infra/cai1331_close_decision_by_audit_could_not_verify_resolved_aware.sql
-- PROPOSE-ONLY. CAI-RESP-1331 (ratified from CAI-RESP-1323; design-audited ACCEPTED by
-- cc-fleet-health, decision_audits row 269, lens=close-mechanism-resolved-verdict-semantics).
-- Authored by cc-fleet-health (builder). cai grants per SS6.6; console applies. The author does
-- NOT apply this and (auditor != builder) is NOT the required 2nd audit lens on it.
--
-- WHAT (the bug): close_decision_by_audit blocks close on the view's resolved_at-BLIND
--   v.n_could_not_verify. A could_not_verify that has been RESOLVED (resolved_at IS NOT NULL, by an
--   INDEPENDENT party — enforced by trigger enforce_decision_audit_resolution_independence, which
--   forbids resolved_by conflicting with auditor_agent) and has SINCE been re-audited to accepted
--   still blocks forever — contradicting the block's own hint ("Resolve it or reassign") and leaving
--   69 open decisions permanently unclosable (verified: CAI-RESP-1104 has resolved could_not_verify
--   id=174 + cc-storefront accepted id=215, yet is_audit_closed=false).
--
-- FIX (exactly as ratified): block only on UNRESOLVED could_not_verify, counted INLINE and
--   could_not_verify-SPECIFIC:
--     * could_not_verify-SPECIFIC — rejected/nonconforming stay permanently blocking regardless of
--       resolved_at (CAI-991: a real defect needs a NEW decision_ref, never same-ref resolution).
--       (Do NOT reuse decision_audit_unresolved() wholesale here — it would also un-block resolved
--        rejected/nonconforming.)
--     * INLINE — computed in the function like the existing n_nonconforming arm (defence-in-depth),
--       NOT by mutating the view's raw n_could_not_verify column (other consumers/dashboards read it).
--   Aligns the close-block with the resolved_at-aware semantics decision_audit_unresolved() already
--   implements elsewhere in decision_audit_state. The accepted-quorum gate (n_accepted>0 AND, for
--   FULL tier, >=2 distinct completed accepted lenses) is UNCHANGED, so a resolved could_not_verify
--   ALONE still cannot close anything — genuine independent acceptance is still required.
--
-- DIFF vs the CURRENT LIVE function body: exactly TWO edits — (1) DECLARE adds `v_n_unresolved_cnv int;`
--   after v_n_nonconforming; (2) the `IF v.n_could_not_verify > 0` block is replaced by the inline
--   resolved-aware count below. Every other line is byte-identical to the live definition.

CREATE OR REPLACE FUNCTION public.close_decision_by_audit(p_decision_ref text, p_closed_by text)
 RETURNS text
 LANGUAGE plpgsql
AS $function$
DECLARE
    v RECORD;
    v_distinct_accepted_lenses int;
    v_n_nonconforming int;
    v_n_unresolved_cnv int;   -- CAI-RESP-1331: UNRESOLVED could_not_verify count (inline, resolved_at-aware)
BEGIN
    SELECT * INTO v FROM decision_audit_state WHERE decision_ref = p_decision_ref;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such decision (or it is a test row): %', p_decision_ref;
    END IF;

    IF v.n_accepted = 0 THEN
        RAISE EXCEPTION 'cannot close %: no completed audit with verdict=accepted', p_decision_ref
            USING HINT = 'An audit must have RUN. CAI-978 — a control is not satisfied until it executes.';
    END IF;
    IF v.n_open > 0 THEN
        RAISE EXCEPTION 'cannot close %: % audit(s) still in flight', p_decision_ref, v.n_open;
    END IF;
    IF v.n_rejected > 0 THEN
        RAISE EXCEPTION 'cannot close %: an auditor REJECTED it', p_decision_ref;
    END IF;

    -- CAI-RESP-1331 (was CAI-RESP-1323): could_not_verify is RESOLVABLE — unlike rejected/nonconforming
    -- it usually means the auditor lacked scope/access, not that a defect was found, and its hint
    -- already promises "Resolve it or reassign". Block only on UNRESOLVED could_not_verify rows,
    -- counted INLINE (not the view's resolved_at-BLIND v.n_could_not_verify) and could_not_verify-
    -- SPECIFIC. A resolved could_not_verify no longer blocks; resolution is provably independent
    -- (enforce_decision_audit_resolution_independence: resolved_by may not conflict with the auditor),
    -- and the accepted-quorum gate below still requires genuine acceptance to actually close.
    SELECT count(*) INTO v_n_unresolved_cnv FROM decision_audits
        WHERE decision_ref = p_decision_ref
          AND verdict = 'could_not_verify' AND resolved_at IS NULL;
    IF v_n_unresolved_cnv > 0 THEN
        RAISE EXCEPTION 'cannot close %: an UNRESOLVED auditor could-not-verify remains', p_decision_ref
            USING HINT = 'could_not_verify blocks until independently resolved or reassigned; a RESOLVED could_not_verify no longer blocks. rejected/nonconforming block permanently (CAI-991).';
    END IF;

    -- B.4 (CAI-991): a 'nonconforming' verdict is a terminal NON-pass — it must block close
    -- exactly like rejected/could_not_verify, or the new verdict would silently round to a pass.
    -- (Computed directly so this arm is correct even if the view's n_nonconforming (B.3) has not
    --  yet been applied — defence in depth.)
    SELECT count(*) INTO v_n_nonconforming FROM decision_audits
        WHERE decision_ref = p_decision_ref AND verdict = 'nonconforming';
    IF v_n_nonconforming > 0 THEN
        RAISE EXCEPTION 'cannot close %: an auditor found it NONCONFORMING', p_decision_ref
            USING HINT = 'nonconforming (CAI-991) is a visible outcome, not a pass. Resolve it or rebuild.';
    END IF;

    -- C (CAI-996): a FULL-tier decision needs >=2 DISTINCT auditor LENSES that COMPLETED with
    -- accepted — the single-auditor-close 996 ordered be impossible. (v.lenses is distinct over
    -- ALL audits incl. open/other-verdict, so compute the completed+accepted distinct-lens count
    -- directly.) Non-FULL tiers keep the single-accepted-lens close (unchanged behaviour).
    IF v.audit_tier = 'FULL' THEN
        SELECT count(DISTINCT lens) INTO v_distinct_accepted_lenses
          FROM decision_audits
         WHERE decision_ref = p_decision_ref
           AND completed_at IS NOT NULL AND verdict = 'accepted' AND lens IS NOT NULL;
        IF v_distinct_accepted_lenses < 2 THEN
            RAISE EXCEPTION
              'cannot close %: FULL tier requires >=2 distinct completed accepted lenses (have %) — CAI-996',
              p_decision_ref, v_distinct_accepted_lenses
              USING HINT = 'A single-auditor pass cannot close a FULL decision. Assign a second, distinct lens.';
        END IF;
    END IF;

    UPDATE strategic_decisions
       SET challenge_status = 'accepted_by_audit', updated_at = now()
     WHERE decision_ref = p_decision_ref
       AND challenge_status IN ('challenge_window', 'unchallenged');
    IF NOT FOUND THEN
        RETURN 'skipped_not_open';
    END IF;

    INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, requires_response)
    VALUES (COALESCE(p_closed_by, 'substrate'), 'cai', 'decision',
        p_decision_ref || ': CLOSED accepted_by_audit (' || array_to_string(v.auditors, ', ') || ')',
        'Closed by ASSIGNED AUDIT rather than by timeout (CAI-RESP-987). Auditors: '
            || array_to_string(v.auditors, ', ')
            || E'.\nRead decision_audits.checks_performed for what was actually checked.', false);
    RETURN 'closed';
END;
$function$;
