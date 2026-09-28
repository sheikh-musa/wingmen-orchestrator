-- 055_audit_board_sink.sql
-- cc-quality F2 / cai CAI-RESP-992 item 4d: the board had NO READER.
--
-- THE FINDING. cc-quality grepped the entire orchestrator repo, console included, for any
--   consumer of `decision_audit_state`. ONE hit: its own review report. No console panel, no
--   cron report, no digest. Its words: the count is "visible only to a body that already suspects
--   the answer and writes the query by hand".
--
-- cai first ruled this subsumed by making the tier NOT NULL, then CORRECTED HERSELF in CAI-992
--   4d: NOT-NULL covers the untiered COUNT, but the audit OUTCOMES -- rejections,
--   could-not-verifies, stale escalations -- still have no reader. FULL-tier, on #62.
--
-- WHY IT MATTERS MORE THAN IT SOUNDS, and this is the whole arc of 2026-08-16: a correct
--   measurer with no sink is the exact shape of `invariant_registry` (34 rows, no writer, no
--   reader, RESIDENCY-1 sitting at NULL) and of `bug_pipeline_readiness` (10/10 green for 39 days
--   off a clock nobody scheduled). We built an honest board and then very nearly reproduced the
--   defect it exists to name, one layer up. A board nobody reads is not a control; it is a
--   query somebody could have written.
--
-- WHAT THIS IS, deliberately small: a DIGEST, not a console page. A console change goes through
--   the fail-closed deploy gate (sw.js/APP_BUILD parity, a cc-quality review of that exact
--   content hash, render screenshots) -- correct for shipping UI, disproportionate for getting a
--   reader onto a board tonight. The digest needs no deploy and is verifiable from the same
--   connection that applies it.
--
-- THE TWO THINGS THAT MAKE IT A SINK RATHER THAN NOISE:
--   * It reports ONLY when there is something actionable. A daily "all clear" trains people to
--     stop reading, which is how a real alert gets ignored (the same reason 050 escalates once
--     per audit rather than daily).
--   * It carries its OWN staleness. `audit_board_digest_log` records every run, and the board
--     exposes `digest_last_ran_at` -- so if the cron job dies, that is VISIBLE rather than
--     silently indistinguishable from "nothing to report". A dead measurer must never look like
--     a clean bill of health (CAI-RESP-986 §1). That is the one honest answer to "who watches the
--     watcher" available without an infinite regress.
--
-- RESIDUAL, STATED NOT HIDDEN: nothing yet reads `digest_last_ran_at` either. This pushes the
--   unread-state problem up one level rather than eliminating it -- but it converts "no reader at
--   all" into "one timestamp a human or a future console row can check", and the digest itself
--   arrives in cai's inbox, which IS read. Naming the residual because pretending a sink is
--   complete would be the same defect wearing a new coat.
--
-- APPLY: direct psycopg only -- scripts/apply_audit_board_sink.py.

CREATE TABLE IF NOT EXISTS audit_board_digest_log (
    id          bigserial PRIMARY KEY,
    ran_at      timestamptz NOT NULL DEFAULT now(),
    had_content boolean     NOT NULL,
    msg_id      bigint,
    summary     text
);

REVOKE ALL ON audit_board_digest_log FROM anon, authenticated;
GRANT SELECT ON audit_board_digest_log TO console_readonly;
ALTER TABLE audit_board_digest_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_board_digest_log_console_ro ON audit_board_digest_log;
CREATE POLICY audit_board_digest_log_console_ro ON audit_board_digest_log
    FOR SELECT TO console_readonly USING (true);
DROP POLICY IF EXISTS audit_board_digest_log_service ON audit_board_digest_log;
CREATE POLICY audit_board_digest_log_service ON audit_board_digest_log
    TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE audit_board_digest_log IS
    'Every run of audit_board_digest(), including the runs with nothing to report. This is what '
    'makes the digest''s own silence readable: no row for today means the JOB did not run, which '
    'is different from "the board was clean" and must never look the same.';

