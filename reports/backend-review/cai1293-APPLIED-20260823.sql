-- ============================================================================
-- CAI-1293 decision-audit mechanism fix — AS APPLIED TO LIVE SUBSTRATE
-- Applied: 2026-08-23 by orch-console (Nazim). Grant: CAI-RESP-1300 (both auditors PASS)
--   + CAI-RESP-1301 (apply-under-discretion for the pure-reorder fix).
-- Source proposal: reports/proposals/cai1293-mechanism-fix.proposal.sql @ git 23e1970
-- Applier changes vs the raw 23e1970 artifact (end-state byte-identical, verified):
--   1. REORDER: DROP old audit_tier CHECK moved BEFORE the LEGACY backfill (the granted
--      order backfilled before widening the CHECK -> as-written aborted on the live
--      {FULL,NONE} constraint; scratch CTAS copy hid it. cai CAI-RESP-1301 blessed the reorder).
--   2. B.3 view re-decl + PART D digest = APPLY-TIME artifacts constructed from LIVE defs
--      (regen + surgical additions, diff-verified: 4 view additions, 1 digest line).
-- Wet-prove: 27/27 PROD-FIDELITY on the REAL substrate (BEGIN..ROLLBACK), then committed.
-- Post-commit verified fresh-conn: append-only role matrix, backfill 0-NULL/1521-LEGACY,
--   guard+SECDEF, nonconforming verdict, view LEGACY-CANDIDATE=16/LEGACY=363, digest filter.
-- Closes CAI-991 / CAI-996 / CAI-1009. Residual (a) live (log=detection), (b) tracked.
-- ============================================================================

BEGIN;

-- ---- PART A/B/C body (reordered) ----

-- ---------------------------------------------------------------------------
-- PART A — audit_tier mandatory + a drop-guard that BLOCKS the dodge AND records
--          EVERY tier change (CAI-988/F1 + CAI-1009). Design confirmed Nazim #32152:
--          do BOTH (block the dodge, log every change) — the safe maximum.
-- ---------------------------------------------------------------------------

-- A.0 — a dedicated tier-change log. decision_tier_escalations is (decision_ref,
--       escalated_at) only — it cannot record actor/old/new/reason, and "escalation"
--       means a RAISE, not a drop. This new table is CAI-1009's "record every FULL->NONE"
--       (and every other tier change) so a change is NEVER unrecorded again.
CREATE TABLE IF NOT EXISTS decision_tier_changes (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_ref text        NOT NULL,
    changed_at   timestamptz NOT NULL DEFAULT now(),
    actor        text,                         -- app.current_agent_id at change time (may be NULL if unset)
    old_tier     text,
    new_tier     text        NOT NULL,
    reason       text,                         -- app.tier_change_reason GUC, if the caller set one
    direction    text        NOT NULL          -- 'raise' | 'drop' | 'set' (derived)
);

-- A.0b — LOCK DOWN the log (cc-storefront #32230/#32263 F3+F4 / cc-quality #32253 F1-a/F1-b /
--        CAI-1018/1019 deny-by-default). A bare CREATE TABLE inherits the substrate pg_default_acl —
--        which on public grants anon=SELECT, authenticated=full DML, AND service_role=arwdDxtm (full
--        DML) on EVERY new table. So the "never unrecorded again" trail would be anon-readable and
--        authenticated- AND service_role-ERASABLE, i.e. the guarantee is hollow. Two teeth, both
--        wet-proven prod-fidelity (a scratch schema has NO default ACL and is BLIND to this — F3 slipped
--        rev1-3 for exactly that reason):
--        (1) DENY-BY-DEFAULT incl service_role (F3): REVOKE ALL from PUBLIC,anon,authenticated,service_role
--            then GRANT SELECT,INSERT to service_role only. service_role has rolbypassrls=true, so RLS
--            does NOT restrain it — APPEND-ONLY for service_role is enforced by this GRANT (no UPDATE/
--            DELETE/TRUNCATE), NOT by RLS. Additive GRANT alone would leave the inherited full-DML.
--        (2) RLS POLICIES (F4): RLS is ON, so a GRANT with NO policy reads 0 rows for a non-bypassrls
--            role. console_readonly (rolbypassrls=false) needs an explicit SELECT policy or its GRANT is
--            DEAD; all 65 other RLS'd governance tables carry a *_console_ro policy. Mirror the sibling
--            decision_audits' policy pair EXACTLY (console_ro SELECT + service_only ALL) — the service
--            policy is inert (bypassrls) but kept for parity + defense if bypassrls ever flips (the GRANT
--            still holds append-only either way). NB decision_audits is deliberately MUTABLE (full DML
--            grant); we intentionally keep the STRICTER SELECT,INSERT grant here.
ALTER TABLE decision_tier_changes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON decision_tier_changes FROM PUBLIC, anon, authenticated, service_role;  -- F3: deny-by-default incl service_role
GRANT  SELECT, INSERT ON decision_tier_changes TO service_role;   -- append-only: NO update/delete/truncate
GRANT  SELECT          ON decision_tier_changes TO console_readonly;
CREATE POLICY decision_tier_changes_console_ro   ON decision_tier_changes FOR SELECT TO console_readonly USING (true);           -- F4: console read (RLS-on needs a policy)
CREATE POLICY decision_tier_changes_service_only ON decision_tier_changes FOR ALL    TO service_role     USING (true) WITH CHECK (true);  -- sibling parity (inert under bypassrls; GRANT is the append-only gate)

