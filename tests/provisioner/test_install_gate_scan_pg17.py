"""PG17 harness for the step-1 gate scan (bus #80290): fetch_gate_rows must
filter by the artifact's sha12 IN SQL, not scan the newest N bus rows —
orch-console had to re-issue a live gate (#80290) solely because #79787 had
scrolled out of the 500-row window. Proven against a real ephemeral
PostgreSQL with MORE than 500 newer non-matching rows, the exact production
condition. Skips cleanly when no PG17 binary is available (pg_dsn fixture).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]

_mod_path = ORCH_DIR / "scripts" / "install_ceayj_provisioner.py"
_ispec = importlib.util.spec_from_file_location("install_ceayj_provisioner", _mod_path)
inst = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(inst)

_SHA12 = "6ec5aff367f5"

_CREATE_AGENT_MESSAGES = """
DROP TABLE IF EXISTS agent_messages;
CREATE TABLE agent_messages (
  id           BIGINT PRIMARY KEY,
  from_agent   TEXT NOT NULL,
  to_agent     TEXT,
  message_type TEXT NOT NULL,
  subject      TEXT NOT NULL,
  body         TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_at      TIMESTAMPTZ,
  priority     TEXT
)
"""


def test_gate_survives_more_than_500_newer_rows(pg_dsn):
    """The exact live failure: 600 newer bus rows pushed the gate row out of
    a newest-500 scan. The sha12 must filter IN SQL so non-matching chatter
    can never displace the gate, regardless of volume."""
    import psycopg

    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(_CREATE_AGENT_MESSAGES)
        # the gate row: OLDEST of the lot (everything below is newer)
        conn.execute(
            "INSERT INTO agent_messages (id, from_agent, message_type, subject, body, created_at)"
            " VALUES (79787, 'orch-console', 'decision', %s, %s, now() - interval '2 hours')",
            (f"GATE: apply migration draft sha12 {_SHA12} on the substrate", "body names " + _SHA12))
        # 600 newer rows that never mention the sha12
        from psycopg import sql as psql_sql
        rows = psql_sql.SQL(", ").join(
            psql_sql.SQL("({}, 'paperclip', 'update', 'noise subject {}', 'noise body', now() - interval '1 minute')")
            .format(i, i) for i in range(600))
        conn.execute(psql_sql.SQL("INSERT INTO agent_messages (id, from_agent, message_type, subject, body, created_at) VALUES ") + rows)

        try:
            got = inst.fetch_gate_rows(pg_dsn, _SHA12)
            assert any(r["id"] == 79787 for r in got), (
                f"gate row 79787 lost among newer chatter: got ids "
                f"{[r['id'] for r in got][:5]}... ({len(got)} rows)")
        finally:
            conn.execute("DROP TABLE IF EXISTS agent_messages")


def test_fetch_gate_rows_sql_filters_by_sha12_not_newest_n():
    """SQL-shape pin (runs without PG17): the pushed-down filter must appear
    in the query text with the sha12 as a bound pattern on BOTH subject and
    body, inside the age window — a regression to a bare newest-N scan fails
    here even where no PostgreSQL binary exists."""
    captured = {}

    class _Cur:
        def execute(self, query, params=None):
            captured["query"] = query
            captured["params"] = params
            return self

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import unittest.mock as mock

    with mock.patch.object(inst.psycopg, "connect", return_value=_Conn()):
        inst.fetch_gate_rows("dsn-placeholder", _SHA12)

    q = captured["query"].upper()
    assert "LIKE" in q and "SUBJECT" in q and "BODY" in q, (
        f"no sha12 LIKE filter in SQL: {captured['query']!r}")
    pattern = f"%{_SHA12}%"
    assert captured["params"] is not None and pattern in captured["params"], (
        f"sha12 pattern {pattern!r} not bound: {captured['params']!r}")
    assert q.count("LIKE") >= 2, "both subject and body must filter"