-- ---------------------------------------------------------------------------------------------
-- The digest. Returns what it did, so a caller can never infer success from silence.
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.audit_board_digest()
RETURNS TABLE(had_content boolean, summary text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_body    text := '';
    v_lines   int  := 0;
    v_msg_id  bigint;
    r         RECORD;
    v_untiered_open int;
    v_candidates    int;
BEGIN
    -- 1. Audits that came back NEGATIVE or INCONCLUSIVE and nobody has resolved. These BLOCK a
    --    close and no timer will ever move them -- the state cc-quality found CAI-985 sitting in.
    FOR r IN
        SELECT da.decision_ref, da.auditor_agent, da.verdict, da.lens,
               round(EXTRACT(EPOCH FROM (now() - da.completed_at))/3600.0, 1) AS hours,
               sd.audit_tier
          FROM decision_audits da
          JOIN strategic_decisions sd ON sd.decision_ref = da.decision_ref
         WHERE da.verdict IN ('rejected', 'could_not_verify')
           AND da.resolved_at IS NULL
           AND COALESCE(sd.is_test, false) = false
         ORDER BY da.completed_at
    LOOP
        v_lines := v_lines + 1;
        v_body := v_body || format(E'  UNRESOLVED %s — %s (%s, %s lens) %sh, tier %s\n',
                                   upper(r.verdict), r.decision_ref, r.auditor_agent,
                                   COALESCE(r.lens, 'no lens recorded'), r.hours,
                                   COALESCE(r.audit_tier, 'UNTIERED'));
    END LOOP;

    -- 2. Assigned and never started, past SLA.
    FOR r IN
        SELECT da.decision_ref, da.auditor_agent,
               round(EXTRACT(EPOCH FROM (now() - da.assigned_at))/3600.0, 1) AS hours, da.sla_hours
          FROM decision_audits da
          JOIN strategic_decisions sd ON sd.decision_ref = da.decision_ref
         WHERE da.completed_at IS NULL
           AND da.assigned_at < now() - make_interval(hours => da.sla_hours)
           AND COALESCE(sd.is_test, false) = false
         ORDER BY da.assigned_at
    LOOP
        v_lines := v_lines + 1;
        v_body := v_body || format(E'  STALE ASSIGNMENT — %s (%s) %sh, SLA %sh\n',
                                   r.decision_ref, r.auditor_agent, r.hours, r.sla_hours);
    END LOOP;

    -- 3. FULL-tier with nobody asked. Never closes, and nobody is chasing it (cc-quality F2).
    FOR r IN
        SELECT decision_ref, left(title, 70) AS title
          FROM decision_audit_state
         WHERE audit_state = 'AUDIT-OWED-NO-AUDITOR'
         ORDER BY decided_at
    LOOP
        v_lines := v_lines + 1;
        v_body := v_body || format(E'  NO AUDITOR ASSIGNED — %s — %s\n', r.decision_ref, r.title);
    END LOOP;

    -- 4. The untiered watch cai named as "the EXERCISED watch" in CAI-988 §2. Counted, not listed.
    SELECT count(*) FILTER (WHERE untiered),
           count(*) FILTER (WHERE audit_state = 'UNTIERED-CANDIDATE')
      INTO v_untiered_open, v_candidates
      FROM decision_audit_state
     WHERE challenge_status IN ('challenge_window', 'unchallenged');
    IF v_candidates > 0 THEN
        v_lines := v_lines + 1;
        v_body := v_body || format(
            E'  UNTIERED MONEY/RESIDENCY CANDIDATES — %s open decision(s) nobody has tiered\n',
            v_candidates);
    END IF;

    IF v_lines = 0 THEN
        INSERT INTO audit_board_digest_log (had_content, summary)
        VALUES (false, 'nothing actionable');
        RETURN QUERY SELECT false, 'nothing actionable'::text;
        RETURN;
    END IF;

    v_body :=
        'The audit board has ' || v_lines || ' item(s) that need somebody. Nothing here closes on '
        || 'its own -- these states have no timer by design.' || E'\n\n' || v_body
        || E'\nRESOLVE by acting, then set resolved_at/resolved_by on the audit row (a verdict must '
        || 'not clear its own escalation). Full detail: decision_audit_state / decision_audits.'
        || E'\n\nThis digest reports ONLY when something is actionable -- a daily all-clear trains '
        || 'people to stop reading it. Its runs, including the silent ones, are in '
        || 'audit_board_digest_log, so a dead job is distinguishable from a clean board.';

    INSERT INTO agent_messages
        (from_agent, to_agent, message_type, subject, body, requires_response, priority)
    VALUES ('substrate', 'cai', 'update',
            'AUDIT BOARD: ' || v_lines || ' item(s) need somebody (nothing here closes on its own)',
            v_body, false, 'P2')
    RETURNING id INTO v_msg_id;

    INSERT INTO audit_board_digest_log (had_content, msg_id, summary)
    VALUES (true, v_msg_id, v_lines || ' actionable item(s)');

    RETURN QUERY SELECT true, (v_lines || ' actionable item(s)')::text;
END;
$function$;

COMMENT ON FUNCTION public.audit_board_digest() IS
    'cc-quality F2 / CAI-RESP-992 4d: the reader the audit board did not have. Reports ONLY when '
    'something is actionable; logs EVERY run including the silent ones, so a dead job is '
    'distinguishable from a clean board. Never closes or resolves anything.';

REVOKE ALL ON FUNCTION public.audit_board_digest() FROM PUBLIC, anon, authenticated;

-- Daily at 08:00 UTC (noon Abu Dhabi -- the operator is UTC+4 and cai reads the bus continuously).
SELECT cron.schedule('audit-board-digest', '0 8 * * *', 'SELECT audit_board_digest();');
