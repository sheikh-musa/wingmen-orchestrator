"""Tests for migration 124 (backlog#124's final step, console bus #85451): the
REVOKE half split out of migration 123 (Musa op#34845, bus #78393 ruling) --
revokes postgres and service_role's direct INSERT on agent_messages. Gated on
a full writer inventory being complete: python/sh/PostgREST categories are
confirmed clean fleet-wide by agent_messages_writer_gate.py --require-complete
(not re-tested here, that gate has its own test file,
tests/test_agent_messages_writer_gate.py); the 4th category -- the 5 live
postgres-owned DB functions that INSERT into agent_messages directly -- is
this migration's second half and this file's main subject.

Fixture builds a FAITHFUL MINIMAL reproduction of the CAI decision/audit
schema these 5 functions actually touch (strategic_decisions, decision_audits,
decision_audit_state, decision_tier_escalations, audit_board_digest_log,
notification_log, decision_audit_unresolved()) -- not a full replay of
bedrock_substrate_core.sql's entire CAI surface, which has far more than these
functions ever reference. decision_audit_state's real view definition joins
3 more helper functions (decision_audit_required/decision_audit_tier_
candidate/decision_audit_effective_builder) this fixture has no use for;
its own version here is simplified to expose exactly the columns the 5
functions read, computed directly rather than via those helpers -- migration
124 never touches the view's definition, so its derivation logic is not
under test here, only whether bus_writer can SELECT it and get correct data
for the scenarios below.
"""
import pathlib
import re
import sys
import uuid

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "migrations"

_AGENT_MESSAGES_SCHEMA = """
    CREATE TABLE agent_messages (
        id                 BIGSERIAL PRIMARY KEY,
        from_agent         TEXT NOT NULL,
        to_agent           TEXT NOT NULL,
        message_type       TEXT NOT NULL,
        subject            TEXT NOT NULL,
        body               TEXT NOT NULL,
        priority           TEXT NOT NULL DEFAULT 'P2',
        requires_response  BOOLEAN NOT NULL DEFAULT false,
        thread_id          UUID NOT NULL DEFAULT gen_random_uuid(),
        sub_tag            TEXT,
        is_test            BOOLEAN NOT NULL DEFAULT false,
        posted_by_identity TEXT,
        read_at            TIMESTAMPTZ,
        responded_at       TIMESTAMPTZ,
        response_ref       TEXT,
        skipped_at         TIMESTAMPTZ,
        claimed_by         TEXT,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

# Faithful minimal reproduction of the CAI decision/audit objects the 5
# postgres-owned functions in migration 124's second half actually touch.
_WORK_ITEM_MINIMAL = """
    -- Bare tables only (no trigger) -- migration 123's GRANT statements
    -- reference these; none of this file's tests exercise work_item
    -- mirroring itself (that is test_durable_inbox_enforcement_123.py's
    -- subject), so the trigger/mirror function is deliberately omitted.
    CREATE TABLE work_item (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_table text NOT NULL,
        source_id    bigint NOT NULL,
        CONSTRAINT work_item_source_uk UNIQUE (source_table, source_id)
    );
    CREATE TABLE work_item_audit (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        work_item_id bigint NOT NULL REFERENCES work_item (id)
    );
