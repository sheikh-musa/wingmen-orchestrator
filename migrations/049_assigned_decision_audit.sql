-- 049_assigned_decision_audit.sql
-- CAI-RESP-987 (supersedes CAI-970): replace the passive challenge WINDOW with an ASSIGNED AUDIT.
--
-- THE OPERATOR, op#13610: "why not do a thorough audit instead of simply waiting and hoping
--   someone might catch it?" — and his sharper follow-up, op#13617, which is the actual argument:
--   the operator-veto lever "assumes i know theres a window". A control that only fires when a
--   human happens to be watching is not a control. He also named the half nobody measures:
--   "not hearing gazzabyte complain about how slow our substrate is" — the WAIT costs us
--   invisibly in both directions. Nobody challenges, and nobody notices the waiting.
--
-- THE MEASUREMENT THAT MADE THE CASE (orch-console, at source): of 1,351 non-test
--   strategic_decisions, 941 closed `accepted_by_timeout` — a clock ran out and nothing looked.
--   506 ran a window in the last 30 days. NO substantive challenge was found. cai's own words:
--   the window is "a deny-control that never executed", and she owns that CAI-970 "optimised the
--   wrong variable — I tiered how long to WAIT when the lever is whether someone LOOKS".
--
-- THIS IS CAI-RESP-986 §1 APPLIED TO GOVERNANCE ITSELF: NEVER GREEN ON ABSENCE-OF-SIGNAL.
--   `accepted_by_timeout` is the purest form of the defect this fleet spent 2026-08-16 finding
--   everywhere else — a readiness board green off a measurer that was never scheduled, an
--   invariant registry whose EXERCISED state was unreachable, an SSRF test that passed on the
--   vulnerable code. A decision accepted because nobody objected is the governance version of
--   exactly that. Silence is not review.
--
-- WHAT THIS DOES NOT DO, deliberately — one tier first, never big-bang (that is what killed
-- lane_tasks #60, and cai's CAI-RESP-987 timing note says so explicitly):
--
--   * It does NOT auto-classify decisions from their text, and that is a MEASURED refusal, not a
--     shortcut. I built the keyword tier first and measured it against the real corpus before
--     shipping it, and it fails in BOTH directions on this data:
--        - precision: a money/residency keyword set over title+decision+reasoning matches
--          449/508 = 88% of the last 30 days (title-only: 262/508 = 52%), because cai's rulings
--          CITE doctrine constantly. It flags CAI-RESP-986 (a registry-honesty ruling) as money.
--        - recall: it MISSES obvious ones — CAI-RESP-924 (§6.6 order+audit grant),
--          CAI-RESP-732 (billable load on a consumer token), CAI-RESP-854 (PII decrypt-on-read).
--     A tier that flags 88% and still misses the real money rows is not a tier. Shipping it would
--     have been a second unexercised control wearing the costume of the first. The tier is
--     therefore an EXPLICIT field the decider sets AS PART OF DECIDING (one write, not a second
--     thing to remember), and the untiered population stays on the passive window exactly as
--     today. Generalisation is cai's, on those numbers.
--
--   * It does NOT close anything by itself. `accepted_by_audit` is reachable ONLY through
--     close_decision_by_audit(), which requires a completed, non-self audit that states what it
--     checked. Compare 047: an allowlist of three tokens I invented, none writable by the CHECK
--     constraint, so EXERCISED was unreachable and the view would have read correct forever.
--     The apply script executes the close path against a real row for that exact reason.
--
--   * It does NOT open a governance gap. Untiered decisions (audit_tier IS NULL) ride the
--     existing pg_cron window unchanged — VERIFIED live at source, not assumed: pg_cron jobid 7,
--     schedule '*/5 * * * *', command `SELECT enforce_challenge_window_timeouts();`, active=true.
--
-- VOCABULARY CHECKED BEFORE IT WAS USED. `strategic_decisions_challenge_status_check` allows
--   exactly unchallenged|challenge_window|challenged|accepted|accepted_by_timeout|overridden|
--   cai_review_requested|informational|implemented|superseded. `accepted_by_audit` is NOT among
--   them, so this migration widens the CHECK by exactly one value — the token cai named in #62's
--   acceptance criteria. Widening a CHECK cannot invalidate an existing row. This is 047's
--   lesson paid forward: read the constraint, then write the code.
--
-- APPLY: direct psycopg only — scripts/apply_assigned_decision_audit.py. NEVER `supabase db push`
--   (decision 962). Target: the SUBSTRATE coordination-plane DB, not any client silo.

-- ---------------------------------------------------------------------------------------------
-- 1. TIER — explicit, set by the decider, NULL means nobody tiered it (not "safe")
-- ---------------------------------------------------------------------------------------------

ALTER TABLE strategic_decisions ADD COLUMN IF NOT EXISTS audit_tier text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'strategic_decisions'::regclass
                      AND conname  = 'strategic_decisions_audit_tier_check') THEN
        ALTER TABLE strategic_decisions
            ADD CONSTRAINT strategic_decisions_audit_tier_check
            CHECK (audit_tier IS NULL OR audit_tier = ANY (ARRAY['FULL', 'NONE']));
    END IF;
