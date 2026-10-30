"""Tests for scripts.apply_orch_lease — the direct-psycopg applier for
migrations/016_orch_lease.sql (decision-962: never `supabase db push`).

All psycopg access is mocked at the psycopg.connect boundary — these tests
NEVER connect to a real database. DSN values are clearly-fake placeholders.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import apply_orch_lease  # noqa: E402

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "016_orch_lease.sql"
SELECT_SQL = "SELECT lease_key, holder, holder_host, acquired_at FROM orch_lease"


def _mock_connect(monkeypatch):
    """Patch psycopg.connect with a MagicMock whose connection/cursor behave as
    context managers, matching how apply_orch_lease.main() drives them
    (`with psycopg.connect(dsn) as conn, conn.cursor() as cur:`). Returns the
    (fake_connect, conn, cur) triple so tests can assert on the call sequence."""
    conn = MagicMock(name="conn")
    # `with psycopg.connect(dsn) as conn` -> the connection is its own CM.
    conn.__enter__.return_value = conn
    # `conn.cursor() as cur` -> cursor() returns a CM whose __enter__ is the cursor.
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [
        ("orch-hub", "cc-orchestrator", None, "2026-07-04T00:00:00Z"),
    ]
    fake_connect = MagicMock(name="connect", return_value=conn)
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    return fake_connect, conn, cur


def test_dsn_prefers_database_url(monkeypatch):
    """When both DATABASE_URL and SUPABASE_DB_URL are set, DATABASE_URL wins."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-primary/db")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake-fallback/db")
    fake_connect, _, _ = _mock_connect(monkeypatch)

    assert apply_orch_lease.main() == 0
    fake_connect.assert_called_once_with("postgresql://fake-primary/db")


def test_dsn_falls_back_to_supabase_db_url(monkeypatch):
    """With DATABASE_URL unset/empty, the DSN falls back to SUPABASE_DB_URL."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake-fallback/db")
    fake_connect, _, _ = _mock_connect(monkeypatch)

    assert apply_orch_lease.main() == 0
    fake_connect.assert_called_once_with("postgresql://fake-fallback/db")


def test_dsn_falls_back_when_database_url_is_empty(monkeypatch):
    """An empty-string DATABASE_URL is falsy, so SUPABASE_DB_URL still wins."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake-fallback/db")
    fake_connect, _, _ = _mock_connect(monkeypatch)

    assert apply_orch_lease.main() == 0
    fake_connect.assert_called_once_with("postgresql://fake-fallback/db")


def test_executes_real_migration_then_select(monkeypatch):
    """main() executes the exact on-disk migration SQL, then the orch_lease SELECT."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-primary/db")
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    _, _, cur = _mock_connect(monkeypatch)

    expected_sql = MIGRATION_PATH.read_text()
    assert apply_orch_lease.main() == 0

    executed = [call.args[0] for call in cur.execute.call_args_list]
    assert executed == [expected_sql, SELECT_SQL]


def test_commits_and_returns_zero(monkeypatch):
    """The connection is committed and main() returns 0."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-primary/db")
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    _, conn, cur = _mock_connect(monkeypatch)

    assert apply_orch_lease.main() == 0
    conn.commit.assert_called_once()
    cur.fetchall.assert_called_once()
