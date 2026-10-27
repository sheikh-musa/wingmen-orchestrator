"""Tests for migration 123 (backlog#124, durable-inbox final step, split per orch-console
ruling bus #74885; renumbered three times -- 118/119 -> 119/120 after a PR#490 CI finding,
then -> 120/121 after a second collision (orch-console bus #76518: base branch had
separately merged its own migrations/118_operator_asks_needs_musa.sql), then -> 123/124
after a third collision (orch-console bus #78234: base branch had independently taken
119/120/121 for 3 unrelated migrations) -- see
migrations/122_post_agent_message_optional_params.sql's header): a dedicated bus_writer
role owns post_agent_message/mark_delivered/ack_message and holds the INSERT-capable
grants. Purely additive -- creates bus_writer and moves the RPCs onto it, but does NOT
revoke service_role's (or postgres's) direct INSERT.

PR SPLIT (Musa op#34845, bus #78393 ruling): migration 124 (the revoke -- the breaking
half) does NOT ship in this PR; it ships later as its own PR-B once a full writer
inventory across 4 categories is complete (python psycopg, .sh/psql heredocs,
PostgREST/service-key REST calls, and 6 postgres-owned DB functions that INSERT into
this table directly). This file dropped its 124-dependent tests and `enforced_db`
fixture accordingly -- see migrations/123_durable_inbox_enforce_bus_writer.sql's own
header for the full ruling. `additive_db` (123 alone) is therefore this file's only
fixture now, and it is also the real substrate's actual end-state for as long as PR-B
is pending, not an intermediate stage.

Real risk this file's last test exists to catch (my own audit finding, bus #74860):
agent_messages' AFTER-trigger work_item_mirror_bus_trg (and work_item_mirror() itself)
are SECURITY INVOKER, not DEFINER. Once the posting RPCs are owned by bus_writer, that
trigger fires AS bus_writer -- needing SELECT/INSERT/UPDATE on work_item and INSERT on
work_item_audit, which service_role has today only via Supabase's blanket default-ACL
(confirmed from migrations 039/049's own headers) and which bus_writer gets NONE of
automatically. Without an explicit grant this degrades silently (the trigger's own
try/catch, orch-console bus #73195, swallows the error) -- invisible to any suite that
never touches work_item.

Row level security (Musa op#34845, bus #78352 NO-GO, real finding): `_setup_base_and_123`
now permanently enables RLS on agent_messages and seeds the same service_role-only
policy the live substrate already has, console's own exact repro. This is a permanent
fixture change (console's ask #2), not a temporary repro to be reverted -- the live
table has had RLS enabled the whole time; no local/CI Postgres cluster did, which is
why bus_writer's missing policy (and its missing from_agent column grant, see
test_work_item_mirror_resolves_from_agent_when_responded_at_preset_with_123_alone)
never surfaced until Musa's own repro against the real substrate.
"""
import pathlib
import uuid

import psycopg
import pytest

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"

