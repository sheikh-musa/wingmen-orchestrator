"""Read-only DB layer (CAI-RESP-264 condition 2).

Reads via CONSOLE_DB_URL (a SELECT-only role DSN). For local dev it may fall
back to DATABASE_URL, but the code must NEVER read the Supabase service key and
must NEVER attempt a write.
"""
import os

import pytest

from nervous_system.console import db


def test_dsn_prefers_console_db_url(monkeypatch):
    monkeypatch.setenv("CONSOLE_DB_URL", "postgresql://ro@h/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://rw@h/db")
    assert db.resolve_dsn() == "postgresql://ro@h/db"


def test_dsn_falls_back_to_database_url(monkeypatch):
    monkeypatch.delenv("CONSOLE_DB_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://rw@h/db")
    assert db.resolve_dsn() == "postgresql://rw@h/db"


def test_dsn_none_when_unset(monkeypatch):
    monkeypatch.delenv("CONSOLE_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.resolve_dsn() is None


def test_never_reads_service_key(monkeypatch):
    """The module must not touch SUPABASE_SERVICE_KEY at all."""
    sentinel = "SHOULD-NEVER-BE-READ"
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", sentinel)
    monkeypatch.setenv("CONSOLE_DB_URL", "postgresql://ro@h/db")
    # resolve_dsn must not return the service key.
    assert sentinel not in (db.resolve_dsn() or "")


def test_messages_query_is_select_only():
    """The generated messages SQL must be a pure SELECT (no writes)."""
    sql, params = db.build_messages_query(limit=10, thread=None, agent=None)
    lowered = sql.strip().lower()
    assert lowered.startswith("select")
    for bad in ("insert", "update", "delete", "drop", "alter", "truncate"):
        assert bad not in lowered


def test_lanes_query_is_select_only():
    sql, params = db.build_lanes_query()
    lowered = sql.strip().lower()
    assert lowered.startswith("select")
    for bad in ("insert", "update", "delete", "drop", "alter", "truncate"):
        assert bad not in lowered


def test_messages_query_limit_clamped():
    # An absurd limit is clamped to MAX_LIMIT (DoS / oversized-read guard).
    sql, params = db.build_messages_query(limit=10_000, thread=None, agent=None)
    assert params[-1] <= db.MAX_LIMIT


def test_messages_query_filters_are_parameterized():
    sql, params = db.build_messages_query(limit=5, thread="t-1", agent="cc-cai")
    # Filters appear as bound params, not string-interpolated.
    assert "t-1" in params
    assert "cc-cai" in params
    assert "t-1" not in sql
    assert "cc-cai" not in sql
