"""Tests for the STEP-0 poll-health reaper (orch-console bus #89318).

ingest_poll_health is keyed (channel_key, host) and `host` is socket.gethostname(),
which on this Mac can be either "Sheikhs-Mini" or "Sheikhs-Mac-mini.local". A
rename or relaunch therefore strands a phantom row that never updates again, and
readers filtering on channel_key alone keep seeing it forever.

Live case: (coffeemedia, gzbai) still showed "16 consecutive 409 Conflict" from
2026-10-17 while the real Mini poller was healthy, and a human read that stale
row as an ONGOING duplicate-poller incident. The reaper deletes host rows for our
own channels that have been silent past the window.

What matters and is pinned here: it is SCOPED (only our channel keys, never our
own host row) and it FAILS OPEN (a reap error must never break polling).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import nervous_system.ingest as ingest  # noqa: E402


class _Chan:
    def __init__(self, key):
        self.key = key


class _Cur:
    def __init__(self, store, rows):
        self._store = store
        self._rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._store.append((sql, params))

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows=(), fail=None):
        self.statements = []
        self._rows = list(rows)
        self._fail = fail

    def cursor(self):
        if self._fail:
            raise self._fail
        return _Cur(self.statements, self._rows)


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch):
    # None = never reaped. Deliberately NOT 0.0: time.monotonic() is process-relative
    # (a fresh process reads ~0.4s), so a 0.0 sentinel would defer the first sweep by
    # an hour and the very first test below would silently no-op.
    monkeypatch.setattr(ingest, "_poll_health_last_reap", None, raising=False)


def test_reap_is_scoped_to_group_keys_and_excludes_own_host(monkeypatch):
    conn = _Conn(rows=[("coffeemedia", "gzbai", "2026-10-17 19:11:36+00")])
    group = [_Chan("coffeemedia"), _Chan("brandit-coffeemedia")]

    ingest._reap_silent_poll_health_rows(conn, group)

    assert conn.statements, "expected a DELETE"
    sql, params = conn.statements[-1]
    assert sql.lstrip().upper().startswith("DELETE FROM INGEST_POLL_HEALTH")
    keys, host, days = params
    assert keys == ["coffeemedia", "brandit-coffeemedia"], "must scope to THIS group's keys"
    assert host == ingest.socket.gethostname(), "must never delete our own host row"
    assert days == ingest.POLL_HEALTH_REAP_AFTER_DAYS


def test_reap_is_throttled_so_it_does_not_run_every_cycle(monkeypatch):
    conn1 = _Conn()
    ingest._reap_silent_poll_health_rows(conn1, [_Chan("coffeemedia")])
    assert conn1.statements, "first call should reap"

    conn2 = _Conn()
    ingest._reap_silent_poll_health_rows(conn2, [_Chan("coffeemedia")])
    assert not conn2.statements, "second call inside the window must be a no-op"


def test_reap_fails_open_and_never_breaks_polling(monkeypatch):
    conn = _Conn(fail=RuntimeError("boom"))
    # must not raise
    ingest._reap_silent_poll_health_rows(conn, [_Chan("coffeemedia")])


def test_empty_group_is_a_noop():
    conn = _Conn()
    ingest._reap_silent_poll_health_rows(conn, [])
    assert not conn.statements
