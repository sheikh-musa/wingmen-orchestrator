"""Tests for migration 118 (backlog#124, bus #74939; renumbered from 120 to 118 after
a PR#490 CI finding -- see migrations/118_post_agent_message_optional_params.sql's
header): post_agent_message extended with
2 optional, defaulted trailing params (p_is_test, p_posted_by_identity) so the 3 scripts
held back from the one-shots migration (lane_drill_seed.py, nervous_system/qwen_lane.py,
opus_reprobe_storefront.py) can express what their raw INSERT sets today without a lossy
behavior change.

The real risk this file exists to catch: Postgres identifies a function by name AND
parameter list, so a naive CREATE OR REPLACE with 2 extra params would create a SECOND
overload alongside the original 9-param one, making every existing 9-positional-arg
caller raise "function is not unique". Migration 118 DROPs the old 9-param signature
first so only one overload exists -- this suite proves that actually holds.
"""
import pathlib

import psycopg
import pytest

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def db(pg_dsn):
    migration_117 = (MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text()
    migration_120 = (MIGRATIONS_DIR / "118_post_agent_message_optional_params.sql").read_text()
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname='service_role'")
        if cur.fetchone() is None:
            cur.execute("CREATE ROLE service_role")
        cur.execute("""
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
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute(migration_117)
        cur.execute(migration_120)
        # Faithful minimal reproduction of the REAL provenance trigger
        # (migrations/infra/bedrock_substrate_core.sql's populate_agent_messages_
        # provenance) -- the thing p_posted_by_identity's default (NULL) actually
        # interacts with on the real substrate.
        cur.execute("""
            CREATE FUNCTION populate_agent_messages_provenance() RETURNS trigger
            LANGUAGE plpgsql SECURITY DEFINER SET search_path TO public, pg_temp
            AS $fn$
            BEGIN
                IF NEW.posted_by_identity IS NULL THEN
                    NEW.posted_by_identity := current_user;
                END IF;
                RETURN NEW;
            END;
            $fn$;
            CREATE TRIGGER trg_agent_messages_provenance BEFORE INSERT ON agent_messages
                FOR EACH ROW EXECUTE FUNCTION populate_agent_messages_provenance();
        """)

    conn = psycopg.connect(pg_dsn)
    yield conn
    conn.close()


def _row(db, msg_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT is_test, posted_by_identity FROM agent_messages WHERE id=%s", (msg_id,))
        return cur.fetchone()


def test_9_arg_positional_call_still_resolves_unambiguously_and_behaves_as_before(db):
    """The actual proof migration 118's header promises: an existing 9-arg caller
    (bus_send.py, every already-migrated one-shot) still works, unchanged, with both
    new params defaulted -- no "function is not unique" ambiguity error."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("orch-console", "cc-quality", "update", "subj", "body", "P2", False,
             "11111111-1111-1111-1111-111111111111", None),
        )
        new_id, thread_id = cur.fetchone()
    db.commit()
    is_test, posted_by_identity = _row(db, new_id)
    assert is_test is False
    assert posted_by_identity is not None, "trigger still auto-populates it from current_user"


def test_p_is_test_true_sets_is_test_on_the_row(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("orch-console", "cc-irsyad", "question", "subj", "body", "P2", True,
             "22222222-2222-2222-2222-222222222222", None, True),
        )
        new_id, _ = cur.fetchone()
    db.commit()
    is_test, _ = _row(db, new_id)
    assert is_test is True


def test_explicit_posted_by_identity_is_honored_not_overridden(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("cc-fleet-health", "cc-storefront", "update", "subj", "body", "P1", False,
             "33333333-3333-3333-3333-333333333333", None, False, "cc-fleet-health"),
        )
        new_id, _ = cur.fetchone()
    db.commit()
    _, posted_by_identity = _row(db, new_id)
    assert posted_by_identity == "cc-fleet-health"


def test_omitting_posted_by_identity_still_lets_the_trigger_auto_populate(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("orch-console", "cc-quality", "update", "subj", "body", "P2", False,
             "44444444-4444-4444-4444-444444444444", None, False),
        )
        new_id, _ = cur.fetchone()
    db.commit()
    _, posted_by_identity = _row(db, new_id)
    assert posted_by_identity is not None and posted_by_identity != ""


def test_old_9_param_signature_is_gone(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT to_regprocedure("
            "'public.post_agent_message(text,text,text,text,text,text,boolean,uuid,text)')"
        )
        assert cur.fetchone()[0] is None


def test_migration_120_is_idempotent_on_rerun(db):
    migration_120 = (MIGRATIONS_DIR / "118_post_agent_message_optional_params.sql").read_text()
    with db.cursor() as cur:
        cur.execute(migration_120)
    db.commit()
    # still exactly one overload, still works
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM post_agent_message(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("orch-console", "cc-quality", "update", "subj", "body", "P2", False,
             "55555555-5555-5555-5555-555555555555", None),
        )
        assert cur.fetchone() is not None