"""

_CAI_SCHEMA = """
    CREATE TABLE strategic_decisions (
        id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        decision_ref                text NOT NULL UNIQUE,
        title                       text NOT NULL,
        audit_tier                  text,
        decided_at                  timestamptz NOT NULL DEFAULT now(),
        challenge_status            text NOT NULL DEFAULT 'unchallenged',
        source                      text NOT NULL DEFAULT 'claude_ai_session',
        bypass_review               boolean NOT NULL DEFAULT false,
        is_test                     boolean NOT NULL DEFAULT false,
        announced_by_msg_id         bigint,
        notified_at                 timestamptz,
        execution_status            text,
        parent_ref                  text,
        parent_msg_id               bigint,
        announce_to_agent           text,
        announce_thread_id          uuid,
        announce_requires_response  boolean NOT NULL DEFAULT false,
        updated_at                  timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE decision_audits (
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        decision_ref  text NOT NULL REFERENCES strategic_decisions (decision_ref),
        auditor_agent text NOT NULL,
        verdict       text,
        lens          text,
        completed_at  timestamptz,
        assigned_at   timestamptz NOT NULL DEFAULT now(),
        sla_hours     integer NOT NULL DEFAULT 24,
        escalated_at  timestamptz,
        resolved_at   timestamptz,
        updated_at    timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE decision_tier_escalations (
        decision_ref  text PRIMARY KEY,
        escalated_at  timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE audit_board_digest_log (
        id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ran_at      timestamptz NOT NULL DEFAULT now(),
        had_content boolean,
        msg_id      bigint,
        summary     text
    );

    CREATE TABLE notification_log (
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source        text,
        decision_ref  text,
        channel       text DEFAULT 'telegram',
        recipient     text,
        message_text  text,
        created_at    timestamptz NOT NULL DEFAULT now()
    );

    -- Real signature (checked live); simplified body -- "unresolved" means no
    -- verdict yet, or a terminal-non-pass verdict nobody has resolved. This
    -- migration does not touch this function at all; it only needs to behave
    -- plausibly for the test rows below.
    CREATE OR REPLACE FUNCTION decision_audit_unresolved(
        p_verdict text, p_completed_at timestamptz, p_resolved_at timestamptz
    ) RETURNS boolean LANGUAGE sql AS $$
        SELECT p_completed_at IS NULL
            OR (p_verdict IN ('rejected', 'could_not_verify', 'nonconforming')
                AND p_resolved_at IS NULL);
    $$;

    -- Simplified stand-in for the real decision_audit_state view: exposes
    -- exactly the columns close_decision_by_audit / escalate_stale_decision_
    -- audits read, computed directly rather than via the 3 helper functions
    -- the real view also joins (not under test here -- see module docstring).
    CREATE VIEW decision_audit_state AS
    SELECT sd.decision_ref,
           sd.title,
           sd.audit_tier,
           sd.challenge_status,
           count(da.*) AS n_assigned,
           count(da.*) FILTER (WHERE da.completed_at IS NULL) AS n_open,
           count(da.*) FILTER (WHERE da.verdict = 'accepted') AS n_accepted,
           count(da.*) FILTER (WHERE da.verdict = 'rejected') AS n_rejected,
           array_remove(array_agg(da.auditor_agent ORDER BY da.assigned_at), NULL) AS auditors,
           count(da.*) FILTER (
               WHERE decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at)
           ) AS n_unresolved,
           (count(da.*) > 0) AS audit_required
      FROM strategic_decisions sd
      LEFT JOIN decision_audits da ON da.decision_ref = sd.decision_ref
     WHERE COALESCE(sd.is_test, false) = false
     GROUP BY sd.decision_ref, sd.title, sd.audit_tier, sd.challenge_status;
"""

# The 5 live postgres-owned functions, PRE-124 (exactly as pulled from the
# real substrate's pg_proc, 2026-10-31), owned by whoever runs this block
# (live_applier, mirroring postgres), EXECUTE granted the same way live has
# it: postgres + service_role for all 5, PLUS anon/authenticated on
# trigger_cai_decision_announce (the hole migration 124 closes).
_PRE_124_FUNCTIONS = r"""
    CREATE OR REPLACE FUNCTION audit_board_digest()
    RETURNS TABLE(had_content boolean, summary text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public', 'pg_temp' AS $$
    DECLARE
        v_body   text := '';
        v_lines  int  := 0;
        v_msg_id bigint;
        r        RECORD;
    BEGIN
        FOR r IN
            SELECT da.decision_ref, da.auditor_agent, da.verdict
              FROM decision_audits da
              JOIN strategic_decisions sd ON sd.decision_ref = da.decision_ref
             WHERE da.verdict IN ('rejected', 'could_not_verify')
               AND da.resolved_at IS NULL
               AND COALESCE(sd.is_test, false) = false
        LOOP
            v_lines := v_lines + 1;
            v_body := v_body || format(E'  UNRESOLVED %s — %s\n', r.verdict, r.decision_ref);
        END LOOP;

        IF v_lines = 0 THEN
            INSERT INTO audit_board_digest_log (had_content, summary)
            VALUES (false, 'nothing actionable');
            RETURN QUERY SELECT false, 'nothing actionable'::text;
            RETURN;
        END IF;

        INSERT INTO agent_messages
            (from_agent, to_agent, message_type, subject, body, requires_response, priority)
        VALUES ('substrate', 'cai', 'update',
                'AUDIT BOARD: ' || v_lines || ' item(s) need somebody', v_body, false, 'P2')
        RETURNING id INTO v_msg_id;

        INSERT INTO audit_board_digest_log (had_content, msg_id, summary)
        VALUES (true, v_msg_id, v_lines || ' actionable item(s)');

        RETURN QUERY SELECT true, (v_lines || ' actionable item(s)')::text;
    END;
    $$;

    CREATE OR REPLACE FUNCTION close_decision_by_audit(p_decision_ref text, p_closed_by text)
    RETURNS text LANGUAGE plpgsql AS $$
    DECLARE
        v RECORD;
    BEGIN
        SELECT * INTO v FROM decision_audit_state WHERE decision_ref = p_decision_ref;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'no such decision (or it is a test row): %', p_decision_ref;
        END IF;
        IF v.n_accepted = 0 THEN
            RAISE EXCEPTION 'cannot close %: no completed audit with verdict=accepted', p_decision_ref;
        END IF;
        IF v.n_open > 0 THEN
            RAISE EXCEPTION 'cannot close %: % audit(s) still in flight', p_decision_ref, v.n_open;
        END IF;
        IF v.n_rejected > 0 THEN
            RAISE EXCEPTION 'cannot close %: an auditor REJECTED it', p_decision_ref;
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
            p_decision_ref || ': CLOSED accepted_by_audit', 'closed by assigned audit', false);
        RETURN 'closed';
    END;
    $$;

    CREATE OR REPLACE FUNCTION escalate_full_tier_without_auditor(p_grace_hours numeric DEFAULT 2)
    RETURNS TABLE(decision_ref text, auditor_agent text, action text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public', 'pg_temp' AS $$
    #variable_conflict use_column
    DECLARE
        rec RECORD;
    BEGIN
        FOR rec IN
            SELECT sd.decision_ref, sd.title, sd.decided_at
              FROM strategic_decisions sd
             WHERE sd.audit_tier = 'FULL'
               AND COALESCE(sd.is_test, false) = false
               AND sd.decided_at < now() - (p_grace_hours * interval '1 hour')
               AND NOT EXISTS (SELECT 1 FROM decision_audits da WHERE da.decision_ref = sd.decision_ref)
               AND NOT EXISTS (SELECT 1 FROM decision_tier_escalations e WHERE e.decision_ref = sd.decision_ref)
        LOOP
            INSERT INTO agent_messages
                (from_agent, to_agent, message_type, subject, body, requires_response, priority)
            VALUES ('substrate', 'cai', 'blocker',
                'FULL TIER, NO AUDITOR: ' || rec.decision_ref, 'nobody assigned', true, 'P2');
            INSERT INTO decision_tier_escalations (decision_ref) VALUES (rec.decision_ref)
                ON CONFLICT (decision_ref) DO NOTHING;
            RETURN QUERY SELECT rec.decision_ref, '(nobody assigned)'::TEXT, 'escalated_full_no_auditor'::TEXT;
        END LOOP;
    END;
    $$;

    CREATE OR REPLACE FUNCTION escalate_stale_decision_audits()
    RETURNS TABLE(decision_ref text, auditor_agent text, action text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public', 'pg_temp' AS $$
    #variable_conflict use_column
    DECLARE
        rec RECORD;
    BEGIN
        FOR rec IN
            SELECT da.id, da.decision_ref, da.auditor_agent, da.sla_hours, da.verdict, da.completed_at
              FROM decision_audits da
              JOIN strategic_decisions sd ON sd.decision_ref = da.decision_ref
             WHERE decision_audit_unresolved(da.verdict, da.completed_at, da.resolved_at)
               AND da.escalated_at IS NULL
               AND COALESCE(sd.is_test, false) = false
               AND COALESCE(da.completed_at, da.assigned_at) < now() - make_interval(hours => da.sla_hours)
        LOOP
            INSERT INTO agent_messages
                (from_agent, to_agent, message_type, subject, body, requires_response, priority)
            VALUES ('substrate', 'cai', 'blocker',
                'STALE AUDIT: ' || rec.decision_ref, 'stale', true, 'P2');
            UPDATE decision_audits SET escalated_at = now(), updated_at = now() WHERE id = rec.id;
            RETURN QUERY SELECT rec.decision_ref, rec.auditor_agent, 'escalated_unresolved'::TEXT;
        END LOOP;
    END;
    $$;

    -- Faithful to the real live body (2026-10-31 pg_get_functiondef pull,
    -- inline comments stripped) -- this exact fidelity matters: an earlier,
    -- simplified version of this fixture dropped the parent_msg_id lookup
    -- entirely, which let a missing `GRANT SELECT ON agent_messages TO
    -- bus_writer` ship undetected (cc-quality review, PR#716, bus #85462,
    -- BLOCKING 2) -- the SECURITY INVOKER->DEFINER conversion needs its own
    -- SELECT grant for this exact path, and nothing exercised it.
    CREATE OR REPLACE FUNCTION trigger_cai_decision_announce()
    RETURNS trigger LANGUAGE plpgsql SET search_path TO 'public', 'pg_temp' AS $$
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
        VALUES ('bridge_tier3_misroute', NEW.decision_ref, 'agent_messages', 'cc-ihsanos', 'fallback fired');
      END IF;

      RETURN NEW;
    END;
    $$;

    CREATE TRIGGER cai_decision_announce_insert
        BEFORE INSERT ON strategic_decisions
        FOR EACH ROW EXECUTE FUNCTION trigger_cai_decision_announce();

    GRANT EXECUTE ON FUNCTION audit_board_digest() TO postgres, service_role;
    GRANT EXECUTE ON FUNCTION close_decision_by_audit(text, text) TO postgres, service_role;
    GRANT EXECUTE ON FUNCTION escalate_full_tier_without_auditor(numeric) TO postgres, service_role;
    GRANT EXECUTE ON FUNCTION escalate_stale_decision_audits() TO postgres, service_role;
    GRANT EXECUTE ON FUNCTION trigger_cai_decision_announce() TO postgres, service_role, anon, authenticated;
"""


def _setup_base_123_and_cai_schema(cur):
    """123's own base schema (agent_messages, bus_writer, the 3 RPCs) plus the
    CAI decision/audit schema and the 5 PRE-124 functions, owned by
    live_applier (mirroring postgres), with the real live starting grants on
    strategic_decisions' RLS: console-readonly SELECT + service_role ALL."""
    migration_117 = (MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text()
    migration_123 = (MIGRATIONS_DIR / "123_durable_inbox_enforce_bus_writer.sql").read_text()
    migration_122 = (MIGRATIONS_DIR / "122_post_agent_message_optional_params.sql").read_text()

    cur.execute("DROP SCHEMA public CASCADE")
    cur.execute("CREATE SCHEMA public")
    cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    cur.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT EXECUTE ON FUNCTIONS TO anon, authenticated, service_role"
    )

    cur.execute(_AGENT_MESSAGES_SCHEMA)
    cur.execute("ALTER TABLE agent_messages ENABLE ROW LEVEL SECURITY")
    cur.execute(
        "CREATE POLICY agent_messages_service_only ON agent_messages "
        "FOR ALL TO service_role USING (true) WITH CHECK (true)"
    )
    cur.execute(migration_117)
    cur.execute(migration_122)
    cur.execute(_WORK_ITEM_MINIMAL)
    cur.execute("GRANT SELECT, INSERT, UPDATE ON agent_messages TO service_role")
    cur.execute("GRANT USAGE ON SEQUENCE agent_messages_id_seq TO service_role")
    cur.execute("GRANT SELECT, INSERT, UPDATE ON work_item TO service_role")
    cur.execute("GRANT INSERT ON work_item_audit TO service_role")
    cur.execute(migration_123)

    cur.execute(_CAI_SCHEMA)
    cur.execute("ALTER TABLE strategic_decisions ENABLE ROW LEVEL SECURITY")
    cur.execute(
        "CREATE POLICY strategic_decisions_console_ro ON strategic_decisions "
        "FOR SELECT USING (true)"
    )
    cur.execute(
        "CREATE POLICY strategic_decisions_service_only ON strategic_decisions "
        "FOR ALL TO service_role USING (true) WITH CHECK (true)"
    )
    # service_role's own real, pre-existing standing privilege on the whole
    # CAI schema (from whichever earlier migration created these tables,
    # not this file's subject) -- RLS policies above are necessary but not
    # sufficient without the matching table-level GRANT, same lesson as
    # everywhere else in this fixture.
    cur.execute("GRANT SELECT, INSERT, UPDATE ON strategic_decisions TO service_role")
    cur.execute("GRANT SELECT, INSERT, UPDATE ON decision_audits TO service_role")
    cur.execute("GRANT SELECT, INSERT ON decision_tier_escalations TO service_role")
    cur.execute("GRANT SELECT, INSERT ON audit_board_digest_log TO service_role")
    cur.execute("GRANT SELECT, INSERT ON notification_log TO service_role")
    cur.execute("GRANT SELECT ON decision_audit_state TO service_role")
    # Real live RLS state (checked against the substrate, 2026-10-31): ALL 6
    # of these tables have RLS enabled, not just agent_messages/
    # strategic_decisions -- a fixture that skips this for the other 4 would
    # make every one of this file's "still works after 124" tests pass
    # trivially regardless of whether bus_writer actually has a matching
    # policy, exactly the false-negative risk migration 123's header already
    # paid for once on agent_messages itself.
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname='console_readonly'")
    if cur.fetchone() is None:
        cur.execute("CREATE ROLE console_readonly")
    for tbl in ("decision_audits", "decision_tier_escalations", "audit_board_digest_log"):
        cur.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
    cur.execute("ALTER TABLE notification_log ENABLE ROW LEVEL SECURITY")
    cur.execute(
        "CREATE POLICY decision_audits_console_ro ON decision_audits "
        "FOR SELECT TO console_readonly USING (true)"
    )
    cur.execute(
        "CREATE POLICY decision_audits_service_only ON decision_audits "
        "FOR ALL TO service_role USING (true) WITH CHECK (true)"
    )
    cur.execute(
        "CREATE POLICY decision_tier_escalations_service ON decision_tier_escalations "
        "FOR ALL TO service_role USING (true) WITH CHECK (true)"
    )
    cur.execute(
        "CREATE POLICY audit_board_digest_log_console_ro ON audit_board_digest_log "
        "FOR SELECT TO console_readonly USING (true)"
    )
    cur.execute(
        "CREATE POLICY audit_board_digest_log_service ON audit_board_digest_log "
        "FOR ALL TO service_role USING (true) WITH CHECK (true)"
    )
    # Real live policy uses auth.role() = 'service_role' (a Supabase/PostgREST
    # JWT-claim function, not the DB role) with no TO clause -- a direct SQL
    # session (no JWT) never satisfies it, so this table is, in effect,
    # already unwritable by anything except PostgREST-mediated service_role
    # calls. Reproduced with an equivalent always-false stand-in (no auth
    # schema in this ephemeral cluster) -- the real behaviour this fixture
    # needs is "no role gets in via the EXISTING policy alone", which this
    # preserves; bus_writer's own new policy (migration 124) is what
    # actually has to do the work here, same as live.
    cur.execute("CREATE SCHEMA IF NOT EXISTS auth")
    cur.execute("CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql AS $$ SELECT NULL::text $$")
    cur.execute(
        "CREATE POLICY \"service_role full access\" ON notification_log "
        "USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role')"
    )

    cur.execute(_PRE_124_FUNCTIONS)
    # postgres pre-124's real starting privilege: direct table-level INSERT on
    # agent_messages, same simulated starting point as additive_db's own
    # service_role grant above.
    cur.execute("GRANT INSERT ON agent_messages TO postgres")


@pytest.fixture
def pre_124_db(pg_dsn):
    """123 + the CAI schema, all 5 functions still postgres-owned (pre-124
    end state) -- same live_applier-mirrors-postgres shape as
    test_durable_inbox_enforcement_123.py's additive_db fixture."""
    with psycopg.connect(pg_dsn, autocommit=True) as super_conn, super_conn.cursor() as super_cur:
        super_cur.execute("SELECT 1 FROM pg_roles WHERE rolname='live_applier'")
        if super_cur.fetchone() is None:
            super_cur.execute("CREATE ROLE live_applier LOGIN NOSUPERUSER CREATEROLE BYPASSRLS")
        super_cur.execute("ALTER SCHEMA public OWNER TO live_applier")
        super_cur.execute("GRANT CREATE ON DATABASE postgres TO live_applier")
        for role in ("anon", "authenticated", "service_role", "postgres"):
            super_cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if super_cur.fetchone() is None:
                super_cur.execute(f"CREATE ROLE {role}")
        super_cur.execute(
            "GRANT anon, authenticated, service_role, postgres TO live_applier "
            "WITH INHERIT FALSE, SET TRUE"
        )

    applier_dsn = pg_dsn.replace("user=postgres", "user=live_applier")
    with psycopg.connect(applier_dsn, autocommit=True) as conn, conn.cursor() as cur:
        _setup_base_123_and_cai_schema(cur)

    conn = psycopg.connect(applier_dsn)
    yield conn
    conn.close()


@pytest.fixture
def post_124_db(pre_124_db):
    """pre_124_db, then migration 124 applied on top -- postgres and
    service_role lose direct INSERT on agent_messages, and the 5 functions
    move onto bus_writer."""
    migration_124 = (MIGRATIONS_DIR / "124_durable_inbox_revoke_direct_insert.sql").read_text()
    with pre_124_db.cursor() as cur:
        cur.execute(am.strip_txn_control(am.strip_sql_comments(migration_124)))
    pre_124_db.commit()
    return pre_124_db


def test_raw_insert_as_postgres_fails_after_124(post_124_db):
    """postgres OWNS agent_messages (pg_class.relowner) -- a REVOKE against it
    would be a no-op (cc-quality's PR#716 review, bus #85462, BLOCKING 1; a
    table owner's base privileges are implicit and not grant/revoke-
    removable). That is exactly why 124 uses a BEFORE INSERT guard trigger
    instead of a REVOKE for postgres: triggers fire for every inserting role
    regardless of ownership or ACL privilege -- including a role that also
    happens to be this ephemeral harness's actual initdb-bootstrap
    superuser. Unlike an ACL/has_table_privilege check (which a superuser
    always passes, the limitation the previous version of this test
    disclosed), trigger firing is NOT bypassed by superuser status -- only
    RLS is -- so this IS a meaningful, real test of the postgres path, in
    this harness as much as on the real substrate."""
    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE postgres")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as excinfo:
            cur.execute(
                "INSERT INTO agent_messages "
                "(from_agent, to_agent, message_type, subject, body) "
                "VALUES ('x','y','update','s','b')"
            )
        assert "direct INSERT not allowed" in str(excinfo.value)
    post_124_db.rollback()


def test_raw_insert_as_service_role_fails_after_124(post_124_db):
    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO agent_messages "
                "(from_agent, to_agent, message_type, subject, body) "
                "VALUES ('x','y','update','s','b')"
            )
    post_124_db.rollback()


def test_post_agent_message_rpc_path_unaffected_by_124(post_124_db):
    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("x", "y", "update", "via rpc", "b", "P2", False, str(uuid.uuid4()), None),
        )
        row = cur.fetchone()
    post_124_db.commit()
    assert row[0] is not None


def test_mark_delivered_and_ack_still_work_after_124(post_124_db):
    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("x", "y", "update", "s", "b", "P2", False, str(uuid.uuid4()), None),
        )
        (row_id, _thread) = cur.fetchone()
        cur.execute("SELECT mark_delivered(%s, %s)", (row_id, 60))
        cur.execute("SELECT ack_message(%s)", (row_id,))
    post_124_db.commit()
    with post_124_db.cursor() as cur:
        cur.execute("SELECT delivered_at, acked_at FROM agent_messages WHERE id=%s", (row_id,))
        delivered_at, acked_at = cur.fetchone()
    assert delivered_at is not None
    assert acked_at is not None


def test_audit_board_digest_still_works_after_124(post_124_db):
    with post_124_db.cursor() as cur:
        cur.execute(
            "INSERT INTO strategic_decisions (decision_ref, title, challenge_status, is_test) "
            "VALUES ('D-1', 'test decision', 'accepted', false)"
        )
        cur.execute(
            "INSERT INTO decision_audits (decision_ref, auditor_agent, verdict, completed_at) "
            "VALUES ('D-1', 'cc-quality', 'rejected', now())"
        )
    post_124_db.commit()

    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE postgres")  # the real cron caller (username='postgres')
        cur.execute("SELECT * FROM audit_board_digest()")
        (had_content, summary) = cur.fetchone()
    post_124_db.commit()
    assert had_content is True

    with post_124_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_board_digest_log")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM agent_messages WHERE subject LIKE 'AUDIT BOARD%'")
        assert cur.fetchone()[0] == 1