END $$;

COMMENT ON COLUMN strategic_decisions.audit_tier IS
    'How deeply this decision must be looked at before it can close. FULL = a named auditor must '
    'audit it at source; it will NEVER close by timeout. NONE = deliberately judged not to need '
    'one, by somebody, on the record. NULL = UNTIERED, meaning nobody made that judgement -- '
    'which is NOT the same as NONE and must never be rendered as one (CAI-RESP-986 s1). Untiered '
    'decisions ride the passive challenge window exactly as before. Set this AS PART OF filing '
    'the decision: a tier that is a separate thing to remember is the control the operator '
    'already rejected (op#13617, "assumes i know theres a window").';

-- Widen by exactly one value: the token cai named in lane_tasks #62's acceptance criteria.
ALTER TABLE strategic_decisions DROP CONSTRAINT IF EXISTS strategic_decisions_challenge_status_check;
ALTER TABLE strategic_decisions ADD CONSTRAINT strategic_decisions_challenge_status_check
    CHECK (challenge_status = ANY (ARRAY[
        'unchallenged', 'challenge_window', 'challenged', 'accepted', 'accepted_by_timeout',
        'overridden', 'cai_review_requested', 'informational', 'implemented', 'superseded',
        'accepted_by_audit'
    ]));

-- ---------------------------------------------------------------------------------------------
-- 2. THE AUDIT RECORD
-- ---------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS decision_audits (
    id              bigserial PRIMARY KEY,
    decision_ref    text        NOT NULL REFERENCES strategic_decisions(decision_ref) ON DELETE RESTRICT,
    auditor_agent   text        NOT NULL,
    assigned_by     text        NOT NULL,
    assigned_at     timestamptz NOT NULL DEFAULT now(),
    verdict         text,
    checks_performed text,
    findings        text,
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    -- One row per (decision, auditor) -- so a decision can carry SEVERAL auditors. cai's
    -- CAI-RESP-987 named the trigger for a second independent auditor (audit LOAD near capacity,
    -- or STAKES concentrating -- specifically a governance change to the audit mechanism ITSELF,
    -- where cc-quality would be auditing the rules of its own auditing). That trigger needs a
    -- shape that can HOLD a second auditor or it is a promise; this is the shape.
    CONSTRAINT decision_audits_one_per_auditor UNIQUE (decision_ref, auditor_agent),

    CONSTRAINT decision_audits_verdict_check
        CHECK (verdict IS NULL OR verdict = ANY (ARRAY['accepted', 'rejected', 'could_not_verify'])),

    -- An audit is either in flight or finished. A verdict with no completion time (or the
    -- reverse) is the ACCEPTED-BY-NOBODY shape cc-quality named on PR #75: a row that looks
    -- acted-on is more misleading than one that was never touched.
    CONSTRAINT decision_audits_completion_coherent
        CHECK ((verdict IS NULL) = (completed_at IS NULL)),

    -- GUARD 2, NO RUBBER-STAMP, enforced rather than requested: a finished audit MUST state what
    -- it actually checked. "Looks fine" with no stated checks is absence-of-signal wearing the
    -- costume of review -- it would recreate the timeout with extra steps.
    -- HONEST LIMIT, stated rather than implied: a length floor stops the EMPTY and the one-word
    -- stamp. It cannot stop a determined body from typing forty characters of nothing. What it
    -- buys is the same thing PR #75 bought -- self-certifying now takes a DELIBERATE LIE in a
    -- named column instead of the silent default. Anyone claiming this makes a rubber-stamp
    -- impossible is wrong.
    CONSTRAINT decision_audits_no_rubber_stamp
        CHECK (completed_at IS NULL
               OR (checks_performed IS NOT NULL AND length(btrim(checks_performed)) >= 40))
);

