"""Real-PG proof for migrations/drafts/hc95_exec_broker_heartbeat.sql + the
heartbeat CAS take (cc-fleet-health review): a second daemon on the SAME host
re-taking a gated ref's row is stamped (taken_over_from = first boot_id,
takeover_reason = 'same_host_new_boot'); a fresh foreign holder is never stolen.
Runs against the repo's sanctioned ephemeral Postgres fixture (pg_dsn); under
WINGMEN_REQUIRE_PG_PROOFS=1 a missing PG server FAILS instead of skipping."""
from __future__ import annotations

import os
import pathlib
import sys
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import assert_dsn_is_not_production

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "lib"))

from exec_broker.heartbeat import renew, take  # noqa: E402

_DRAFT = _ROOT / "migrations" / "drafts" / "hc95_exec_broker_heartbeat.sql"
_REF = "syntheticbrokerqfakesilo"


@pytest.fixture
def conn(request):
    if os.environ.get("WINGMEN_REQUIRE_PG_PROOFS") == "1":
        pg_bin = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
        for name in ("initdb", "pg_ctl", "postgres"):
            if not os.path.exists(os.path.join(pg_bin, name)):
                pytest.fail(f"WINGMEN_REQUIRE_PG_PROOFS=1 but PG server binary {name} is missing")
    dsn = request.getfixturevalue("pg_dsn")
    assert_dsn_is_not_production(dsn)
    with psycopg.connect(dsn, autocommit=True) as c:
        for role in ("authenticated", "anon", "service_role", "exec_broker_role"):
            c.execute(f"DO $$ BEGIN CREATE ROLE {role}; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        c.execute("DROP TABLE IF EXISTS exec_broker_heartbeat")
        c.execute(_DRAFT.read_text())
        c.execute(_DRAFT.read_text())   # idempotent re-apply
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS exec_broker_heartbeat")


def _take(c, host, boot):
    with c.cursor() as cur:
        ok = take(cur, project_ref=_REF, host=host, pid=4242, boot_id=boot, interval_seconds=60, ttl_seconds=180)
    c.commit()
    return ok


def _row(c):
    return c.execute("SELECT * FROM exec_broker_heartbeat WHERE project_ref = %s", (_REF,)).fetchone()


def test_same_host_second_boot_take_is_stamped(conn):
    b1, b2 = str(uuid.uuid4()), str(uuid.uuid4())
    assert _take(conn, "gzbai", b1)
    assert _row(conn)["taken_over_from"] is None
    assert _take(conn, "gzbai", b2)                       # same host, new boot -> allowed AND visible
    row = _row(conn)
    assert str(row["boot_id"]) == b2
    assert row["taken_over_from"] == b1 and row["takeover_reason"] == "same_host_new_boot"


def test_fresh_foreign_holder_is_never_stolen_and_renew_records_the_probe(conn):
    b1 = str(uuid.uuid4())
    assert _take(conn, "gzbai", b1)
    assert not _take(conn, "otherhost", str(uuid.uuid4()))   # live row on another host
    assert str(_row(conn)["boot_id"]) == b1
    with conn.cursor() as cur:
        assert renew(cur, project_ref=_REF, boot_id=b1, loop_ok=False, loop_latency_ms=2000,
                     loop_error="timeout after 2s (accept loop not responding)")
    conn.commit()
    row = _row(conn)
    assert row["beat_seq"] == 1 and row["loop_ok"] is False and row["loop_checked_at"] is not None


def test_expired_row_on_another_host_is_taken_with_host_change(conn):
    b1, b2 = str(uuid.uuid4()), str(uuid.uuid4())
    assert _take(conn, "gzbai", b1)
    conn.execute("UPDATE exec_broker_heartbeat SET beat_at = now() - interval '1 hour' WHERE project_ref = %s", (_REF,))
    conn.commit()
    assert _take(conn, "otherhost", b2)
    row = _row(conn)
    assert row["taken_over_from"] == b1 and row["takeover_reason"] == "host_change"