def test_escalate_full_tier_without_auditor_still_works_after_124(post_124_db):
    with post_124_db.cursor() as cur:
        cur.execute(
            "INSERT INTO strategic_decisions (decision_ref, title, audit_tier, decided_at, "
            " challenge_status, is_test) "
            "VALUES ('D-2', 'full tier, no auditor', 'FULL', now() - interval '10 hours', "
            " 'unchallenged', false)"
        )
    post_124_db.commit()

    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE postgres")
        cur.execute("SELECT * FROM escalate_full_tier_without_auditor(2)")
        rows = cur.fetchall()
    post_124_db.commit()
    assert rows == [("D-2", "(nobody assigned)", "escalated_full_no_auditor")]

    with post_124_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM decision_tier_escalations WHERE decision_ref='D-2'")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM agent_messages WHERE subject LIKE 'FULL TIER%'")
        assert cur.fetchone()[0] == 1


def test_escalate_stale_decision_audits_still_works_after_124(post_124_db):
    with post_124_db.cursor() as cur:
        cur.execute(
            "INSERT INTO strategic_decisions (decision_ref, title, challenge_status, is_test) "
            "VALUES ('D-3', 'stale audit', 'challenge_window', false)"
        )
        cur.execute(
            "INSERT INTO decision_audits (decision_ref, auditor_agent, verdict, sla_hours, "
            " assigned_at) VALUES ('D-3', 'cc-quality', NULL, 1, now() - interval '10 hours')"
        )
    post_124_db.commit()

    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE postgres")
        cur.execute("SELECT * FROM escalate_stale_decision_audits()")
        rows = cur.fetchall()
    post_124_db.commit()
    assert rows == [("D-3", "cc-quality", "escalated_unresolved")]

    with post_124_db.cursor() as cur:
        cur.execute("SELECT escalated_at FROM decision_audits WHERE decision_ref='D-3'")
        assert cur.fetchone()[0] is not None
        cur.execute("SELECT count(*) FROM agent_messages WHERE subject LIKE 'STALE AUDIT%'")
        assert cur.fetchone()[0] == 1