CREATE INDEX IF NOT EXISTS decision_audits_decision_ref_idx ON decision_audits (decision_ref);
CREATE INDEX IF NOT EXISTS decision_audits_open_idx ON decision_audits (decision_ref) WHERE completed_at IS NULL;

-- RLS + grants, mirrored from the peer governance tables rather than invented. CREATE TABLE
-- inherits Supabase's default privileges, which on this project hand `anon` SELECT and
-- `authenticated` INSERT/UPDATE/DELETE -- so a freshly created governance table is BOTH readable
-- by the anon PostgREST role and DELETABLE by any authenticated one. Every peer
-- (strategic_decisions, lane_tasks) has RLS on, zero anon/authenticated grants, and exactly two
-- policies. An audit record that a non-owner can quietly DELETE is not an audit record.
-- Caught by comparing against the peers after apply, not by the migration having thought of it.
REVOKE ALL ON decision_audits FROM anon, authenticated;
GRANT SELECT ON decision_audits TO console_readonly;
ALTER TABLE decision_audits ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS decision_audits_console_ro ON decision_audits;
CREATE POLICY decision_audits_console_ro ON decision_audits FOR SELECT TO console_readonly USING (true);

DROP POLICY IF EXISTS decision_audits_service_only ON decision_audits;
CREATE POLICY decision_audits_service_only ON decision_audits TO service_role USING (true) WITH CHECK (true);

COMMENT ON TABLE decision_audits IS
    'CAI-RESP-987: one row per (decision, named auditor). This is the thing that replaces waiting. '
    'An audit is ASSIGNED to a body, and it closes with a verdict that STATES WHAT WAS CHECKED. '
    '"could_not_verify" is a first-class outcome and is deliberately NOT a pass -- it blocks the '
    'close, because a check that could not be run is exactly the state the old window rendered as '
    'acceptance.';
COMMENT ON COLUMN decision_audits.verdict IS
    'accepted | rejected | could_not_verify. NULL = still in flight. could_not_verify does NOT '
    'close the decision: it is visible, and it holds. Rounding "I could not check this" to "fine" '
    'is the whole failure mode (orch-console reported "could not measure" rather than ship a '
    'number on 2026-08-16, and that is the standard this column encodes).';
COMMENT ON COLUMN decision_audits.checks_performed IS
    'What the auditor ACTUALLY DID -- the commands, the files, the rows read at source. Required '
    'before an audit can complete. A verdict with no stated checks is absence-of-signal.';
COMMENT ON COLUMN decision_audits.auditor_agent IS
    'The body doing the looking. MUST NOT be the decider -- enforced in code by '
    'trg_decision_audits_not_self, not by convention (lane_tasks #62 acceptance criterion 3).';

