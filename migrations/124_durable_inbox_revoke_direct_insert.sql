-- 124_durable_inbox_revoke_direct_insert.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Backlog#124's final step (console's bus #85451, Sat 31 Oct): closes the
-- direct-INSERT path on agent_messages that migration 123 (Musa op#34845,
-- bus #78393 ruling) deliberately left open, leaving bus_writer (123) as the
-- only INSERT path other than a guard-trigger-enforced identity check (see
-- GUARD TRIGGER below -- NOT a REVOKE, for a reason discovered during
-- cc-quality's PR#716 review, bus #85462/#85464).
--
-- cto_desktop (unrelated to this migration's own writer inventory, still
-- holds a direct INSERT grant + its own cto_desktop_insert_self RLS policy)
-- is blocked by the guard trigger too, unconditionally -- the trigger does
-- not special-case it. This is a deliberate side effect, not an oversight:
-- console asked whether cto_desktop is a live writer before applying this
-- (bus #85464); if it is, it needs to move onto bus_send/post_agent_message
-- like every other writer did, not get carved out of the gate.
--
-- Gated on a full writer inventory across 4 categories being complete first
-- (123's own header): python psycopg, .sh/psql heredocs, PostgREST/service-key
-- REST calls (all three confirmed CLEAN fleet-wide by
-- scripts/agent_messages_writer_gate.py --require-complete, baseline file now
-- empty), and the 6 postgres-owned DB functions that INSERT into this table
-- directly -- this migration's second half (below) is that 4th category.
--
-- WHY A TRIGGER, NOT JUST A REVOKE (cc-quality's BLOCKING 1, console's design
-- call #85464): `REVOKE INSERT ON agent_messages FROM postgres` is a no-op --
-- postgres OWNS this table (pg_class.relowner), and a table owner's base
-- privileges are implicit, not grant/revoke-removable (this is unrelated to
-- rolbypassrls, an earlier draft of this migration's own header wrongly
-- conflated the two -- rolbypassrls governs RLS-policy bypass only, not base
-- table ACL). Reassigning ownership away from postgres would fix it but is a
-- separate, bigger, more-carefully-reviewed change. A BEFORE INSERT guard
-- trigger sidesteps the whole issue: triggers fire for every row regardless
-- of who owns or has ACL privilege on the table (that is what makes RLS
-- policies and check constraints work on an owner's own writes too), so
-- `RAISE EXCEPTION unless current_user = bus_writer` binds postgres exactly
-- as it binds everyone else. The REVOKE against service_role (a real,
-- effective ACL change -- service_role is not the owner) stays as a second,
-- independent layer.
--
-- TRIGGER ORDERING (console asked this be checked and stated): BEFORE INSERT
-- triggers on the same table/event fire in trigger-NAME alphabetical order.
-- Existing BEFORE INSERT triggers on agent_messages: trg_agent_messages_cai_gate,
-- trg_agent_messages_provenance. This migration's trg_agent_messages_bus_writer_gate
-- sorts before both ('b' < 'c' < 'p'), so it fires first. This is safe either
-- way: the gate only reads current_user, not any NEW.* column either of the
-- other two triggers sets or reads, so there is no ordering dependency in
-- either direction -- firing first just means a disallowed caller is
-- rejected before the other two do any work, which is the preferable order
-- if anything (fail fastest, least work done before rejection).
--
-- assert: no_table_privilege service_role public.agent_messages INSERT
-- assert: search_path public.audit_board_digest()
-- assert: search_path public.escalate_full_tier_without_auditor(numeric)
-- assert: search_path public.escalate_stale_decision_audits()
-- assert: search_path public.close_decision_by_audit(text,text)
-- assert: search_path public.trigger_cai_decision_announce()
-- assert: no_execute anon public.trigger_cai_decision_announce()
-- assert: no_execute authenticated public.trigger_cai_decision_announce()
--
-- The guard trigger's own existence+enabled check is NOT expressed as a
-- header assert (apply_migration.py's assert vocabulary -- dropped,
-- no_execute, no_table_privilege, no_sequence_privilege, search_path -- has
-- no "trigger enabled" kind, and extending that shared vocabulary under
-- today's deadline is its own change deserving its own review). Instead it
-- is an inline DO-block check in the migration body itself (below, right
-- after CREATE TRIGGER) that RAISEs and aborts the whole transaction --
-- including under --dry-run -- exactly as a failed header assert would.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- THE 5 LIVE POSTGRES-OWNED DB FUNCTIONS (pg_proc audit against the real
-- substrate, 2026-10-31: 6 functions' bodies contain a literal "INSERT INTO
-- agent_messages" -- post_agent_message is the 6th, already bus_writer-owned
-- since migration 123, so it is unaffected by this REVOKE). Each of the 5
-- below does a raw `INSERT INTO agent_messages` and would break the instant
-- the REVOKE below takes effect, UNLESS it is re-pointed onto bus_writer
-- first, in THIS SAME transaction -- same ordering requirement as migration
-- 123's own OWNER-TO-then-REVOKE sequencing (see that file's "ORDER/AUTHORITY"
-- comment): the REVOKE happens only after every function that needs the
-- privilege has been moved off the revoked role.
--
-- 3 are already SECURITY DEFINER with a pinned search_path (audit_board_digest,
-- escalate_full_tier_without_auditor, escalate_stale_decision_audits, all
-- called only by pg_cron as postgres, confirmed via cron.job) -- a plain
-- OWNER TO bus_writer makes them execute AS bus_writer instead of postgres;
-- CREATE OR REPLACE is not used here (unlike migration 134's trigger-body
-- fix) because nothing about these 3 functions' BODIES needs to change, only
-- their owner, and ALTER FUNCTION ... OWNER TO is the narrower statement for
-- that -- it does not redefine or touch SECURITY DEFINER/search_path, which
-- stay exactly as already set.
ALTER FUNCTION audit_board_digest() OWNER TO bus_writer;
ALTER FUNCTION escalate_full_tier_without_auditor(numeric) OWNER TO bus_writer;
ALTER FUNCTION escalate_stale_decision_audits() OWNER TO bus_writer;

-- The other 2 are SECURITY INVOKER today -- OWNER TO alone would do nothing
-- for them (invoker-mode runs as whoever CALLS the function, not its owner),
-- so they need CREATE OR REPLACE to add SECURITY DEFINER, same as OWNER TO
-- for the 3 above. Converting invoker->definer is a real privilege-model
-- change, not routine, done here because it is the only way these 2 keep
-- working without a standing table-level grant to whichever role calls them
-- (postgres for close_decision_by_audit -- no cron job and no in-repo
-- caller found; service_role for trigger_cai_decision_announce, which fires
-- on strategic_decisions writes from CAI, an external system outside this
-- repo -- see this file's own PR notes on both unresolved callers). Checked
-- for new privilege-escalation surface before doing this (not assumed
-- safe): close_decision_by_audit's EXECUTE was already postgres/service_role
-- only (no anon/authenticated hole to begin with); trigger_cai_decision_
-- announce's EXECUTE is additionally revoked from anon/authenticated below,
-- and that revoke is a true no-op behaviourally -- strategic_decisions has
-- RLS enabled with exactly 2 policies (console-readonly SELECT, service_role
-- ALL), so anon/authenticated have never had INSERT/UPDATE on the table and
-- could never have fired this trigger regardless of its EXECUTE grant.
-- search_path is pinned on both, matching the migration 131/134 hardening
-- convention -- close_decision_by_audit had NO search_path pin at all before
-- this (a separate, smaller gap this migration also closes while already
-- touching the function for the OWNER change).
--
-- NOT IDEMPOTENT ON A RAW RE-RUN (disclosed, not hidden -- see this file's
-- test suite): CREATE OR REPLACE here must be issued AS the function's
-- CURRENT owner. That is the applying role (postgres or whoever runs this
-- migration) on the real, one-time apply -- the only scenario that matters,
-- since apply_migration.py's ledger refuses a genuine second real attempt
-- outright, before ever re-running this body. A manual raw re-run bypassing
-- the ledger, after the ALTER below has already moved ownership to
-- bus_writer, WOULD fail here ("must be owner") -- a real, narrow,
-- intentionally-undefended gap rather than one papered over with dynamic
-- SET ROLE/DROP-and-recreate machinery whose own correctness would be
-- harder to verify than the gap it closes. Same for trigger_cai_decision_
-- announce below (which additionally cannot safely self-heal via DROP at
-- all -- a live trigger depends on it, and DROP ... CASCADE would take the
-- trigger down with it).
CREATE OR REPLACE FUNCTION public.close_decision_by_audit(p_decision_ref text, p_closed_by text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v RECORD;
    v_distinct_accepted_lenses int;
    v_n_nonconforming int;
    v_n_unresolved_cnv int;
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

    SELECT count(*) INTO v_n_unresolved_cnv FROM decision_audits
        WHERE decision_ref = p_decision_ref
          AND verdict = 'could_not_verify' AND resolved_at IS NULL;
    IF v_n_unresolved_cnv > 0 THEN
        RAISE EXCEPTION 'cannot close %: an UNRESOLVED auditor could-not-verify remains', p_decision_ref
            USING HINT = 'could_not_verify blocks until independently resolved or reassigned; a RESOLVED could_not_verify no longer blocks. rejected/nonconforming block permanently (CAI-991).';
    END IF;

    SELECT count(*) INTO v_n_nonconforming FROM decision_audits
        WHERE decision_ref = p_decision_ref AND verdict = 'nonconforming';
    IF v_n_nonconforming > 0 THEN
        RAISE EXCEPTION 'cannot close %: an auditor found it NONCONFORMING', p_decision_ref
            USING HINT = 'nonconforming (CAI-991) is a visible outcome, not a pass. Resolve it or rebuild.';
    END IF;

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
$$;

ALTER FUNCTION close_decision_by_audit(text, text) OWNER TO bus_writer;

CREATE OR REPLACE FUNCTION public.trigger_cai_decision_announce()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_msg_id BIGINT;
  v_subject TEXT;
  v_body TEXT;
  v_message_type TEXT;
  v_requires_response BOOLEAN;
  v_to_agent TEXT;
  v_thread_id UUID;
  v_parent_from_agent TEXT;
  v_parent_thread_id UUID;
BEGIN
  IF NEW.source IS DISTINCT FROM 'claude_ai_session'
     OR NEW.challenge_status NOT IN ('challenge_window', 'accepted')
     OR COALESCE(NEW.bypass_review, false) = true
     OR COALESCE(NEW.is_test, false) = true
     OR NEW.announced_by_msg_id IS NOT NULL THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' THEN
    IF OLD.announced_by_msg_id IS NOT NULL
       OR OLD.execution_status = 'implemented' THEN
      RETURN NEW;
    END IF;
  END IF;

  IF NEW.challenge_status = 'challenge_window' THEN
    v_message_type := 'review_request';
    v_subject := NEW.decision_ref || ': ' || NEW.title || ' — for review + challenge';
  ELSE
    v_message_type := 'decision';
    v_subject := NEW.decision_ref || ': ' || NEW.title;
  END IF;

  v_requires_response := COALESCE(NEW.announce_requires_response, false);

  v_body := format(
    E'Decision %s filed by CAI (status: %s).\nFull spec: see strategic_decisions.decision_ref=%s%s\n',
    NEW.decision_ref,
    NEW.challenge_status,
    NEW.decision_ref,
    CASE WHEN NEW.parent_ref IS NOT NULL
         THEN E'\nParent: ' || NEW.parent_ref
         ELSE '' END
  );

  IF NEW.parent_msg_id IS NOT NULL THEN
    SELECT from_agent, thread_id
      INTO v_parent_from_agent, v_parent_thread_id
      FROM agent_messages
     WHERE id = NEW.parent_msg_id;
  END IF;

  v_to_agent := COALESCE(NEW.announce_to_agent, v_parent_from_agent, 'cc-ihsanos');
  v_thread_id := COALESCE(NEW.announce_thread_id, v_parent_thread_id, gen_random_uuid());

  INSERT INTO agent_messages (
    thread_id, from_agent, to_agent, message_type,
    subject, body, requires_response
  ) VALUES (
    v_thread_id, 'cai', v_to_agent, v_message_type,
    v_subject, v_body, v_requires_response
  )
  RETURNING id INTO v_msg_id;

  NEW.announced_by_msg_id := v_msg_id;
  NEW.notified_at := now();

  IF NEW.announce_to_agent IS NULL
     AND v_parent_from_agent IS NULL THEN
    INSERT INTO notification_log (source, decision_ref, channel, recipient, message_text)
    VALUES (
      'bridge_tier3_misroute',
      NEW.decision_ref,
      'agent_messages',
      'cc-ihsanos',
      json_build_object(
        'spawned_msg_id',       v_msg_id,
        'parent_msg_id',        NEW.parent_msg_id,
        'announce_to_agent',    NEW.announce_to_agent,
        'announce_thread_id',   NEW.announce_thread_id,
        'reason',               'Tier-3 fallback fired — populate announce_to_agent or parent_msg_id at INSERT to route correctly per CAI-PROCESS-ROUTING-001'
      )::text
    );
  END IF;

  RETURN NEW;
END;
$$;

ALTER FUNCTION trigger_cai_decision_announce() OWNER TO bus_writer;

-- Close the latent anon/authenticated EXECUTE hole on the trigger function
-- (Supabase's standing ALTER DEFAULT PRIVILEGES rule, same mechanism as
-- migration 123's mark_delivered/ack_message finding) -- a true no-op
-- behaviourally, since strategic_decisions' RLS already blocks anon/
-- authenticated from ever INSERTing/UPDATEing the row that would fire it,
-- but left closed rather than left as a live hole just because nothing
-- currently reaches it. postgres/service_role (the only roles that can
-- actually write strategic_decisions, confirmed via its RLS policies) keep
-- EXECUTE -- restated explicitly as a no-op GRANT so this migration is
-- self-contained regardless of what either role already held.
-- Both the REVOKE and the restating GRANT must run AS bus_writer (not the
-- applying role): once OWNER TO above transfers ownership away, the
-- applying role has no GRANT OPTION left on this function -- same ordering
-- lesson as migration 123's own "ORDER/AUTHORITY" fix (REVOKE/GRANT AFTER
-- OWNER TO, AS the new owner, never before or as anyone else).
SET ROLE bus_writer;
REVOKE EXECUTE ON FUNCTION trigger_cai_decision_announce() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION trigger_cai_decision_announce() TO postgres, service_role;
RESET ROLE;

-- bus_writer's new grant footprint for the 5 functions above, each one tied
-- to a specific table/column/sequence those functions' bodies actually
-- touch (none of this would have been obvious from reasoning about the
-- OWNER change alone -- same caution as migration 123's own grant-footprint
-- section):
--
-- cc-quality review raised trigger_cai_decision_announce's parent_msg_id
-- lookup (`SELECT from_agent, thread_id FROM agent_messages WHERE id =
-- NEW.parent_msg_id`, reached on any threaded CAI announce) as needing a
-- new SELECT grant for bus_writer now that this function runs DEFINER
-- (PR#716, bus #85462, their BLOCKING 2) -- checked, and no new grant is
-- needed: migration 123 already grants bus_writer column-scoped
-- `GRANT SELECT (id, thread_id, from_agent) ON agent_messages`, which is
-- exactly the 3 columns this query touches (has_column_privilege confirms
-- true for all 3 live; has_table_privilege, which the review checked,
-- correctly reports false for a column-only grant but isn't what this
-- query needs). Added a dedicated test
-- (test_trigger_cai_decision_announce_parent_msg_id_lookup_after_124) that
-- exercises exactly this path -- confirmed red/wrong-fixture before this
-- comment's fixture-fidelity fix, green after, independent of any new
-- grant here (none added).
GRANT SELECT ON decision_audits TO bus_writer;
GRANT UPDATE (escalated_at, updated_at) ON decision_audits TO bus_writer;
GRANT SELECT ON strategic_decisions TO bus_writer;
GRANT UPDATE (challenge_status, updated_at) ON strategic_decisions TO bus_writer;
-- decision_audit_state is a plain (non security_invoker) view -- the caller
-- only needs SELECT on the view itself, not on the tables it reads; the
-- view's OWNER's privileges on those tables are what get checked, same as
-- any other classic Postgres view.
GRANT SELECT ON decision_audit_state TO bus_writer;
GRANT INSERT ON audit_board_digest_log TO bus_writer;
GRANT USAGE ON SEQUENCE audit_board_digest_log_id_seq TO bus_writer;
GRANT SELECT, INSERT ON decision_tier_escalations TO bus_writer;
GRANT INSERT ON notification_log TO bus_writer;
GRANT USAGE ON SEQUENCE notification_log_id_seq TO bus_writer;
-- decision_audit_unresolved(text,timestamptz,timestamptz), called inside
-- escalate_stale_decision_audits, already grants EXECUTE TO PUBLIC (checked
-- against the live proacl) -- bus_writer inherits that automatically, no
-- separate grant needed.

-- ALL SIX tables these 5 functions touch have RLS enabled on the live
-- substrate (checked directly, 2026-10-31) -- the column/table-level GRANTs
-- above are necessary but NOT sufficient for any of them, same lesson
-- migration 123 already paid for once on agent_messages: without a matching
-- policy, bus_writer's writes silently affect zero rows (an UPDATE's own
-- IF NOT FOUND branch, never an error) or its reads come back empty, rather
-- than failing loud. Caught here by TDD against a fixture that enables RLS
-- on all 6 (not just agent_messages/strategic_decisions) with the real live
-- policies replicated -- a fixture that skipped this would have made every
-- "still works after 124" test pass trivially regardless of whether these
-- policies existed.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'strategic_decisions'
          AND policyname = 'bus_writer_update'
    ) THEN
        CREATE POLICY bus_writer_update ON strategic_decisions
            FOR UPDATE TO bus_writer USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'decision_audits'
          AND policyname = 'bus_writer_select'
    ) THEN
        CREATE POLICY bus_writer_select ON decision_audits
            FOR SELECT TO bus_writer USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'decision_audits'
          AND policyname = 'bus_writer_update'
    ) THEN
        CREATE POLICY bus_writer_update ON decision_audits
            FOR UPDATE TO bus_writer USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'decision_tier_escalations'
          AND policyname = 'bus_writer_select'
    ) THEN
        CREATE POLICY bus_writer_select ON decision_tier_escalations
            FOR SELECT TO bus_writer USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'decision_tier_escalations'
          AND policyname = 'bus_writer_insert'
    ) THEN
        CREATE POLICY bus_writer_insert ON decision_tier_escalations
            FOR INSERT TO bus_writer WITH CHECK (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'audit_board_digest_log'
          AND policyname = 'bus_writer_insert'
    ) THEN
        CREATE POLICY bus_writer_insert ON audit_board_digest_log
            FOR INSERT TO bus_writer WITH CHECK (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public' AND tablename = 'notification_log'
          AND policyname = 'bus_writer_insert'
    ) THEN
        CREATE POLICY bus_writer_insert ON notification_log
            FOR INSERT TO bus_writer WITH CHECK (true);
    END IF;
END $$;

-- GUARD TRIGGER (this migration's actual headline, replacing a REVOKE that
-- cc-quality's review proved would be a no-op for postgres -- see header).
-- Binds EVERY inserting role, including the table owner, since triggers
-- fire regardless of ACL/ownership. SECURITY INVOKER (no elevation needed --
-- it only reads current_user and either RAISEs or returns NEW unchanged),
-- matching this repo's existing simple guard triggers (e.g.
-- enforce_challenge_window_requires_expiry, enforce_audit_tier_change_guard
-- on strategic_decisions -- neither is SECURITY DEFINER either).
CREATE OR REPLACE FUNCTION agent_messages_bus_writer_gate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_user <> 'bus_writer' THEN
        RAISE EXCEPTION
            'agent_messages: direct INSERT not allowed for role % -- use '
            'bus_send.py / post_agent_message(), or a bus_writer-owned '
            'SECURITY DEFINER function, not a raw INSERT (backlog#124)',
            current_user
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_agent_messages_bus_writer_gate
    BEFORE INSERT ON agent_messages
    FOR EACH ROW EXECUTE FUNCTION agent_messages_bus_writer_gate();

-- Inline existence+enabled check in place of a header assert (see header
-- comment for why): aborts the whole transaction, same as a failed assert
-- would, under both --dry-run and a real apply.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
         WHERE tgrelid = 'agent_messages'::regclass
           AND tgname = 'trg_agent_messages_bus_writer_gate'
           AND tgenabled = 'O'
    ) THEN
        RAISE EXCEPTION 'trg_agent_messages_bus_writer_gate missing or not enabled after apply';
    END IF;
END $$;

-- Belt-and-braces second layer: service_role is NOT the table owner, so this
-- REVOKE is a real, independently-effective ACL change (unlike the postgres
-- half the guard trigger above replaces). Kept even though the trigger alone
-- already blocks service_role too -- redundant defense is cheap here and
-- survives even if the trigger is ever accidentally DISABLEd.
REVOKE INSERT ON agent_messages FROM service_role;

-- WET self-test (console's explicit ask, bus #85468/#85469 -- not the
-- existence-check above, an actual behavioral probe run inside this same
-- transaction, under both --dry-run and a real apply): a raw INSERT as
-- postgres must be rejected by the guard trigger, and post_agent_message
-- (bus_writer-owned SECURITY DEFINER, migration 123, untouched by 124) must
-- still succeed. The PL/pgSQL BEGIN/EXCEPTION block below is an implicit
-- savepoint, so a caught failure here does not abort the outer transaction
-- -- only an uncaught RAISE (a genuine self-test failure) does.
--
-- SET ROLE bus_writer around the post_agent_message call rather than
-- relying on the applying role's own (possibly inherited, possibly not --
-- found this the hard way via this file's own TDD) EXECUTE privilege: the
-- real thing being proven here is that bus_writer's own access path works,
-- not whatever ambient privilege the role running this migration happens
-- to carry. Migration 123's own `GRANT bus_writer TO CURRENT_USER WITH SET
-- TRUE` is exactly what makes this SET ROLE always available to whoever
-- applies a migration.
DO $$
DECLARE
    v_msg_id BIGINT;
BEGIN
    BEGIN
        SET ROLE postgres;
        INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body)
          VALUES ('migration-124-self-test', 'migration-124-self-test', 'update',
                   'self-test: should be rejected', 'self-test');
        RESET ROLE;
        RAISE EXCEPTION
            'MIGRATION 124 SELF-TEST FAILED: a raw INSERT as postgres succeeded -- '
            'trg_agent_messages_bus_writer_gate is not enforcing';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RESET ROLE;
    END;

    SET ROLE bus_writer;
    SELECT id INTO v_msg_id FROM post_agent_message(
        p_from_agent => 'migration-124-self-test',
        p_to_agent => 'migration-124-self-test',
        p_message_type => 'update',
        p_subject => 'self-test: should succeed',
        p_body => 'migration 124 self-test via post_agent_message',
        p_priority => 'P3',
        p_requires_response => false,
        p_thread_id => gen_random_uuid(),
        p_sub_tag => NULL,
        p_is_test => true
    );
    RESET ROLE;

    IF v_msg_id IS NULL THEN
        RAISE EXCEPTION
            'MIGRATION 124 SELF-TEST FAILED: post_agent_message did not return a row '
            'after the guard trigger was added';
    END IF;
END $$;

-- Self-test cleanup: only test 2 ever committed a row (test 1's INSERT was
-- rejected before it could); DELETE is idempotent either way.
DELETE FROM agent_messages WHERE from_agent = 'migration-124-self-test';

COMMIT;