-- A.1 — BACKFILL the NULLs (⚠️ cai fork; B=LEGACY drafted). Must precede NOT NULL.
--       'LEGACY' = these 1514 predate mandatory tiering; the NOT-NULL + guard bind
--       going FORWARD without back-asserting a NONE audit-judgment on history.
-- [APPLIER REORDER — Nazim CAI-1293 apply, wet-prove-caught 2026-08-23]: the granted .sql
-- backfilled LEGACY BEFORE widening the CHECK, so the UPDATE violated the live {FULL,NONE} check
-- and the as-written apply aborted. Drop the old CHECK first (the later DROP IF EXISTS is now a no-op).
-- Pure ordering fix; end-state identical to the audited artifact.
ALTER TABLE strategic_decisions DROP CONSTRAINT IF EXISTS strategic_decisions_audit_tier_check;
UPDATE strategic_decisions SET audit_tier = 'LEGACY' WHERE audit_tier IS NULL;

-- A.2 — tighten the CHECK: drop the "IS NULL OR" branch, add 'LEGACY' (fork B).
ALTER TABLE strategic_decisions DROP CONSTRAINT IF EXISTS strategic_decisions_audit_tier_check;
ALTER TABLE strategic_decisions ADD  CONSTRAINT strategic_decisions_audit_tier_check
    CHECK (audit_tier = ANY (ARRAY['FULL','NONE','LEGACY']));   -- fork B set; A would be {FULL,NONE}

-- A.3 — mandatory at creation (CAI-988/F1: NOT NULL, no default — a decision must be
--       tiered on purpose, never defaulted).
ALTER TABLE strategic_decisions ALTER COLUMN audit_tier SET NOT NULL;