-- ---------------------------------------------------------------------------------------------
-- 3. AUDITOR != DECIDER — ONE definition of the rule, consumed everywhere
-- ---------------------------------------------------------------------------------------------
-- cc-quality's sharpest finding on PR #75 was not the bypass but that the GUARD carried its own
-- second copy of the normalisation and so was blind in the identical spot -- structurally
-- incapable of catching the bug it existed to catch. So the rule is defined ONCE here and the
-- trigger, the view and the close function all CONSUME it. (Same discipline the DeepSeek harness
-- assessment flagged as worth stealing: no privileged core, one definition.)

CREATE OR REPLACE FUNCTION public.decision_audit_actor_norm(p_actor text)
RETURNS text
LANGUAGE sql IMMUTABLE
AS $$
    SELECT CASE
        WHEN p_actor IS NULL THEN NULL
        -- The console body IS the orchestrator lane; 'cc-' is the lane-body prefix.
        WHEN lower(btrim(p_actor)) = 'orch-console' THEN 'orchestrator'
        WHEN lower(btrim(p_actor)) LIKE 'cc-%' THEN substring(lower(btrim(p_actor)) from 4)
        ELSE lower(btrim(p_actor))
    END
$$;

COMMENT ON FUNCTION public.decision_audit_actor_norm(text) IS
    'Normalise an agent id to its lane. cc-quality -> quality, orch-console -> orchestrator. '
    'ONE definition, referenced by the trigger, the view and the close function -- never copied.';

CREATE OR REPLACE FUNCTION public.decision_audit_conflict(p_auditor text, p_decider text)
RETURNS boolean
LANGUAGE sql IMMUTABLE
AS $$
    -- Component match on a '-' boundary in BOTH directions, plus numeric-instance stripping.
    -- PR #75's F-CRIT was that every real agent id is SUFFIXED (cc-shipforge-1, cc-ihsanos-qa-1),
    -- so an exact match let 9 of 22 live bodies through undetected -- the gate was effectively
    -- OFF for every multi-instance lane. Measured then, encoded now.
    -- It fails SAFE: an over-match REFUSES the audit (costs a message) and can never silently
    -- certify. Over-blocking is the affordable error; under-blocking certifies unchecked work.
    SELECT CASE
        WHEN p_auditor IS NULL OR p_decider IS NULL THEN false
        ELSE (
            WITH n AS (
                SELECT regexp_replace(decision_audit_actor_norm(p_auditor), '-[0-9]+$', '') AS a,
                       regexp_replace(decision_audit_actor_norm(p_decider), '-[0-9]+$', '') AS d
            )
            SELECT n.a = n.d OR n.a LIKE n.d || '-%' OR n.d LIKE n.a || '-%' FROM n
        )
    END
$$;

COMMENT ON FUNCTION public.decision_audit_conflict(text, text) IS
    'TRUE when an auditor and a decider are the same body or the same lane. The auditor exists so '
    'the decision is looked at by something that does not share the decider''s blind spot; a body '
    'in the same lane shares its context, tooling and worktree. Lane granularity, deliberately.';

CREATE OR REPLACE FUNCTION public.enforce_decision_audit_not_self()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_decided_by text;
BEGIN
    SELECT decided_by INTO v_decided_by
      FROM strategic_decisions
     WHERE decision_ref = NEW.decision_ref;

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

DROP TRIGGER IF EXISTS trg_decision_audits_not_self ON decision_audits;
CREATE TRIGGER trg_decision_audits_not_self
    BEFORE INSERT OR UPDATE ON decision_audits
    FOR EACH ROW EXECUTE FUNCTION enforce_decision_audit_not_self();