_AGENT_MESSAGES_SCHEMA = """
    CREATE TABLE agent_messages (
        id                 BIGSERIAL PRIMARY KEY,
        from_agent         TEXT NOT NULL,
        to_agent           TEXT NOT NULL,
        message_type       TEXT NOT NULL,
        subject            TEXT NOT NULL,
        body               TEXT NOT NULL,
        priority           TEXT NOT NULL,
        requires_response  BOOLEAN NOT NULL DEFAULT false,
        thread_id          UUID NOT NULL,
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

# Faithful minimal reproduction of migration 114's work_item SSOT -- the exact objects
# work_item_mirror_bus_trg touches, not a full replay of 114 (which also wires
# operator_asks/held_commitments triggers this fixture has no use for).
_WORK_ITEM_SCHEMA = """
    CREATE TABLE work_item (
        id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        kind            text        NOT NULL,
        state           text        NOT NULL DEFAULT 'captured',
        owner_agent     text,
        title           text        NOT NULL,
        source_table    text        NOT NULL,
        source_id       bigint      NOT NULL,
        source_ref      text,
        due_at          timestamptz,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now(),
        done_at         timestamptz,
        done_by         text,
        dropped_at      timestamptz,
        dropped_by      text,
        drop_reason     text,
        CONSTRAINT work_item_source_uk UNIQUE (source_table, source_id)
    );

    CREATE TABLE work_item_audit (
        id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        work_item_id    bigint      NOT NULL REFERENCES work_item (id),
        event           text        NOT NULL,
        actor           text        NOT NULL,
        at              timestamptz NOT NULL DEFAULT now(),
        note            text
    );

    DO $$ BEGIN
        CREATE TYPE work_item_derived AS (
            state text, owner_agent text, title text, source_ref text, due_at timestamptz,
            done_at timestamptz, done_by text, dropped_at timestamptz, dropped_by text,
            drop_reason text
        );
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;

    CREATE OR REPLACE FUNCTION work_item_mirror(
        p_kind text, p_source_table text, p_source_id bigint, d work_item_derived, p_actor text
    ) RETURNS void LANGUAGE plpgsql AS $wim$
    DECLARE
        prior_state text;
        v_id        bigint;
    BEGIN
        SELECT state INTO prior_state FROM work_item
         WHERE source_table = p_source_table AND source_id = p_source_id;

        INSERT INTO work_item (kind, state, owner_agent, title, source_table, source_id,
                                source_ref, due_at, done_at, done_by, dropped_at, dropped_by,
                                drop_reason, updated_at)
        VALUES (p_kind, d.state, d.owner_agent, d.title, p_source_table, p_source_id,
                d.source_ref, d.due_at, d.done_at, d.done_by, d.dropped_at, d.dropped_by,
                d.drop_reason, now())
        ON CONFLICT (source_table, source_id) DO UPDATE SET
            state = EXCLUDED.state, owner_agent = EXCLUDED.owner_agent, title = EXCLUDED.title,
            source_ref = EXCLUDED.source_ref, due_at = EXCLUDED.due_at, done_at = EXCLUDED.done_at,
            done_by = EXCLUDED.done_by, dropped_at = EXCLUDED.dropped_at,
            dropped_by = EXCLUDED.dropped_by, drop_reason = EXCLUDED.drop_reason,
            updated_at = now()
        RETURNING id INTO v_id;

        IF prior_state IS NULL OR prior_state IS DISTINCT FROM d.state THEN
            INSERT INTO work_item_audit (work_item_id, event, actor, note)
            VALUES (v_id, CASE WHEN prior_state IS NULL THEN 'mirrored' ELSE 'state_changed' END,
                    p_actor, COALESCE(prior_state, '(new)') || ' -> ' || d.state);
        END IF;
    END;
    $wim$;

    CREATE OR REPLACE FUNCTION work_item_derive_bus(
        p_id bigint, p_subject text, p_responded_at timestamptz, p_skipped_at timestamptz,
        p_claimed_by text, p_response_ref text
    ) RETURNS work_item_derived LANGUAGE plpgsql AS $wdb$
    DECLARE
        d work_item_derived;
        v_replying_agent text;
    BEGIN
        d.title := left(p_subject, 200);
        d.source_ref := 'agent_messages id=' || p_id;
        IF p_responded_at IS NOT NULL THEN
            d.state := 'done';
            d.done_at := p_responded_at;
            v_replying_agent := NULL;
            IF p_response_ref ~ '^[0-9]+$' THEN
                SELECT from_agent INTO v_replying_agent FROM agent_messages
                 WHERE id = p_response_ref::bigint;
            END IF;
            d.done_by := COALESCE(v_replying_agent, 'source:agent_messages');
        ELSIF p_skipped_at IS NOT NULL THEN
            d.state := 'dropped';
            d.dropped_at := p_skipped_at;
            d.dropped_by := 'source:agent_messages';
            d.drop_reason := 'skipped (priority_sla_watchdog)';
        ELSIF p_claimed_by IS NOT NULL THEN
            d.state := 'owned';
            d.owner_agent := p_claimed_by;
        ELSE
            d.state := 'captured';
        END IF;
        RETURN d;
    END;
    $wdb$;

    CREATE OR REPLACE FUNCTION work_item_mirror_bus_trg() RETURNS trigger LANGUAGE plpgsql AS $wmbt$
    BEGIN
        BEGIN
            PERFORM work_item_mirror('bus', 'agent_messages', NEW.id,
                work_item_derive_bus(NEW.id, NEW.subject, NEW.responded_at, NEW.skipped_at,
                                      NEW.claimed_by, NEW.response_ref),
                'trigger:agent_messages');
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'work_item mirror failed for agent_messages id=%: %', NEW.id, SQLERRM;
        END;
        RETURN NEW;
    END;
    $wmbt$;

    CREATE TRIGGER work_item_mirror_bus_trg
        AFTER INSERT OR UPDATE ON agent_messages
        FOR EACH ROW WHEN (NEW.requires_response)
        EXECUTE FUNCTION work_item_mirror_bus_trg();
