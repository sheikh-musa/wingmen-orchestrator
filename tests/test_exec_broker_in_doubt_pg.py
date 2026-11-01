"""Real-PG proof for queue row 272 (migrations/147_hc95_exec_broker_in_doubt.sql +
vault_log.mark_previous_boots_in_doubt): a leftover 'started' row from a fake
previous boot becomes 'in_doubt' (in_doubt_at set, ts_end still NULL), a
current-boot row and a live other-host broker's row are untouched, and nothing
is re-executed. Runs against the repo's sanctioned ephemeral Postgres fixture
(pg_dsn); under WINGMEN_REQUIRE_PG_PROOFS=1 a missing PG server FAILS."""
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

from exec_broker.vault_log import IN_DOUBT_STATUS, VaultLogEntry, log_started, mark_previous_boots_in_doubt  # noqa: E402

_MIG = _ROOT / "migrations"
_FILES = [_MIG / "drafts" / "hc95_exec_broker_access_log.sql",   # live, outside the numbered chain
          _MIG / "142_hc95_exec_broker_gate_ref.sql",
          _MIG / "144_hc95_exec_broker_heartbeat.sql",
          _MIG / "147_hc95_exec_broker_in_doubt.sql"]


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
        c.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
        for role in ("authenticated", "anon", "service_role", "exec_broker_role"):
            c.execute(f"DO $$ BEGIN CREATE ROLE {role}; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        c.execute("DROP TABLE IF EXISTS exec_broker_heartbeat")
        for f in _FILES:
            c.execute(f.read_text())
        c.execute(_FILES[-1].read_text())   # 147 is idempotent
        c.execute("DELETE FROM exec_broker_access_log")
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DELETE FROM exec_broker_access_log")
        c.execute("DROP TABLE IF EXISTS exec_broker_heartbeat")


def _row(c, boot, ledger):
    with c.cursor() as cur:
        rid = log_started(cur, VaultLogEntry(exec_ledger_id=ledger, caller="c", project_ref="syntheticref",
                                             guarded_sql="update t set a = 1", reason="", gate_id="1",
                                             boot_id=boot))
    c.commit()
    return rid


def _get(c, rid):
    return c.execute("SELECT status, ts_end, in_doubt_at, boot_id FROM exec_broker_access_log WHERE id = %s",
                     (rid,)).fetchone()


def test_previous_boot_started_row_becomes_in_doubt_current_boot_untouched(conn):
    prev, live_other, me = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    r_prev = _row(conn, prev, "e-prev")
    r_null = _row(conn, None, "e-preboot")           # written before boot_id existed
    r_me = _row(conn, me, "e-me")
    r_other = _row(conn, live_other, "e-other")      # a broker alive on ANOTHER host
    r_done = _row(conn, prev, "e-done")
    conn.execute("UPDATE exec_broker_access_log SET status = 'ok', ts_end = now() WHERE id = %s", (r_done,))
    conn.execute(
        "INSERT INTO exec_broker_heartbeat (project_ref, holder_host, broker_pid, boot_id, interval_seconds, ttl_seconds) "
        "VALUES ('syntheticref', 'otherhost', 1, %s, 60, 180)", (live_other,))
    conn.commit()
    with conn.cursor() as cur:
        n, oldest = mark_previous_boots_in_doubt(cur, boot_id=me, host="gzbai")
    conn.commit()
    assert n == 2 and oldest >= 0
    for rid in (r_prev, r_null):
        row = _get(conn, rid)
        assert row["status"] == IN_DOUBT_STATUS == "in_doubt"
        assert row["in_doubt_at"] is not None and row["ts_end"] is None      # never finalized, no fake end time
    assert _get(conn, r_me)["status"] == "started"
    assert _get(conn, r_other)["status"] == "started"
    assert _get(conn, r_done)["status"] == "ok"
    # idempotent: a second boot pass marks nothing new and re-executes nothing
    with conn.cursor() as cur:
        assert mark_previous_boots_in_doubt(cur, boot_id=me, host="gzbai") == (0, 0)
    conn.commit()


def test_stale_other_host_heartbeat_does_not_protect_its_rows(conn):
    dead_other, me = str(uuid.uuid4()), str(uuid.uuid4())
    rid = _row(conn, dead_other, "e-dead")
    conn.execute(
        "INSERT INTO exec_broker_heartbeat (project_ref, holder_host, broker_pid, boot_id, beat_at, interval_seconds, ttl_seconds) "
        "VALUES ('syntheticref', 'otherhost', 1, %s, now() - interval '1 hour', 60, 180)", (dead_other,))
    conn.commit()
    with conn.cursor() as cur:
        assert mark_previous_boots_in_doubt(cur, boot_id=me, host="gzbai")[0] == 1
    conn.commit()
    assert _get(conn, rid)["status"] == "in_doubt"


def test_status_check_accepts_in_doubt_and_enforces_in_doubt_at(conn):
    rid = _row(conn, str(uuid.uuid4()), "e-x")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE exec_broker_access_log SET status = 'in_doubt' WHERE id = %s", (rid,))
    conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE exec_broker_access_log SET status = 'bogus' WHERE id = %s", (rid,))
    conn.rollback()
    idx = conn.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'exec_broker_access_log_started_open_idx'").fetchone()
    assert idx and "status = 'started'" in idx["indexdef"] and "ts_end IS NULL" in idx["indexdef"]
