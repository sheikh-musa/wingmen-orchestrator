"""scripts/apply_orch_lease.py — the direct-psycopg applier for
migrations/016_orch_lease.sql (decision-962: never `supabase db push`).

PURE-LOGIC tests: `psycopg.connect` is monkeypatched at the module boundary, so
the connect/cursor/execute/commit sequence is exercised exactly as the real
script drives it, but no database is ever contacted (local or remote).

The migration text asserted here is read from disk, not hand-copied, so the
test cannot drift from the real migration file.
"""
import importlib.util
import os
import sys
from unittest import mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "apply_orch_lease.py")
_MIGRATION = os.path.join(_ROOT, "migrations", "016_orch_lease.sql")

# Import-time `load_dotenv()` would pull the real repo .env into this process.
# Stub it out: these tests must not depend on (or read) live credentials.
with mock.patch("dotenv.load_dotenv", lambda *a, **k: False):
    _spec = importlib.util.spec_from_file_location("apply_orch_lease", _SCRIPT)
    apply_orch_lease = importlib.util.module_from_spec(_spec)
    sys.modules["apply_orch_lease"] = apply_orch_lease
    _spec.loader.exec_module(apply_orch_lease)

SELECT_SQL = "SELECT lease_key, holder, holder_host, acquired_at FROM orch_lease"

# Clearly-fake placeholders (connect is mocked) — never real DSN values.
FAKE_DATABASE_URL = "fake-dsn-preferred-database-url"
FAKE_SUPABASE_DB_URL = "fake-dsn-fallback-supabase-db-url"


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append(sql)

    def fetchall(self):
        return [("orch-hub", "cc-orchestrator", None, "2026-10-31T00:00:00+00:00")]


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True


@pytest.fixture
def fake_db(monkeypatch):
    """Install a fake `psycopg.connect`; return (conn, captured_dsns)."""
    conn = _FakeConn()
    dsns = []

    def _fake_connect(dsn, *a, **k):
        dsns.append(dsn)
        return conn

    monkeypatch.setattr(apply_orch_lease.psycopg, "connect", _fake_connect)
    # Never inherit a DSN from the invoking shell — each test sets its own.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    return conn, dsns


def test_dsn_prefers_database_url_when_both_are_set(fake_db, monkeypatch):
    _conn, dsns = fake_db
    monkeypatch.setenv("DATABASE_URL", FAKE_DATABASE_URL)
    monkeypatch.setenv("SUPABASE_DB_URL", FAKE_SUPABASE_DB_URL)

    assert apply_orch_lease.main() == 0

    assert dsns == [FAKE_DATABASE_URL]


@pytest.mark.parametrize("how", ["unset", "empty"])
def test_dsn_falls_back_to_supabase_db_url(fake_db, monkeypatch, how):
    _conn, dsns = fake_db
    if how == "empty":
        monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("SUPABASE_DB_URL", FAKE_SUPABASE_DB_URL)

    assert apply_orch_lease.main() == 0

    assert dsns == [FAKE_SUPABASE_DB_URL]


def test_executes_the_real_migration_text_then_reads_the_lease(fake_db, monkeypatch):
    conn, _dsns = fake_db
    monkeypatch.setenv("DATABASE_URL", FAKE_DATABASE_URL)
    with open(_MIGRATION) as f:
        migration_sql = f.read()

    apply_orch_lease.main()

    assert conn.executed == [migration_sql, SELECT_SQL]


def test_commits_and_returns_zero(fake_db, monkeypatch, capsys):
    conn, _dsns = fake_db
    monkeypatch.setenv("DATABASE_URL", FAKE_DATABASE_URL)

    rc = apply_orch_lease.main()

    assert conn.committed is True
    assert rc == 0
    assert "016_orch_lease applied." in capsys.readouterr().out