-- ---------------------------------------------------------------------------------------------
-- 4. "DOES THIS NEED AN AUDIT" — one definition, consumed by the view AND the timeout enforcer
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.decision_audit_required(p_decision_ref text)
RETURNS boolean
LANGUAGE sql STABLE
AS $$
    SELECT COALESCE((
        SELECT sd.audit_tier = 'FULL'
               -- An ASSIGNED audit BINDS regardless of tier. Once a body has been told to look,
               -- a clock must not be able to close the decision out from under it -- that would
               -- make the whole mechanism a race against the enforcer.
               OR EXISTS (SELECT 1 FROM decision_audits da
                           WHERE da.decision_ref = sd.decision_ref
                             AND da.completed_at IS NULL)
          FROM strategic_decisions sd
         WHERE sd.decision_ref = p_decision_ref
    ), false)
$$;

COMMENT ON FUNCTION public.decision_audit_required(text) IS
    'TRUE when this decision must NOT be closed by the passive timeout: either it is tiered FULL, '
    'or an audit is assigned and still open. ONE definition -- consumed by decision_audit_state '
    'and by enforce_challenge_window_timeouts, so the board and the enforcer cannot drift apart.';

-- ---------------------------------------------------------------------------------------------
-- 5. THE BOARD — renders the honest state, including the 941 that closed on silence
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE VIEW decision_audit_state AS
SELECT
    -- Columns enumerated explicitly, never `sd.*`: with `sd.*` a future ADD COLUMN re-expands
    -- into the MIDDLE of the output list and CREATE OR REPLACE VIEW (append-only) then FAILS,
    -- making this migration un-re-appliable. cc-quality's F3 on 047, confirmed empirically twice.
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
        -- Ordered most-alarming first. Every arm is an AFFIRMATIVE reading of a signal that is
        -- PRESENT; nothing reads clean because a field happened to be empty.
        WHEN a.n_rejected > 0                              THEN 'AUDIT-REJECTED'
        -- Deliberately NOT a pass and deliberately its own bucket. An auditor who could not run
        -- the check is the exact state the old window rendered as acceptance.
        WHEN a.n_could_not_verify > 0                      THEN 'COULD-NOT-VERIFY'
        WHEN a.n_open > 0                                  THEN 'AUDIT-IN-FLIGHT'
        WHEN sd.challenge_status = 'accepted_by_audit'     THEN 'AUDITED-ACCEPTED'
        -- The honest reading of the 941. Not 'accepted' -- nobody looked; a clock ran out.
        WHEN sd.challenge_status = 'accepted_by_timeout'   THEN 'CLOSED-ON-SILENCE'
        WHEN sd.audit_tier = 'FULL'                        THEN 'AUDIT-OWED'
        WHEN sd.challenge_status = 'challenge_window'      THEN 'WINDOW-OPEN'
        -- Somebody judged, on the record, that this needs no audit. Distinct from NULL.
        WHEN sd.audit_tier = 'NONE'                        THEN 'AUDIT-NOT-REQUIRED'
        -- Closed by a path that predates this mechanism -- somebody DID act on these (an explicit
        -- 'accepted', a supersession, an implementation). Split out of UNTIERED deliberately:
        -- 334 of them read UNTIERED in the first cut, which says "nobody ever judged this" about
        -- rows a person actually accepted. That is 047's F5 in a new place -- the misleading
        -- bucket is the one a consumer skips -- so the two are named apart.
        WHEN sd.challenge_status IN ('accepted', 'implemented', 'superseded', 'informational',
                                     'overridden', 'challenged', 'cai_review_requested')
                                                           THEN 'CLOSED-OTHER'
        -- The analogue of 047's DECLARED-SILENT and 048's no_criteria: nobody ever made the
        -- tiering judgement, and nothing else has happened either. Named as its own state rather
        -- than folded into "not required", because an unmade judgement rendered as a made one is
        -- the whole defect.
        ELSE 'UNTIERED'
    END AS audit_state,
    -- Affirmative boolean: green requires a TRUE here, never the absence of a flag. Built from
    -- the SAME single-sourced terms as the label so the two cannot be tuned apart (047 F1).
    (
        sd.challenge_status = 'accepted_by_audit'
        AND a.n_accepted > 0
        AND a.n_open = 0
        AND a.n_rejected = 0
        AND a.n_could_not_verify = 0
    ) AS is_audit_closed,
    -- The Q4-style measurement, exposed so it can be watched rather than assumed: how much of the
    -- corpus nobody has tiered. If this stays at 100% it means the tier field is decorative and
    -- tiering has to move into decision CREATION -- the same deadline cc-quality set on
    -- lane_tasks.no_criteria.
    (sd.audit_tier IS NULL) AS untiered