def test_close_decision_by_audit_still_works_after_124(post_124_db):
    with post_124_db.cursor() as cur:
        cur.execute(
            "INSERT INTO strategic_decisions (decision_ref, title, challenge_status, is_test) "
            "VALUES ('D-4', 'closeable', 'challenge_window', false)"
        )
        cur.execute(
            "INSERT INTO decision_audits (decision_ref, auditor_agent, verdict, completed_at) "
            "VALUES ('D-4', 'cc-quality', 'accepted', now())"
        )
    post_124_db.commit()

    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE service_role")  # or postgres -- both are the real legitimate callers
        cur.execute("SELECT close_decision_by_audit(%s, %s)", ("D-4", "orch-console"))
        result = cur.fetchone()[0]
    post_124_db.commit()
    assert result == "closed"

    with post_124_db.cursor() as cur:
        cur.execute("SELECT challenge_status FROM strategic_decisions WHERE decision_ref='D-4'")
        assert cur.fetchone()[0] == "accepted_by_audit"
        cur.execute("SELECT count(*) FROM agent_messages WHERE subject LIKE '%CLOSED accepted_by_audit%'")
        assert cur.fetchone()[0] == 1


def test_trigger_cai_decision_announce_still_fires_after_124(post_124_db):
    """The real caller is CAI, an external system writing strategic_decisions
    via a service_role key over PostgREST (source defaults to
    'claude_ai_session') -- simulated here as service_role, the only
    non-postgres role strategic_decisions' RLS lets write at all."""
    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "INSERT INTO strategic_decisions (decision_ref, title, challenge_status, source) "
            "VALUES ('D-5', 'cai filed this', 'challenge_window', 'claude_ai_session') "
            "RETURNING announced_by_msg_id"
        )
        (announced_by_msg_id,) = cur.fetchone()
    post_124_db.commit()
    assert announced_by_msg_id is not None

    with post_124_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages WHERE id=%s", (announced_by_msg_id,))
        assert cur.fetchone()[0] == 1


