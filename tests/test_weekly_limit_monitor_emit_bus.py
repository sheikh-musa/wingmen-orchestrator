"""weekly_limit_monitor._emit_bus() (backlog#124, bus #74939): now calls
post_agent_message() directly on its own cursor instead of a raw INSERT, followed
by an UPDATE stamping responded_at=now() in the same transaction (the RPC has no
responded_at param, and this alert is deliberately pre-answered so it never
re-creates the SLA false-stall flood). Runs the real SQL against a real ephemeral
Postgres to catch a positional-argument mistake and confirm responded_at lands.
"""
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import nervous_system.weekly_limit_monitor as wlm  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def db(pg_dsn, monkeypatch):
    migration_117 = (MIGRATIONS_DIR / "117_durable_inbox_ack_additive.sql").read_text()
    migration_122 = (MIGRATIONS_DIR / "122_post_agent_message_optional_params.sql").read_text()
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
                responded_at       TIMESTAMPTZ,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute(migration_117)
        cur.execute(migration_122)

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    yield pg_dsn


def test_emit_bus_writes_the_row_pre_answered(db):
    wlm._emit_bus("subj", "body text", "P1")
    with psycopg.connect(db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT from_agent, to_agent, message_type, subject, body, priority, "
            "requires_response, responded_at FROM agent_messages")
        row = cur.fetchone()
    from_agent, to_agent, mtype, subject, body, priority, req, responded_at = row
    assert from_agent == "cc-fleet-health" and to_agent == "orch-console"
    assert mtype == "update" and subject == "subj" and body == "body text"
    assert priority == "P1" and req is False
    assert responded_at is not None, (
        "this alert must be pre-stamped responded_at at insert time, or it "
        "re-creates the SLA false-stall flood")


def test_emit_bus_raises_loud_on_no_dsn(db, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    with pytest.raises(RuntimeError):
        wlm._emit_bus("subj", "body", "P1")