FROM strategic_decisions sd
CROSS JOIN LATERAL (
    SELECT
        count(*)                                                   AS n_assigned,
        count(*) FILTER (WHERE da.completed_at IS NULL)             AS n_open,
        count(*) FILTER (WHERE da.verdict = 'accepted')             AS n_accepted,
        count(*) FILTER (WHERE da.verdict = 'rejected')             AS n_rejected,
        count(*) FILTER (WHERE da.verdict = 'could_not_verify')     AS n_could_not_verify,
        array_remove(array_agg(da.auditor_agent ORDER BY da.assigned_at), NULL) AS auditors
      FROM decision_audits da
     WHERE da.decision_ref = sd.decision_ref
) a
WHERE COALESCE(sd.is_test, false) = false;

COMMENT ON VIEW decision_audit_state IS
    'CAI-RESP-987: what actually happened to each decision, as opposed to what its status claims. '
    'CLOSED-ON-SILENCE is the honest name for accepted_by_timeout -- 941 rows on the day this '
    'landed, none of which anybody looked at. COULD-NOT-VERIFY is its own state and is not a '
    'pass. UNTIERED means nobody judged whether it needed an audit, which is NOT the same as '
    'deciding it did not. Read is_audit_closed, never challenge_status alone.';

-- ---------------------------------------------------------------------------------------------
-- 6. THE CLOSE PATH — the only way to reach accepted_by_audit
-- ---------------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.close_decision_by_audit(p_decision_ref text, p_closed_by text)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v RECORD;
BEGIN
    SELECT * INTO v FROM decision_audit_state WHERE decision_ref = p_decision_ref;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such decision (or it is a test row): %', p_decision_ref;
    END IF;

    IF v.n_accepted = 0 THEN
        RAISE EXCEPTION 'cannot close %: no completed audit with verdict=accepted', p_decision_ref
            USING HINT = 'An audit must have RUN. This is CAI-978 -- a control is not satisfied until it executes.';
    END IF;
    IF v.n_open > 0 THEN
        RAISE EXCEPTION 'cannot close %: % audit(s) still in flight', p_decision_ref, v.n_open;
    END IF;
    IF v.n_rejected > 0 THEN
        RAISE EXCEPTION 'cannot close %: an auditor REJECTED it', p_decision_ref;
    END IF;
    -- The load-bearing arm. "Could not verify" must never round to acceptance.
    IF v.n_could_not_verify > 0 THEN
        RAISE EXCEPTION 'cannot close %: an auditor could NOT VERIFY it', p_decision_ref
            USING HINT = 'could_not_verify is a visible outcome, not a pass. Resolve it or reassign.';
    END IF;

    UPDATE strategic_decisions
       SET challenge_status = 'accepted_by_audit',
           updated_at       = now()
     WHERE decision_ref = p_decision_ref
       AND challenge_status IN ('challenge_window', 'unchallenged');

    IF NOT FOUND THEN
        RETURN 'skipped_not_open';
    END IF;

    INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, requires_response)
    VALUES (
        COALESCE(p_closed_by, 'substrate'), 'cai', 'decision',
        p_decision_ref || ': CLOSED accepted_by_audit (' || array_to_string(v.auditors, ', ') || ')',
        'Closed by ASSIGNED AUDIT rather than by timeout (CAI-RESP-987). Auditors: '
            || array_to_string(v.auditors, ', ')
            || E'.\nRead decision_audits.checks_performed for what was actually checked.',
        false
    );

    RETURN 'closed';