"""


def _setup_base_and_123(cur):
    """Schema + role bootstrap, migration 117, then 122 (RPC optional-params
    extension), then 123 (the additive half -- filename order is now dependency
    order, see 122's own header for the renumbering story). 124 (the revoke)
    does not ship in this PR (Musa op#34845, bus #78393 ruling) -- see
    migrations/123_durable_inbox_enforce_bus_writer.sql's header.

    `cur` must already be connected AS live_applier (see additive_db below), not the
    initdb superuser -- this function creates the public schema, agent_messages,
    work_item and the RPCs itself, so live_applier ends up owning all of it, exactly
    mirroring how postgres owns these objects on the real substrate."""
    migration_117 = (MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text()
    migration_123 = (MIGRATIONS_DIR / "123_durable_inbox_enforce_bus_writer.sql").read_text()
    migration_122 = (MIGRATIONS_DIR / "122_post_agent_message_optional_params.sql").read_text()

    cur.execute("DROP SCHEMA public CASCADE")
    cur.execute("CREATE SCHEMA public")
    # A freshly CREATEd schema does NOT inherit the USAGE-to-PUBLIC grant that
    # initdb's bootstrap seeds onto the ORIGINAL 'public' schema -- that grant is
    # what lets a role resolve unqualified table names it doesn't own at all (not
    # a privilege 117's own tests ever exercised, since those never SET ROLE to
    # anything other than the schema's own creator). Restoring it here so this
    # fixture matches what the real substrate's 'public' schema has always had --
    # this is a test-fixture-only fix, NOT something migration 123 itself grants,
    # since the real schema was never dropped/recreated and never lost it.
    cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        if cur.fetchone() is None:
            cur.execute(f"CREATE ROLE {role}")
    # live_applier (CREATEROLE) gets automatic ADMIN on these from creating them (PG16+),
    # but NOT SET -- same createrole_self_grant-empty rule as bus_writer (Musa op#34845,
    # bus #78576). Tests below `SET ROLE service_role/anon/authenticated` to simulate
    # different real callers, which needs SET; this is test-fixture-only plumbing (on the
    # real substrate these roles pre-exist, postgres doesn't create them), so it is NOT
    # part of migration 123 -- granted here, not in the migration file. INHERIT FALSE so
    # live_applier never ambiently gains their privileges outside an explicit SET ROLE.
    cur.execute("GRANT anon, authenticated, service_role TO live_applier WITH INHERIT FALSE, SET TRUE")

    # Supabase's standing ALTER DEFAULT PRIVILEGES rule (Musa op#34845, bus #78531
    # console live finding): grants EXECUTE on every NEW function to anon/authenticated/
    # service_role individually, at CREATE time -- separate from and unaffected by a
    # later REVOKE EXECUTE ... FROM PUBLIC. Must be set BEFORE migration_117 runs (it
    # creates mark_delivered/ack_message) to reproduce the live anon/authenticated
    # EXECUTE hole console found on tscuymavysscrvoberrr.
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
    cur.execute(_WORK_ITEM_SCHEMA)

    # Today's real starting grants (Supabase's blanket default-ACL on the live
    # substrate -- simulated explicitly here since this isolated cluster has no
    # such seed), BEFORE 123 runs -- so 124's later REVOKE has something real to
    # revoke, and the work_item-mirror test proves bus_writer needs its OWN grant
    # rather than accidentally inheriting service_role's.
    cur.execute("GRANT SELECT, INSERT, UPDATE ON agent_messages TO service_role")
    cur.execute("GRANT USAGE ON SEQUENCE agent_messages_id_seq TO service_role")
    cur.execute("GRANT SELECT, INSERT, UPDATE ON work_item TO service_role")
    cur.execute("GRANT INSERT ON work_item_audit TO service_role")

    cur.execute(migration_123)


@pytest.fixture
def additive_db(pg_dsn):
    """123 only -- the real substrate's actual end-state for as long as PR-B (migration
    124, the revoke) is pending a full writer inventory (Musa op#34845, bus #78393
    ruling), not an intermediate stage. service_role (and postgres) must still be able
    to write directly; that is the whole point of splitting the revoke out (bus #74885,
    reaffirmed by bus #78393).

    Musa op#34845, bus #78576 (console's live repro on PG17.11, NOT GO on 471296d):
    live's applying role (postgres) is rolsuper=FALSE, rolcreaterole=TRUE,
    rolbypassrls=TRUE, with createrole_self_grant empty -- NOT the initdb superuser
    pg_dsn's own connection uses. Since PG16, a CREATEROLE (non-superuser) role that
    creates another role gets ADMIN on it automatically but NOT SET, unless
    createrole_self_grant includes 'set' (live's is empty) -- so `ALTER FUNCTION ...
    OWNER TO bus_writer` (which requires SET on bus_writer) fails live with "must be
    able to SET ROLE bus_writer", rolled back whole-transaction. Running setup as the
    initdb superuser (the old approach) always has SET on every role implicitly, so it
    never caught this. `live_applier` mirrors live's postgres role shape exactly;
    everything from the public schema down is created BY it (not just granted to it),
    so it owns it all, same as postgres does live -- that ownership is what lets it run
    migrations 117/122/123 at all without being superuser."""
    with psycopg.connect(pg_dsn, autocommit=True) as super_conn, super_conn.cursor() as super_cur:
        super_cur.execute(
            "SELECT 1 FROM pg_roles WHERE rolname='live_applier'"
        )
        if super_cur.fetchone() is None:
            super_cur.execute(
                "CREATE ROLE live_applier LOGIN NOSUPERUSER CREATEROLE BYPASSRLS"
            )
        # initdb's own 'public' schema is owned by the cluster superuser, not by any
        # CREATEROLE role -- hand it to live_applier so it can DROP/recreate it itself
        # below (_setup_base_and_123 does this every run for a clean reset), same as
        # live's postgres owns the real substrate's public schema.
        super_cur.execute("ALTER SCHEMA public OWNER TO live_applier")
        # CREATE SCHEMA needs CREATE on the database itself (PG15+ no longer grants
        # this to PUBLIC by default) -- live's postgres role has it on the real
        # substrate database or it could never have created anything there.
        super_cur.execute("GRANT CREATE ON DATABASE postgres TO live_applier")

    applier_dsn = pg_dsn.replace("user=postgres", "user=live_applier")
    with psycopg.connect(applier_dsn, autocommit=True) as conn, conn.cursor() as cur:
        _setup_base_and_123(cur)

    conn = psycopg.connect(applier_dsn)
    yield conn
    conn.close()


def test_direct_insert_as_service_role_still_works_with_123_alone(additive_db):
    """The proof the split is supposed to buy us (orch-console ruling, bus #74885):
    123 by itself must NOT break any existing direct writer -- that is what makes it
    safe to merge and apply ahead of the ~50-script migration."""
    with additive_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "INSERT INTO agent_messages "
            "(from_agent, to_agent, message_type, subject, body, priority, "
            " requires_response, thread_id) "
            "VALUES ('x','y','update','direct write still ok','b','P2', false, gen_random_uuid())"
        )
    additive_db.commit()


def test_post_agent_message_still_succeeds_with_123_alone(additive_db):
    with additive_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("x", "y", "update", "via rpc", "b", "P2", False, str(uuid.uuid4()), None),
        )
        row = cur.fetchone()
    additive_db.commit()
    assert row[0] is not None


def test_mark_delivered_and_ack_work_with_123_alone(additive_db):
    with additive_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("x", "y", "update", "s", "b", "P2", False, str(uuid.uuid4()), None),
        )
        (row_id, _thread) = cur.fetchone()
        cur.execute("SELECT mark_delivered(%s, %s)", (row_id, 60))
        cur.execute("SELECT ack_message(%s)", (row_id,))
    additive_db.commit()
    with additive_db.cursor() as cur:
        cur.execute("SELECT delivered_at, acked_at FROM agent_messages WHERE id=%s", (row_id,))
        delivered_at, acked_at = cur.fetchone()
    assert delivered_at is not None
    assert acked_at is not None


def test_direct_update_for_read_at_still_works_with_123_alone(additive_db):
    """Regression 123 never had: the fleet-wide mark-read/respond path
    (`UPDATE agent_messages SET read_at=now()...`, mandated by every lane's own
    CLAUDE.md) is a direct UPDATE, deliberately outside this enforcement's scope."""
    with additive_db.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages "
            "(from_agent, to_agent, message_type, subject, body, priority, "
            " requires_response, thread_id) "
            "VALUES ('x','y','update','s','b','P2', false, gen_random_uuid()) RETURNING id"
        )
        (row_id,) = cur.fetchone()
    additive_db.commit()

    with additive_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute("UPDATE agent_messages SET read_at=now() WHERE id=%s", (row_id,))
    additive_db.commit()

    with additive_db.cursor() as cur:
        cur.execute("SELECT read_at FROM agent_messages WHERE id=%s", (row_id,))
        (read_at,) = cur.fetchone()
    assert read_at is not None


def test_123_is_idempotent_on_rerun(additive_db):
    migration_123 = (MIGRATIONS_DIR / "123_durable_inbox_enforce_bus_writer.sql").read_text()
    with additive_db.cursor() as cur:
        cur.execute(migration_123)  # re-run on an already-applied schema -- must not raise
    additive_db.commit()


def test_work_item_mirror_still_fires_via_rpc_with_123_alone(additive_db):
    """The audit finding (bus #74860): without bus_writer's own work_item/work_item_audit
    grants, this silently degrades (trigger swallows the error) instead of failing loud --
    so the only way to catch it is to check the mirror row actually landed."""
    with additive_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("x", "y", "update", "needs reply", "b", "P1", True, str(uuid.uuid4()), None),
        )
        (row_id, _thread) = cur.fetchone()
    additive_db.commit()

    with additive_db.cursor() as cur:
        cur.execute(
            "SELECT state FROM work_item WHERE source_table='agent_messages' AND source_id=%s",
            (row_id,),
        )
        row = cur.fetchone()
    assert row is not None, (
        "work_item mirror did not fire -- bus_writer is likely missing the "
        "work_item/work_item_audit grants"
    )
    assert row[0] == "captured"


def test_work_item_mirror_resolves_from_agent_when_responded_at_preset_with_123_alone(
    additive_db,
):
    """Musa op#34845, bus #78352 finding #3: the test above only proves the mirror
    fires on a FRESH insert (responded_at NULL), which never reaches
    work_item_derive_bus's `responded_at IS NOT NULL` branch -- so it never actually
    exercised the from_agent grant this migration adds.

    The reachable path: an unrelated, earlier UPDATE (the fleet-wide respond
    convention -- same class as read_at, deliberately outside 123/124's scope, see
    test_direct_update_for_read_at_still_works_with_123_alone -- performed by
    service_role, which already has full column access) sets responded_at/
    response_ref on a requires_response row. That first mirror succeeds and sets
    work_item to 'done' regardless of bus_writer's grants -- asserting on final
    state alone (an earlier, since-corrected version of this test did exactly that)
    proves NOTHING about bus_writer, because the row was already 'done' before
    bus_writer touched it.

    The actual bug surface is the SECOND time the trigger fires: bus_writer's own
    SECURITY DEFINER ack_message() UPDATEs the same row (only acked_at), but
    work_item_mirror_bus_trg is SECURITY INVOKER and re-fires on ANY UPDATE of a
    requires_response row, with NEW.responded_at still non-NULL from the first
    UPDATE -- so it re-enters the from_agent branch, this time AS bus_writer. If
    bus_writer lacks the from_agent grant, THIS SELECT raises InsufficientPrivilege,
    caught and swallowed by the trigger's own exception handler (bus #73195) as a
    bare WARNING -- work_item's state doesn't change because it was already 'done',
    so no assertion on work_item rows can see this regression. The only observable
    signal is the WARNING itself, captured here via psycopg's notice handler (same
    pattern as test_migration_assertion_gate_passes in
    test_boot_briefing_substrate_visibility.py)."""
    notices: list[str] = []

    def _collect(diag: psycopg.errors.Diagnostic) -> None:
        if diag.message_primary:
            notices.append(diag.message_primary.strip())

    additive_db.add_notice_handler(_collect)

    with additive_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "INSERT INTO agent_messages "
            "(from_agent, to_agent, message_type, subject, body, priority, "
            " requires_response, thread_id) "
            "VALUES ('orig-replier','y','update','original','b','P2', false, "
            "gen_random_uuid()) RETURNING id"
        )
        (orig_id,) = cur.fetchone()
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("x", "y", "update", "needs reply", "b", "P1", True, str(uuid.uuid4()), None),
        )
        (reply_id, _thread) = cur.fetchone()
        # The unrelated, earlier UPDATE -- fires the trigger AS service_role, which
        # already has full column access, so this mirror succeeds regardless of
        # bus_writer's grants and sets state to 'done' BEFORE bus_writer is ever
        # involved. This is deliberate: it's what makes the later ack_message()
        # call's re-fire the ONLY thing that can still go wrong.
        cur.execute(
            "UPDATE agent_messages SET responded_at=now(), response_ref=%s WHERE id=%s",
            (str(orig_id), reply_id),
        )
    additive_db.commit()

    with additive_db.cursor() as cur:
        cur.execute(
            "SELECT state, done_by FROM work_item "
            "WHERE source_table='agent_messages' AND source_id=%s",
            (reply_id,),
        )
        row = cur.fetchone()
    assert row == ("done", "orig-replier"), (
        "sanity check failed before bus_writer is even involved -- the earlier "
        f"service_role UPDATE's own mirror didn't land as expected: {row}"
    )

    notices.clear()  # only care about what ack_message's own re-fire produces

    with additive_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        # SECURITY DEFINER -- runs AS bus_writer (the function owner since 123),
        # re-firing work_item_mirror_bus_trg AS bus_writer with NEW.responded_at
        # still non-NULL from the UPDATE above. This is the actual path console's
        # finding #3 is about.
        cur.execute("SELECT ack_message(%s)", (reply_id,))
    additive_db.commit()

    mirror_failures = [n for n in notices if "mirror failed" in n.lower()]
    assert not mirror_failures, (
        "bus_writer's own re-fire of work_item_mirror_bus_trg raised a (swallowed) "
        f"WARNING -- bus_writer is likely missing the from_agent column grant: "
        f"{mirror_failures}"
    )


def test_anon_and_authenticated_cannot_execute_mark_delivered_or_ack_message(additive_db):
    """Musa op#34845, bus #78531 (console's own live finding on tscuymavysscrvoberrr):
    mark_delivered/ack_message are SECURITY DEFINER and, before this fix, anon and
    authenticated held EXECUTE via Supabase's standing ALTER DEFAULT PRIVILEGES rule
    (same mechanism as finding D on post_agent_message) -- unaffected by migration 117's
    own `REVOKE EXECUTE ... FROM PUBLIC` (PUBLIC is not anon/authenticated). That meant
    anyone holding the public anon key could mark ANY bus row delivered or acked via
    PostgREST. service_role must keep EXECUTE (migration 117's original grant, untouched)
    since that is the legitimate caller path."""
    with additive_db.cursor() as cur:
        for role in ("anon", "authenticated"):
            for fn in ("mark_delivered(bigint,integer)", "ack_message(bigint)"):
                cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
                assert cur.fetchone()[0] is False, f"{role} can still EXECUTE {fn}"
        for fn in ("mark_delivered(bigint,integer)", "ack_message(bigint)"):
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", ("service_role", fn))
            assert cur.fetchone()[0] is True, f"service_role lost its legitimate EXECUTE on {fn}"










