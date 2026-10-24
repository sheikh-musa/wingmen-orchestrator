"""Tests for migrations 118 and 119 (backlog#124, durable-inbox final step, split per
orch-console ruling bus #74885): a dedicated bus_writer role owns
post_agent_message/mark_delivered/ack_message and holds the INSERT-capable grants
(migration 118, purely additive), so that revoking direct INSERT (+ sequence USAGE)
from service_role on agent_messages (migration 119, the breaking half, gated on the
fleet-wide enforce-in-code check going green) cannot self-defeat the RPCs that must
survive it (orch-console design review, bus #74858).

`additive_db` exercises 118 alone -- the state the substrate will be in as soon as
this merges. `enforced_db` exercises 118+119 -- the gated end-state nothing applies
until orch-console releases it.

Real risk this file's last test exists to catch (my own audit finding, bus #74860):
agent_messages' AFTER-trigger work_item_mirror_bus_trg (and work_item_mirror() itself)
are SECURITY INVOKER, not DEFINER. Once the posting RPCs are owned by bus_writer, that
trigger fires AS bus_writer -- needing SELECT/INSERT/UPDATE on work_item and INSERT on
work_item_audit, which service_role has today only via Supabase's blanket default-ACL
(confirmed from migrations 039/049's own headers) and which bus_writer gets NONE of
automatically. Without an explicit grant this degrades silently (the trigger's own
try/catch, orch-console bus #73195, swallows the error) -- invisible to any suite that
never touches work_item.
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


def _setup_base_and_118(cur):
    """Schema + role bootstrap, migration 117, then ONLY 118 (the additive half).
    Shared by both fixtures below -- enforced_db just applies 119 on top."""
    migration_117 = (MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text()
    migration_118 = (MIGRATIONS_DIR / "118_durable_inbox_enforce_bus_writer.sql").read_text()

    cur.execute("DROP SCHEMA public CASCADE")
    cur.execute("CREATE SCHEMA public")
    # A freshly CREATEd schema does NOT inherit the USAGE-to-PUBLIC grant that
    # initdb's bootstrap seeds onto the ORIGINAL 'public' schema -- that grant is
    # what lets a role resolve unqualified table names it doesn't own at all (not
    # a privilege 117's own tests ever exercised, since those never SET ROLE to
    # anything other than the schema's own creator). Restoring it here so this
    # fixture matches what the real substrate's 'public' schema has always had --
    # this is a test-fixture-only fix, NOT something migration 118 itself grants,
    # since the real schema was never dropped/recreated and never lost it.
    cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        if cur.fetchone() is None:
            cur.execute(f"CREATE ROLE {role}")

    cur.execute(_AGENT_MESSAGES_SCHEMA)
    cur.execute(migration_117)
    cur.execute(_WORK_ITEM_SCHEMA)

    # Today's real starting grants (Supabase's blanket default-ACL on the live
    # substrate -- simulated explicitly here since this isolated cluster has no
    # such seed), BEFORE 118 runs -- so 119's later REVOKE has something real to
    # revoke, and the work_item-mirror test proves bus_writer needs its OWN grant
    # rather than accidentally inheriting service_role's.
    cur.execute("GRANT SELECT, INSERT, UPDATE ON agent_messages TO service_role")
    cur.execute("GRANT USAGE ON SEQUENCE agent_messages_id_seq TO service_role")
    cur.execute("GRANT SELECT, INSERT, UPDATE ON work_item TO service_role")
    cur.execute("GRANT INSERT ON work_item_audit TO service_role")

    cur.execute(migration_118)


@pytest.fixture
def additive_db(pg_dsn):
    """118 only -- the state the real substrate will be in once this PR merges,
    before orch-console releases 119. service_role must still be able to write
    directly; that is the whole point of splitting the revoke out (bus #74885)."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        _setup_base_and_118(cur)

    conn = psycopg.connect(pg_dsn)
    yield conn
    conn.close()


@pytest.fixture
def enforced_db(pg_dsn):
    """118 + 119 -- the state after orch-console's release gate (fleet-wide
    enforce-in-code check green) lets the revoke apply."""
    migration_119 = (MIGRATIONS_DIR / "119_durable_inbox_revoke_direct_insert.sql").read_text()
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        _setup_base_and_118(cur)
        cur.execute(migration_119)

    conn = psycopg.connect(pg_dsn)
    yield conn
    conn.close()


def test_direct_insert_as_service_role_still_works_with_118_alone(additive_db):
    """The proof the split is supposed to buy us (orch-console ruling, bus #74885):
    118 by itself must NOT break any existing direct writer -- that is what makes it
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


def test_post_agent_message_still_succeeds_with_118_alone(additive_db):
    with additive_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("x", "y", "update", "via rpc", "b", "P2", False, str(uuid.uuid4()), None),
        )
        row = cur.fetchone()
    additive_db.commit()
    assert row[0] is not None


def test_mark_delivered_and_ack_work_with_118_alone(additive_db):
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


def test_direct_update_for_read_at_still_works_with_118_alone(additive_db):
    """Regression 118 never had: the fleet-wide mark-read/respond path
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


def test_118_is_idempotent_on_rerun(additive_db):
    migration_118 = (MIGRATIONS_DIR / "118_durable_inbox_enforce_bus_writer.sql").read_text()
    with additive_db.cursor() as cur:
        cur.execute(migration_118)  # re-run on an already-applied schema -- must not raise
    additive_db.commit()


def test_work_item_mirror_still_fires_via_rpc_with_118_alone(additive_db):
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


def test_direct_insert_as_service_role_fails_after_full_enforcement(enforced_db):
    """119 is the actual breaking change -- this is what it buys, and only this."""
    with enforced_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO agent_messages "
                "(from_agent, to_agent, message_type, subject, body, priority, "
                " requires_response, thread_id) "
                "VALUES ('x','y','update','direct write','b','P2', false, gen_random_uuid())"
            )
    enforced_db.rollback()


def test_post_agent_message_still_succeeds_after_full_enforcement(enforced_db):
    """The actual no-self-defeat proof under the real end-state: service_role (the real
    caller identity) can still write through the RPC even with its direct INSERT gone."""
    with enforced_db.cursor() as cur:
        cur.execute("SET ROLE service_role")
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("x", "y", "update", "via rpc", "b", "P2", False, str(uuid.uuid4()), None),
        )
        row = cur.fetchone()
    enforced_db.commit()
    assert row[0] is not None


def test_119_is_idempotent_on_rerun(enforced_db):
    migration_119 = (MIGRATIONS_DIR / "119_durable_inbox_revoke_direct_insert.sql").read_text()
    with enforced_db.cursor() as cur:
        cur.execute(migration_119)  # re-run on an already-revoked schema -- must not raise
    enforced_db.commit()