END;
$$;

COMMENT ON FUNCTION public.close_decision_by_audit(text, text) IS
    'The ONLY path to accepted_by_audit. Refuses unless a non-self audit COMPLETED with '
    'verdict=accepted, nothing is still in flight, and nothing was rejected or could-not-verify. '
    'Written as an executable path and EXERCISED at apply time, because 047 shipped a state that '
    'was unreachable by construction and would have looked correct forever.';

-- ---------------------------------------------------------------------------------------------
-- 7. THE ENFORCER — stops closing on silence for anything that owes an audit
-- ---------------------------------------------------------------------------------------------
-- This is the actual replacement, and without it the mechanism is decoration: a FULL-tier
-- decision would still be closed by the clock every 5 minutes, and the audit would be a race.
-- It FAILS SAFE -- the row stays OPEN and visible as AUDIT-OWED rather than closing green.
-- The known cost, stated rather than discovered later: if audits are not staffed, FULL-tier
-- windows ACCUMULATE. That is deliberate. An unclosed decision is visible; a falsely-closed one
-- is not, and 941 of those are why this migration exists.

CREATE OR REPLACE FUNCTION public.enforce_challenge_window_timeouts(test_mode boolean DEFAULT false)
 RETURNS TABLE(decision_ref text, action text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
-- #variable_conflict directive from Fix 2: plpgsql OUT variable `decision_ref`
-- shadows table column in INSERT ... ON CONFLICT. Prefer column when names clash.
DECLARE
  mode TEXT;
  rec RECORD;
BEGIN
  SELECT value INTO mode
    FROM orchestrator_runtime_config
   WHERE key = 'challenge_enforcer_mode';
  IF mode IS NULL THEN
    mode := 'dry_run';
  END IF;

  FOR rec IN
    SELECT sd.decision_ref, sd.challenge_status, sd.challengeable_until
      FROM strategic_decisions sd
     WHERE sd.challenge_status = 'challenge_window'
       AND sd.challengeable_until IS NOT NULL
       AND sd.challengeable_until < now()
       AND sd.decided_at < now() - interval '1 hour'
       -- BUG-031: is_test predicate inverts based on test_mode parameter.
       -- test_mode=FALSE (prod) → matches is_test=FALSE rows only.
       -- test_mode=TRUE  (test) → matches is_test=TRUE rows only.
       AND sd.is_test = test_mode
  LOOP
    -- CAI-RESP-987. Consumes decision_audit_required() rather than re-implementing the rule, so
    -- the enforcer and the board can never disagree about what owes an audit.
    IF decision_audit_required(rec.decision_ref) THEN
      RETURN QUERY SELECT rec.decision_ref, 'skipped_audit_required'::TEXT;
      CONTINUE;
    END IF;

    IF mode = 'dry_run' THEN
      INSERT INTO challenge_enforcer_dryrun_log
        (decision_ref, current_challenge_status, challengeable_until, proposed_new_status)
      VALUES
        (rec.decision_ref, rec.challenge_status, rec.challengeable_until, 'accepted_by_timeout')
      ON CONFLICT (decision_ref) DO NOTHING;
      RETURN QUERY SELECT rec.decision_ref, 'logged'::TEXT;
    ELSE
      -- Race guard (from Fix 2 B5 amendment): concurrent challenge flip protection
      UPDATE strategic_decisions
         SET challenge_status = 'accepted_by_timeout',
             updated_at = now()
       WHERE strategic_decisions.decision_ref = rec.decision_ref
         AND strategic_decisions.challenge_status = 'challenge_window';
      IF FOUND THEN
        RETURN QUERY SELECT rec.decision_ref, 'flipped'::TEXT;
      ELSE
        RETURN QUERY SELECT rec.decision_ref, 'skipped_raced'::TEXT;
      END IF;
    END IF;
  END LOOP;
END;
$function$;