def test_trigger_cai_decision_announce_parent_msg_id_lookup_after_124(post_124_db):
    """cc-quality review finding (PR#716, bus #85462, BLOCKING 2): a threaded
    announce (parent_msg_id IS NOT NULL) makes the trigger SELECT from
    agent_messages -- as bus_writer (DEFINER, post-124), not service_role
    (the caller, pre-124's INVOKER mode). bus_writer has INSERT but, before
    the fix, no SELECT -- this would raise 'permission denied for table
    agent_messages' and roll back the whole strategic_decisions write. Also
    confirms the COALESCE-to-parent routing (to_agent/thread_id inherited
    from the parent message when announce_to_agent/announce_thread_id are
    both NULL)."""
    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE bus_writer")
        cur.execute(
            "INSERT INTO agent_messages (thread_id, from_agent, to_agent, message_type, subject, body) "
            "VALUES (gen_random_uuid(), 'orch-console', 'cai', 'update', 'parent', 'parent body') "
            "RETURNING id, thread_id, from_agent"
        )
        parent_id, parent_thread_id, parent_from_agent = cur.fetchone()
        cur.execute("RESET ROLE")
    post_124_db.commit()

    with post_124_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "INSERT INTO strategic_decisions "
            "(decision_ref, title, challenge_status, source, parent_msg_id) "
            "VALUES ('D-6', 'threaded reply', 'challenge_window', 'claude_ai_session', %s) "
            "RETURNING announced_by_msg_id",
            (parent_id,),
        )
        (announced_by_msg_id,) = cur.fetchone()
        cur.execute("RESET ROLE")
    post_124_db.commit()
    assert announced_by_msg_id is not None

    with post_124_db.cursor() as cur:
        cur.execute(
            "SELECT to_agent, thread_id FROM agent_messages WHERE id=%s", (announced_by_msg_id,)
        )
        to_agent, thread_id = cur.fetchone()
        assert to_agent == parent_from_agent
        assert thread_id == parent_thread_id