-- A.4 — the guard trigger. BEFORE UPDATE OF audit_tier. BLOCKS the CAI-1009 dodge
--       (FULL -> non-FULL while the decision is still closeable-by-timeout, i.e. in the
--       challenge window) AND records EVERY actual tier change into decision_tier_changes.
CREATE OR REPLACE FUNCTION enforce_audit_tier_change_guard()
RETURNS trigger LANGUAGE plpgsql
-- SECURITY DEFINER (cc-storefront #32230): the log is service_role-only INSERT (A.0b), but a legit
-- tier UPDATE may come from a non-service_role caller; the trigger must be able to write the record
-- as its OWNER, or the lock-down would break legit tier changes. Pinned search_path (SECDEF hygiene).
SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
DECLARE
    v_actor  text := current_setting('app.current_agent_id', true);
    v_reason text := current_setting('app.tier_change_reason', true);
    v_dir    text;
BEGIN
    IF NEW.audit_tier IS NOT DISTINCT FROM OLD.audit_tier THEN
        RETURN NEW;                                  -- no tier change; nothing to guard/record
    END IF;
    IF NEW.audit_tier IS NULL THEN
        RETURN NEW;   -- a NULL new tier is rejected cleanly by the column NOT NULL (below the BEFORE
                      -- trigger); don't let the NULL-blind `<> 'FULL'` dodge check or the record INSERT
                      -- misfire on it (wet-prove finding 2026-08-23).
    END IF;

    -- BLOCK the dodge: a FULL decision dropped to non-FULL while still closeable by
    -- timeout (challenge_window/unchallenged) is the exact CAI-1009 hole — it flips
    -- decision_audit_required() FALSE and a 0-audit decision closes accepted_by_timeout.
    IF OLD.audit_tier = 'FULL'
       AND NEW.audit_tier <> 'FULL'
       AND NEW.challenge_status = ANY (ARRAY['challenge_window','unchallenged']) THEN
        RAISE EXCEPTION
          'CAI-1009: refusing to drop audit_tier FULL->% on % while it is still closeable by timeout',
          NEW.audit_tier, NEW.decision_ref
          USING HINT = 'A FULL decision in its challenge window must be AUDITED, not silently un-tiered. '
                       'Resolve or close its audit first, then re-tier.';
    END IF;

    v_dir := CASE
        WHEN OLD.audit_tier = 'FULL' AND NEW.audit_tier <> 'FULL' THEN 'drop'
        WHEN NEW.audit_tier = 'FULL' AND OLD.audit_tier <> 'FULL' THEN 'raise'
        ELSE 'set'
    END;
    INSERT INTO decision_tier_changes (decision_ref, actor, old_tier, new_tier, reason, direction)
    VALUES (NEW.decision_ref, v_actor, OLD.audit_tier, NEW.audit_tier,
            NULLIF(v_reason, ''), v_dir);
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_audit_tier_change_guard ON strategic_decisions;
CREATE TRIGGER trg_audit_tier_change_guard
    BEFORE UPDATE OF audit_tier ON strategic_decisions
    FOR EACH ROW EXECUTE FUNCTION enforce_audit_tier_change_guard();

-- ⚠️ DOCUMENTED RESIDUAL (cc-quality #32207, wet-proven; cai's call — NOT fixed here):
-- This guard closes the DIRECT single-UPDATE dodge. A multi-step RE-WINDOW path still reaches
-- the harm: (1) move a FULL decision OUT of window (challenge_status='accepted_by_audit'), then
-- drop audit_tier FULL->NONE — ALLOWED (out of window) and now RECORDED; (2) re-set
-- challenge_status='challenge_window' (this tier trigger does not fire on a challenge_status
-- change, and no challenge_status-lifecycle guard exists); (3) decision_audit_required()=FALSE
-- => a 0-audit was-FULL decision closes accepted_by_timeout. It needs raw challenge_status
-- UPDATEs and NO NEW capability (an actor who can set 'accepted_by_audit' has already closed it),
-- and unlike original CAI-1009 the FULL->NONE drop now leaves a trail in decision_tier_changes.
-- It is a challenge_status-LIFECYCLE gap, ORTHOGONAL to the tier axis. Options for cai:
--   (a) accept as a documented residual; lean on the decision_tier_changes log for detection.
--   (b) add a challenge_status transition guard (no re-entry to 'challenge_window' from a closed
--       state) — cleaner, a SEPARATE change on the lifecycle axis.
--   (c) block FULL->non-FULL when 0 completed audits REGARDLESS of window (tightens THIS guard).
-- cc-quality leans (a)+(b-later). Surfaced to cai; this proposal ships (a) unless cai directs (c).

-- ---------------------------------------------------------------------------
-- PART B — add the 'nonconforming' verdict (CAI-991 / 987-F4) + make the whole
--          mechanism treat it coherently (a terminal NON-pass, like rejected/cnv).
-- ---------------------------------------------------------------------------

-- B.1 — allow the verdict value.
ALTER TABLE decision_audits DROP CONSTRAINT IF EXISTS decision_audits_verdict_check;
ALTER TABLE decision_audits ADD  CONSTRAINT decision_audits_verdict_check
    CHECK (verdict IS NULL OR verdict = ANY (ARRAY['accepted','rejected','could_not_verify','nonconforming']));

-- B.2 — 'nonconforming' is an unresolved-until-acted negative, exactly like rejected/cnv.
CREATE OR REPLACE FUNCTION public.decision_audit_unresolved(
    p_verdict text, p_completed_at timestamptz, p_resolved_at timestamptz)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $fn$
    SELECT CASE
        WHEN p_completed_at IS NULL THEN true
        WHEN p_verdict IN ('could_not_verify','rejected','nonconforming') AND p_resolved_at IS NULL THEN true
        ELSE false
    END
$fn$;

-- B.3 — surface n_nonconforming in decision_audit_state (added at the END of the SELECT
--       list — CREATE OR REPLACE VIEW only allows appending columns). Verbatim re-declare
--       of the existing view + the one new aggregate + one new final column.
--       NOTE FOR AUDITORS: this re-declaration must be byte-checked against the live
--       viewdef at apply time (it may have drifted since 2026-08-23); only the two marked
--       "CAI-1293" lines are new.
-- [FULL VIEW RE-DECLARATION is an APPLY-TIME regen (against drift), with THREE additions —
--  regenerate from live `pg_get_viewdef('decision_audit_state', true)` and add exactly:
--    (1) LATERAL:   count(*) FILTER (WHERE da.verdict = 'nonconforming') AS n_nonconforming
--    (2) top-level: a.n_nonconforming
--    (3) audit_state CASE — make LEGACY a DISTINCT queryable bucket per CAI-RESP-1296 (NOT a silent
--        NONE-equivalent) AND keep cai's deferred retro-tier queue visible to audit_board_digest (F2).
--        ⚠️ PLACEMENT (wet-prove-caught): the LEGACY-CANDIDATE arm must sit PARALLEL to the
--        UNTIERED-CANDIDATE arm — i.e. RIGHT AFTER it and BEFORE the `challenge_status='challenge_window'
--        -> 'WINDOW-OPEN'` arm — with the SAME in-window condition, else an in-window LEGACY candidate
--        is swallowed by WINDOW-OPEN and never reaches the digest:
--          WHEN sd.audit_tier = 'LEGACY' AND (sd.challenge_status = ANY (ARRAY['challenge_window','unchallenged']))
--               AND decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning)
--               THEN 'LEGACY-CANDIDATE'   -- cai's deferred retro-tier queue (F2 depends on this)
--        And a plain closed-LEGACY arm before the 'NONE'->'AUDIT-NOT-REQUIRED' arm:
--          WHEN sd.audit_tier = 'LEGACY' THEN 'LEGACY'
--  Kept out of the wet-prove body only to avoid shipping a stale 40-line view; the behavioural
--  arms (A/B/C) below do NOT depend on the view's new columns (close computes n_nonconforming
--  + distinct lenses DIRECTLY), and the wet-prove proves LEGACY is already column-queryable.]

