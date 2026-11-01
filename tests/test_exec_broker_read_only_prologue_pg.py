"""Real-PG proof for amended ruling C (console bus #89846/#89875): the broker's
READ_ONLY_PROLOGUE, prepended into the SAME query string and run as ONE
simple-query call (how the Supabase Management API runs a multi-statement
string: one implicit transaction), makes any write fail with SQLSTATE 25006
(read_only_sql_transaction) and write nothing, while a prepended plain read
still returns its rows.

Runs against the repo's sanctioned ephemeral Postgres fixture (pg_dsn,
tests/conftest.py) -- never a real substrate. Under WINGMEN_REQUIRE_PG_PROOFS=1
(set in ci.yml's "Lint & Test" Test step) a missing PG server binary FAILS
this module instead of skipping it.
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
import pytest
from psycopg import pq

from tests.conftest import assert_dsn_is_not_production

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lib"))

from exec_broker.read_only import READ_ONLY_PROLOGUE, is_plain_read  # noqa: E402

_REQUIRE_PG = os.environ.get("WINGMEN_REQUIRE_PG_PROOFS") == "1"
_TABLE = "eb_ro_prologue_proof"


@pytest.fixture
def conn(request):
    if _REQUIRE_PG:
        pg_bin = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
        for name in ("initdb", "pg_ctl", "postgres"):
            if not os.path.exists(os.path.join(pg_bin, name)):
                pytest.fail(f"WINGMEN_REQUIRE_PG_PROOFS=1 but PG server binary {name} is missing "
                            "-- the read-only prologue proofs must RUN in CI, never skip")
    dsn = request.getfixturevalue("pg_dsn")
    assert_dsn_is_not_production(dsn)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"DROP TABLE IF EXISTS {_TABLE}")
        c.execute(f"CREATE TABLE {_TABLE} (id int PRIMARY KEY, v text)")
        c.execute(f"INSERT INTO {_TABLE} VALUES (1, 'a')")
        yield c
        c.execute(f"DROP TABLE IF EXISTS {_TABLE}")


def _simple_query(c: psycopg.Connection, sql: str):
    """ONE libpq PQexec call -- the simple-query protocol, a multi-statement
    string runs as one implicit transaction, exactly like the mgmt API."""
    return c.pgconn.exec_(sql.encode("utf-8"))


def _rows(c) -> list:
    return c.execute(f"SELECT id, v FROM {_TABLE} ORDER BY id").fetchall()


@pytest.mark.parametrize("write", [
    f"INSERT INTO {_TABLE} VALUES (2, 'b')",
    f"UPDATE {_TABLE} SET v = 'z' WHERE id = 1",
    f"DELETE FROM {_TABLE}",
    f"CREATE TABLE {_TABLE}_x (a int)",
    f"SELECT 1; INSERT INTO {_TABLE} VALUES (3, 'c')",
])
def test_prepended_write_fails_25006_and_writes_nothing(conn, write):
    res = _simple_query(conn, READ_ONLY_PROLOGUE + write)
    assert res.status == pq.ExecStatus.FATAL_ERROR
    assert res.error_field(pq.DiagnosticField.SQLSTATE) == b"25006"
    assert _rows(conn) == [(1, "a")]
    assert conn.execute("SELECT to_regclass(%s)", (f"{_TABLE}_x",)).fetchone()[0] is None


def test_prepended_plain_read_succeeds_and_returns_rows(conn):
    sql = f"SELECT count(*) FROM {_TABLE}"
    assert is_plain_read(sql)
    res = _simple_query(conn, READ_ONLY_PROLOGUE + sql)
    assert res.status == pq.ExecStatus.TUPLES_OK
    assert res.ntuples == 1 and res.get_value(0, 0) == b"1"


def test_why_multi_statement_reads_are_refused_commit_escapes_the_prologue(conn):
    """Documents the attack the single-statement / txn-control rules close: a
    COMMIT ends the read-only implicit transaction and a following write runs.
    The broker never sends this ungated (is_plain_read refuses it)."""
    escape = f"SELECT 1; COMMIT; INSERT INTO {_TABLE} VALUES (9, 'escaped')"
    assert not is_plain_read(escape)
    _simple_query(conn, READ_ONLY_PROLOGUE + escape)
    assert (9, "escaped") in _rows(conn)