def test_anon_and_authenticated_cannot_execute_trigger_cai_decision_announce_after_124(
    post_124_db,
):
    with post_124_db.cursor() as cur:
        for role in ("anon", "authenticated"):
            cur.execute(
                "SELECT has_function_privilege(%s, 'trigger_cai_decision_announce()', 'EXECUTE')",
                (role,),
            )
            assert cur.fetchone()[0] is False, f"{role} can still EXECUTE trigger_cai_decision_announce"
        for role in ("postgres", "service_role"):
            cur.execute(
                "SELECT has_function_privilege(%s, 'trigger_cai_decision_announce()', 'EXECUTE')",
                (role,),
            )
            assert cur.fetchone()[0] is True, f"{role} lost its legitimate EXECUTE"


def test_anon_and_authenticated_still_cannot_write_strategic_decisions_after_124(post_124_db):
    """Confirms the EXECUTE revoke above is a true no-op behaviourally, not a
    hidden behaviour change -- anon/authenticated never had table-level
    INSERT/UPDATE on strategic_decisions (RLS), so they could never have
    fired the trigger regardless."""
    with post_124_db.cursor() as cur:
        for role in ("anon", "authenticated"):
            for priv in ("INSERT", "UPDATE"):
                cur.execute("SELECT has_table_privilege(%s, 'strategic_decisions', %s)", (role, priv))
                assert cur.fetchone()[0] is False, f"{role} unexpectedly has {priv} on strategic_decisions"


