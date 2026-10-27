"""WATCHDOGS batch (backlog#124, bus #74939/#74954): fleet_health.surface_dead_letters(),
hub_bus_currency_monitor._page(), and hub_self_recovery_staleness_watchdog._page() each loop
over multiple recipients on ONE shared cursor with a SINGLE commit after the loop (an
all-recipients-together batch), so they were migrated onto post_agent_message() directly on
that existing cursor rather than bus_send.send() (which would open its own connection and
commit each recipient independently, breaking the batch). These tests run the real RPC call
against a real ephemeral Postgres -- the risk worth catching is a positional-argument mistake
in the 11-arg direct call, which py_compile can't see, and none of these 3 had a test that
exercised the actual (non-dry) write path before this migration.
"""
import pathlib
import sys

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scripts import fleet_health as fh  # noqa: E402
from scripts import hub_bus_currency_monitor as hbcm  # noqa: E402
from scripts import hub_self_recovery_staleness_watchdog as hsrw  # noqa: E402

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def db(pg_dsn):
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
                read_at            TIMESTAMPTZ,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute(migration_117)
        cur.execute(migration_122)

    conn = psycopg.connect(pg_dsn)
    yield conn
    conn.close()


def _rows(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT from_agent, to_agent, message_type, subject, priority, "
                    "requires_response FROM agent_messages ORDER BY id")
        return cur.fetchall()


def test_fleet_health_surface_dead_letters_batch_commits_together(db):
    with db.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "priority,requires_response,thread_id) VALUES "
            "('some-agent','musa','update','hello','body','P2',false,gen_random_uuid())")
    db.commit()
    with db.cursor() as cur:
        surfaced = fh.surface_dead_letters(cur, dry=False)
    db.commit()
    assert surfaced == ["musa"]
    rows = _rows(db)
    page_rows = [r for r in rows if r[0] == "cc-fleet-health" and r[1] == "orch-console"]
    assert len(page_rows) == 1
    assert page_rows[0][2] == "update" and page_rows[0][4] == "P2" and page_rows[0][5] is False


def test_hub_bus_currency_monitor_page_writes_all_recipients(db, monkeypatch):
    monkeypatch.setattr(hbcm, "RECIPIENTS", ("orch-console", "cc-fleet-health"))
    with db.cursor() as cur:
        sent = hbcm._page(cur, unread_count=12, oldest_unread_age_s=9000.0,
                          reason="test reason", oldest_id=99)
    db.commit()
    assert sent == 2
    rows = _rows(db)
    recipients = {r[1] for r in rows if r[0] == hbcm.SRE_FROM}
    assert recipients == {"orch-console", "cc-fleet-health"}
    for r in rows:
        assert r[4] == "P2" and r[5] is False


def test_hub_self_recovery_staleness_watchdog_page_writes_all_recipients(db, monkeypatch):
    monkeypatch.setattr(hsrw, "RECIPIENTS", ("orch-console", "cc-fleet-health"))
    with db.cursor() as cur:
        sent = hsrw._page(cur, last_tick_age_s=720.0, reason="test reason", last_tick_reason=None)
    db.commit()
    assert sent == 2
    rows = _rows(db)
    recipients = {r[1] for r in rows if r[0] == hsrw.SRE_FROM}
    assert recipients == {"orch-console", "cc-fleet-health"}
    for r in rows:
        assert r[4] == "P2" and r[5] is False
