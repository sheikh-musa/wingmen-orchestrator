-- 059 — a FULL ruling with nobody assigned now RAISES ITSELF. Ratified in CAI-RESP-1001 §2.
--
-- WHY. cai has now missed mandatory-at-creation FOUR times (CAI-989, 995, 999, 1000): a ruling
-- tiered FULL with zero auditors. Each time it was caught by a human reading the daily digest --
-- 989 by the sink's first run, 995 by me reading the board. The digest ALREADY reports this
-- (055 item 3). Reporting is not the same as asserting: a report needs a reader, and the whole
-- lesson of CAI-994 is that a rule which needs someone to remember it is decoration.
--
-- WHY NOT A CONSTRAINT, stated because it is the obvious first idea and it does not work here.
-- A FULL decision cannot carry an auditor at INSERT time: decision_audits has an FK to
-- strategic_decisions, so the decision row must exist first. A deferred constraint trigger would
-- work only if naming and materialising happened in ONE transaction -- and they deliberately do
-- not: cai writes the ruling, orch-console materialises the rows afterwards, which is the
-- separation that makes the naming independently checkable. A hard constraint would either break
-- that separation or force cai to stop writing FULL rulings. So the honest mechanism is a
-- time-boxed escalation, and it should say so rather than pretend to be a constraint.
--
-- WHY NO NEW SCHEDULER. This is PATH 4 on escalate_stale_decision_audits(), which pg_cron jobid 10
-- already runs hourly and which has a PROVEN execution record (cron.job_run_details, 22:00Z
-- 2026-08-16). Adding a second scheduled thing to watch would be a fifth copy of a concept we
-- already collapsed three copies of tonight.
--
-- GRACE WINDOW: 2 hours from decided_at. Long enough that normal materialisation (minutes) never
-- trips it; short enough that a miss surfaces the same working day. Under CAI-1001 the auditor is
-- named INLINE at creation, so the only thing this catches is a naming that never became rows --
-- which is exactly the gap.
--
-- FAILS TOWARD NOISE, NEVER TOWARD SILENCE: if a decision is legitimately awaiting a named auditor
-- (as CAI-999 was, deliberately), this escalates once and the row records that it did. One
-- unnecessary message is the correct trade against a FULL ruling closing unlooked-at.

-- Dedupe store. The other three paths dedupe on decision_audits.escalated_at; PATH 4 exists
-- precisely BECAUSE there are no decision_audits rows, so it needs its own.
CREATE TABLE IF NOT EXISTS decision_tier_escalations (
    decision_ref text PRIMARY KEY REFERENCES strategic_decisions(decision_ref) ON DELETE CASCADE,
    escalated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON decision_tier_escalations FROM anon, authenticated;
ALTER TABLE decision_tier_escalations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS decision_tier_escalations_service ON decision_tier_escalations;
CREATE POLICY decision_tier_escalations_service ON decision_tier_escalations
    FOR ALL TO service_role USING (true) WITH CHECK (true);
COMMENT ON TABLE decision_tier_escalations IS
    'Dedupe for escalate_stale_decision_audits() PATH 4 (FULL tier, zero auditors). Separate from '
    'decision_audits.escalated_at because PATH 4 fires exactly when no decision_audits row exists.';

CREATE OR REPLACE FUNCTION public.escalate_full_tier_without_auditor(p_grace_hours numeric DEFAULT 2)
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
        SELECT sd.decision_ref, sd.title, sd.decided_at
          FROM strategic_decisions sd
         WHERE sd.audit_tier = 'FULL'
           AND COALESCE(sd.is_test, false) = false
           -- NOT make_interval(hours => p_grace_hours): make_interval's `hours` is INTEGER and
           -- p_grace_hours is numeric, so that form throws UndefinedFunction at CALL time while the
           -- CREATE succeeds happily. Caught by exercising it, not by applying it. Multiplication
           -- also keeps a fractional grace meaningful instead of silently truncating it.
           AND sd.decided_at < now() - (p_grace_hours * interval '1 hour')
           AND NOT EXISTS (SELECT 1 FROM decision_audits da
                            WHERE da.decision_ref = sd.decision_ref)
           AND NOT EXISTS (SELECT 1 FROM decision_tier_escalations e
                            WHERE e.decision_ref = sd.decision_ref)
    LOOP
        v_hours := round(EXTRACT(EPOCH FROM (now() - rec.decided_at)) / 3600.0, 1);
        INSERT INTO agent_messages
            (from_agent, to_agent, message_type, subject, body, requires_response, priority)
        VALUES ('substrate', 'cai', 'blocker',
            'FULL TIER, NO AUDITOR: ' || rec.decision_ref || ' — ' || v_hours || 'h and nobody is assigned',
            'CAI-RESP-1001 §2. The mechanism is raising this about itself.'
                || E'\n\nDecision: ' || rec.decision_ref || ' — ' || COALESCE(rec.title,'(no title)')
                || E'\nTier: FULL' || E'\nDecided: ' || rec.decided_at || ' (' || v_hours || 'h ago)'
                || E'\nAuditors assigned: NONE'
                || E'\n\nA FULL ruling with zero auditors never closes and nothing chases it — it is '
                || 'AUDIT-OWED-NO-AUDITOR, which reads on the board as "in progress" and is in fact '
                || 'nobody''s. This has happened four times (CAI-989, 995, 999, 1000), each caught by '
                || 'a human reading the digest. This path exists so it no longer depends on that.'
                || E'\n\nFIX: name the auditor(s) INLINE per CAI-1001 §1, then orch-console '
                || 'materialises the decision_audits rows from that naming.'
                || E'\n\nIf the decision is DELIBERATELY unassigned (as CAI-999 was), this fires ONCE '
                || 'and will not repeat — the row in decision_tier_escalations records that you were '
                || 'told. It fails toward one unnecessary message, never toward silence.',
            true, 'P2');
        INSERT INTO decision_tier_escalations (decision_ref) VALUES (rec.decision_ref)
            ON CONFLICT (decision_ref) DO NOTHING;
        RETURN QUERY SELECT rec.decision_ref, '(nobody assigned)'::TEXT, 'escalated_full_no_auditor'::TEXT;
    END LOOP;
END;
$function$;

COMMENT ON FUNCTION public.escalate_full_tier_without_auditor(numeric) IS
 'CAI-1001 §2: tier=FULL implies auditors>=1, raised by the mechanism rather than by whoever reads '
 'the digest. Time-boxed escalation, NOT a constraint — naming and materialisation are deliberately '
 'separate transactions, so no constraint can express it without breaking that separation.';

-- WIRE IT to the runner that is already PROVEN to execute. jobid 10 has a real
-- cron.job_run_details record (22:00Z 2026-08-16); a brand-new job would be a schedule, and
-- "registered != ran" is the exact distinction CAI-998's F4 turned on. Same job, both calls, so
-- there is one thing to watch rather than two.
-- Via cron.alter_job, NOT a direct UPDATE on cron.job: the table is not writable by this role
-- (permission denied), and alter_job is the extension's supported path. It mutates the job IN
-- PLACE, so jobid 10 keeps the run history that makes "it actually executed" provable.
SELECT cron.alter_job(
    job_id  := 10,
    command := 'SELECT escalate_stale_decision_audits(); SELECT escalate_full_tier_without_auditor();'
);