def test_124_revoke_and_grant_and_policy_statements_are_idempotent_on_rerun(post_124_db):
    """Everything in this migration EXCEPT the 2 CREATE OR REPLACE statements
    for close_decision_by_audit and trigger_cai_decision_announce is
    genuinely idempotent and safe to re-run (same contract as
    test_123_is_idempotent_on_rerun) -- including the 3 bare OWNER-TO-only
    ALTERs, the GRANT/CREATE POLICY additions, the guard trigger function +
    CREATE OR REPLACE TRIGGER, its own existence-check DO block, and the
    service_role REVOKE. Those 2 specific statements are NOT re-runnable --
    see the migration file's own disclosed-limitation comment above each --
    so this test cuts exactly those two out (by regex, not a blind prefix
    slice, so everything AFTER them -- the guard trigger, grants, policies,
    REVOKE -- is actually exercised here too, not silently skipped)."""
    migration_124 = (MIGRATIONS_DIR / "124_durable_inbox_revoke_direct_insert.sql").read_text()
    body = am.strip_txn_control(am.strip_sql_comments(migration_124))
    for fn in ("close_decision_by_audit", "trigger_cai_decision_announce"):
        body, n = re.subn(
            rf"CREATE OR REPLACE FUNCTION public\.{fn}\(.*?\n\$\$;\n",
            "",
            body,
            count=1,
            flags=re.DOTALL,
        )
        assert n == 1, f"expected exactly one CREATE OR REPLACE FUNCTION public.{fn}(...) block"
    with post_124_db.cursor() as cur:
        cur.execute(body)
    post_124_db.commit()


