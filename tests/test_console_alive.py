"""console_alive() -- fresh orch-console agent_status heartbeat check (audit-20261006
item 4, bus#54648). Shared by priority_sla_watchdog.py, sre_liveness_watchdog.py, and
wake_backstop_sweep.py so none of them pages orch-console into a void.

FAIL-SAFE DIRECTION under test: any lookup failure (no DSN, DB error, no matching row)
returns False (treat as NOT alive) -- "fails toward paging," the same direction
priority_sla_watchdog's pre-existing _internal_path_alive already took ("cannot tell =>
page"). A wrongly-triggered hub redirect costs one extra hop; a wrongly-skipped one
repeats the void-page bug this closes.
"""
from datetime import datetime, timedelta, timezone

from scripts.lib import console_alive as ca


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _connect_with(rows):
    def _connect(dsn, **k):
        return _FakeConn(rows)
    return _connect


def test_fresh_heartbeat_is_alive(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(ca.psycopg, "connect", _connect_with([(now - timedelta(seconds=30),)]))
    assert ca.console_alive(dsn="postgres://x") is True


def test_stale_heartbeat_is_not_alive(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(ca.psycopg, "connect", _connect_with([(now - timedelta(seconds=900),)]))
    assert ca.console_alive(dsn="postgres://x") is False


def test_no_matching_row_is_not_alive(monkeypatch):
    monkeypatch.setattr(ca.psycopg, "connect", _connect_with([]))
    assert ca.console_alive(dsn="postgres://x") is False


def test_null_heartbeat_row_is_not_alive(monkeypatch):
    monkeypatch.setattr(ca.psycopg, "connect", _connect_with([(None,)]))
    assert ca.console_alive(dsn="postgres://x") is False


def test_db_error_fails_to_not_alive(monkeypatch):
    def boom(dsn, **k):
        raise RuntimeError("db unreachable")
    monkeypatch.setattr(ca.psycopg, "connect", boom)
    assert ca.console_alive(dsn="postgres://x") is False


def test_no_dsn_fails_to_not_alive(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert ca.console_alive(dsn=None) is False


def test_one_of_several_rows_fresh_is_alive(monkeypatch):
    # base-inclusive match can return >1 row (e.g. a base + a stale leftover instance row);
    # ANY fresh row is enough.
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        ca.psycopg, "connect",
        _connect_with([(now - timedelta(seconds=9000),), (now - timedelta(seconds=5),)]))
    assert ca.console_alive(dsn="postgres://x") is True


def test_custom_fresh_window_respected(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(ca.psycopg, "connect", _connect_with([(now - timedelta(seconds=120),)]))
    assert ca.console_alive(dsn="postgres://x", fresh_s=60) is False
    assert ca.console_alive(dsn="postgres://x", fresh_s=600) is True