-- ---------------------------------------------------------------------------
-- PART C — close_decision_by_audit: block 'nonconforming' (B.4) AND require >=2 DISTINCT
--          completed accepted lenses to close a FULL-tier decision (CAI-996).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.close_decision_by_audit(p_decision_ref text, p_closed_by text)
RETURNS text LANGUAGE plpgsql AS $fn$
DECLARE
    v RECORD;
    v_distinct_accepted_lenses int;
    v_n_nonconforming int;
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
    IF v.n_could_not_verify > 0 THEN
        RAISE EXCEPTION 'cannot close %: an auditor could NOT VERIFY it', p_decision_ref
            USING HINT = 'could_not_verify is a visible outcome, not a pass. Resolve it or reassign.';
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
$fn$;

-- ---------------------------------------------------------------------------
-- PART D — keep audit_board_digest's untiered-candidate watch alive across the backfill
--          (cc-storefront #32230 / cai CAI-RESP-1298). The daily digest (pg_cron 0 8 * * *)
--          counts open should-be-tiered decisions via `audit_state = 'UNTIERED-CANDIDATE'`
--          (and `untiered` = audit_tier IS NULL). The NULL->LEGACY backfill takes both to 0,
--          so the ~20 in-window retro-tier candidates CAI-1296 wants VISIBLE would silently
--          drop off = a false all-clear. Fix: also count the new LEGACY-CANDIDATE bucket.
--
-- APPLY-TIME EDIT (Nazim byte-verifies at apply, like the view re-decl): regenerate
-- audit_board_digest from live `pg_get_functiondef`, then in its section-4 count change EXACTLY:
--     count(*) FILTER (WHERE audit_state = 'UNTIERED-CANDIDATE')
--   ->
--     count(*) FILTER (WHERE audit_state IN ('UNTIERED-CANDIDATE','LEGACY-CANDIDATE'))
-- (No other line changes.) This is LOAD-BEARING on the view's new LEGACY-CANDIDATE arm (B.3) —
-- both must land together. Proven in the wet-prove (post-backfill the digest still counts ~20).
--
-- NOTE re B.3: the view re-declaration's LEGACY-CANDIDATE arm is now LOAD-BEARING (the digest
-- reads it), not observability-only — regen the view WITH the arm before/with this digest edit.