def test_124_sets_lock_timeout_as_its_first_statement():
    raw = (MIGRATIONS_DIR / "124_durable_inbox_revoke_direct_insert.sql").read_text()
    body = am.strip_txn_control(am.strip_sql_comments(raw)).strip()
    assert body.startswith("SET LOCAL lock_timeout = '5s';"), (
        "migration 124's first executed statement must be `SET LOCAL lock_timeout = "
        f"'5s';`, got: {body[:80]!r}"
    )


def test_124_applies_cleanly_via_apply_migration_dry_run(pg_dsn):
    """Runs the REAL file through the REAL applier (SQL body + every
    `-- assert:` line) against an ephemeral cluster -- not hand-executed
    statements -- confirming the body itself runs clean (no ordering/
    permission error of the applier's own making, and the comment-grep/
    sequence-mutation dry-run refusals this file's text might otherwise
    trip do not misfire) and every assertion is satisfied.

    No postgres-superuser-harness caveat needed here (unlike an earlier
    version of this test, back when 124 used a REVOKE for postgres): the
    guard trigger design means there is no `no_table_privilege postgres
    ...` assert to begin with, and triggers fire regardless of superuser
    status anyway (only RLS is bypassed by it) -- see
    test_raw_insert_as_postgres_fails_after_124."""
    with psycopg.connect(pg_dsn, autocommit=True) as super_conn, super_conn.cursor() as super_cur:
        super_cur.execute("SELECT 1 FROM pg_roles WHERE rolname='live_applier'")
        if super_cur.fetchone() is None:
            super_cur.execute("CREATE ROLE live_applier LOGIN NOSUPERUSER CREATEROLE BYPASSRLS")
        super_cur.execute("ALTER SCHEMA public OWNER TO live_applier")
        super_cur.execute("GRANT CREATE ON DATABASE postgres TO live_applier")
        for role in ("anon", "authenticated", "service_role", "postgres"):
            super_cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if super_cur.fetchone() is None:
                super_cur.execute(f"CREATE ROLE {role}")
        super_cur.execute(
            "GRANT anon, authenticated, service_role, postgres TO live_applier "
            "WITH INHERIT FALSE, SET TRUE"
        )

    applier_dsn = pg_dsn.replace("user=postgres", "user=live_applier")
    with psycopg.connect(applier_dsn, autocommit=True) as conn, conn.cursor() as cur:
        _setup_base_123_and_cai_schema(cur)
        cur.execute(
            """CREATE TABLE migration_ledger (
                 repo text NOT NULL,
                 migration_name text NOT NULL,
                 silo_ref text NOT NULL,
                 sha256 text NOT NULL,
                 applied_at timestamptz NOT NULL DEFAULT now(),
                 applied_by text,
                 note text,
                 PRIMARY KEY (repo, migration_name, silo_ref)
               )"""
        )

    silo_dsn = f"{applier_dsn} application_name=tscuymavysscrvoberrr"
    result = am.apply_migration(
        silo_dsn,
        MIGRATIONS_DIR / "124_durable_inbox_revoke_direct_insert.sql",
        silo="tscuymavysscrvoberrr",
        dry_run=True,
    )
    assert result["status"] == "dry_run_ok", result