-- ---- B.3 view re-declaration (apply-time, from live def) ----
CREATE OR REPLACE VIEW decision_audit_state AS
 SELECT sd.decision_ref,
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
            WHEN a.n_rejected > 0 THEN 'AUDIT-REJECTED'::text
            WHEN a.n_could_not_verify > 0 THEN 'COULD-NOT-VERIFY'::text
            WHEN a.n_stale > 0 THEN 'AUDIT-STALE'::text
            WHEN a.n_open > 0 THEN 'AUDIT-IN-FLIGHT'::text
            WHEN sd.challenge_status = 'accepted_by_audit'::text THEN 'AUDITED-ACCEPTED'::text
            WHEN sd.challenge_status = 'accepted_by_timeout'::text THEN 'CLOSED-ON-SILENCE'::text
            WHEN sd.audit_tier = 'FULL'::text AND a.n_assigned = 0 THEN 'AUDIT-OWED-NO-AUDITOR'::text
            WHEN sd.audit_tier = 'FULL'::text THEN 'AUDIT-OWED'::text
            WHEN sd.audit_tier IS NULL AND (sd.challenge_status = ANY (ARRAY['challenge_window'::text, 'unchallenged'::text])) AND decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning) THEN 'UNTIERED-CANDIDATE'::text
            WHEN sd.audit_tier = 'LEGACY'::text AND (sd.challenge_status = ANY (ARRAY['challenge_window'::text, 'unchallenged'::text])) AND decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning) THEN 'LEGACY-CANDIDATE'::text
            WHEN sd.challenge_status = 'challenge_window'::text THEN 'WINDOW-OPEN'::text
            WHEN sd.audit_tier = 'LEGACY'::text THEN 'LEGACY'::text
            WHEN sd.audit_tier = 'NONE'::text THEN 'AUDIT-NOT-REQUIRED'::text
            WHEN sd.challenge_status = ANY (ARRAY['accepted'::text, 'implemented'::text, 'superseded'::text, 'informational'::text, 'overridden'::text, 'challenged'::text, 'cai_review_requested'::text]) THEN 'CLOSED-OTHER'::text
            ELSE 'UNTIERED'::text
        END AS audit_state,
    sd.challenge_status = 'accepted_by_audit'::text AND a.n_accepted > 0 AND a.n_open = 0 AND a.n_rejected = 0 AND a.n_could_not_verify = 0 AS is_audit_closed,
    sd.audit_tier IS NULL AS untiered,
    a.n_stale,
    a.n_escalated,
    a.oldest_open_hours,
    decision_audit_tier_candidate(sd.title, sd.decision, sd.reasoning) AS tier_candidate,
    sd.built_by,
    a.effective_builders,
    a.n_unresolved,
    a.lenses,
    a.n_nonconforming
   FROM strategic_decisions sd
     CROSS JOIN LATERAL ( SELECT count(*) AS n_assigned,
            count(*) FILTER (WHERE da.completed_at IS NULL) AS n_open,
            count(*) FILTER (WHERE da.verdict = 'accepted'::text) AS n_accepted,
            count(*) FILTER (WHERE da.verdict = 'rejected'::text) AS n_rejected,
            count(*) FILTER (WHERE da.verdict = 'could_not_verify'::text) AS n_could_not_verify,
            count(*) FILTER (WHERE da.verdict = 'nonconforming'::text) AS n_nonconforming,
            array_remove(array_agg(da.auditor_agent ORDER BY da.assigned_at), NULL::text) AS auditors,
            count(*) FILTER (WHERE decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at) AND COALESCE(da.completed_at, da.assigned_at) < (now() - make_interval(hours => da.sla_hours))) AS n_stale,
            count(*) FILTER (WHERE da.escalated_at IS NOT NULL) AS n_escalated,
            round(EXTRACT(epoch FROM now() - min(da.assigned_at) FILTER (WHERE da.completed_at IS NULL)) / 3600.0, 1) AS oldest_open_hours,
            array_remove(array_agg(DISTINCT decision_audit_effective_builder(sd.built_by, da.assigned_by)), NULL::text) AS effective_builders,
            count(*) FILTER (WHERE decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at)) AS n_unresolved,
            array_remove(array_agg(DISTINCT da.lens), NULL::text) AS lenses
           FROM decision_audits da
          WHERE da.decision_ref = sd.decision_ref) a
  WHERE COALESCE(sd.is_test, false) = false;

-- ---- PART D digest (apply-time, from live def) ----
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
           count(*) FILTER (WHERE audit_state IN ('UNTIERED-CANDIDATE', 'LEGACY-CANDIDATE'))
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
$function$

COMMIT;
