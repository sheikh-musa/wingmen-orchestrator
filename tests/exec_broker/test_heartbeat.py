"""hc#95 exec-broker dead-man heartbeat writer (spec bus #89814 §5.2)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker import heartbeat  # noqa: E402
from exec_broker.heartbeat import HeartbeatWriter, is_stale, may_take, ttl_for  # noqa: E402

_NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
_QA = "oovskwogvefnznfowjky"
_G = "goumlynecruxrlmzlntp"


def test_missing_row_is_stale():
    assert is_stale(None, _NOW) is True


def test_stale_iff_now_past_beat_plus_ttl():
    row = {"beat_at": _NOW - timedelta(seconds=180), "ttl_seconds": 180}
    assert is_stale(row, _NOW) is False          # exactly at the boundary: fresh
    assert is_stale(row, _NOW + timedelta(seconds=1)) is True


def test_ttl_is_three_intervals():
    assert ttl_for(60) == 180


def test_may_take_mirrors_the_lease_cas():
    fresh_foreign = {"holder_host": "other", "beat_at": _NOW, "ttl_seconds": 180}
    assert may_take(None, "me", _NOW)
    assert may_take({**fresh_foreign, "holder_host": "me"}, "me", _NOW)    # same body restarted
    assert not may_take(fresh_foreign, "me", _NOW)                         # never steal a live one
    assert may_take({**fresh_foreign, "beat_at": _NOW - timedelta(hours=1)}, "me", _NOW)  # expired


class FakeCursor:
    """Answers TAKE/RENEW from a scripted per-ref result."""

    def __init__(self, take_ok=None, renew_ok=None, raise_for=()):
        self.take_ok = take_ok or {}
        self.renew_ok = renew_ok or {}
        self.raise_for = set(raise_for)
        self.calls = []
        self._next = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("INSERT INTO exec_broker_heartbeat"):
            ref = params["ref"]
            if ref in self.raise_for:
                raise RuntimeError("db down")
            self._next = {"boot_id": params["boot"]} if self.take_ok.get(ref, True) else None
        elif sql.startswith("UPDATE exec_broker_heartbeat"):
            ref = params[0]
            if ref in self.raise_for:
                raise RuntimeError("db down")
            self._next = {"beat_seq": 1} if self.renew_ok.get(ref, True) else None

    def fetchone(self):
        return self._next

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cur):
        self.cur = cur
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _writer(cur, refs=(_QA, _G), interval=60):
    conn = FakeConn(cur)
    return HeartbeatWriter(lambda: conn, refs, host="gzb", pid=4242, boot_id="b-1",
                           interval_seconds=interval), conn


def test_take_sql_is_a_cas_that_never_steals_a_fresh_foreign_holder():
    sql = heartbeat._TAKE_SQL
    assert "ON CONFLICT (project_ref) DO UPDATE" in sql
    assert "WHERE exec_broker_heartbeat.holder_host = EXCLUDED.holder_host" in sql
    assert "make_interval(secs => exec_broker_heartbeat.ttl_seconds) < now()" in sql
    assert "RETURNING boot_id" in sql


def test_renew_only_touches_this_boots_row_and_uses_the_db_clock():
    sql = heartbeat._RENEW_SQL
    assert "beat_at = now()" in sql and "WHERE project_ref = %s AND boot_id = %s" in sql


def test_take_all_takes_every_gated_ref_in_one_committed_transaction():
    cur = FakeCursor()
    w, conn = _writer(cur)
    assert w.take_all() == []
    takes = [p for s, p in cur.calls if s.startswith("INSERT")]
    assert [p["ref"] for p in takes] == sorted([_QA, _G])
    assert all(p["host"] == "gzb" and p["pid"] == 4242 and p["boot"] == "b-1"
               and p["interval"] == 60 and p["ttl"] == 180 for p in takes)
    assert conn.commits == 1 and conn.rollbacks == 0


def test_take_all_reports_and_rolls_back_when_a_ref_has_a_live_foreign_holder():
    cur = FakeCursor(take_ok={_G: False})
    w, conn = _writer(cur)
    assert w.take_all() == [_G]
    assert conn.commits == 0 and conn.rollbacks == 1


def test_take_all_propagates_a_db_error():
    w, _ = _writer(FakeCursor(raise_for={_QA}))
    with pytest.raises(RuntimeError):
        w.take_all()


def test_beat_once_renews_every_ref():
    cur = FakeCursor()
    w, conn = _writer(cur)
    assert w.beat_once() == {_G: "beat", _QA: "beat"}
    assert [p for s, p in cur.calls if s.startswith("UPDATE")] == [(_G, "b-1"), (_QA, "b-1")]
    assert conn.commits == 2


def test_beat_once_retakes_only_via_cas_and_never_steals():
    w, _ = _writer(FakeCursor(renew_ok={_G: False, _QA: False}, take_ok={_G: False, _QA: True}))
    assert w.beat_once() == {_G: "lost", _QA: "retaken"}


def test_beat_once_isolates_a_failing_ref_and_never_raises():
    w, _ = _writer(FakeCursor(raise_for={_G}))
    assert w.beat_once() == {_G: "error", _QA: "beat"}


@pytest.mark.parametrize("interval", [0, 9, 901])
def test_interval_bounds(interval):
    with pytest.raises(ValueError):
        _writer(FakeCursor(), interval=interval)


def test_needs_at_least_one_ref():
    with pytest.raises(ValueError):
        _writer(FakeCursor(), refs=())


def test_thread_beats_on_its_interval_and_stops(monkeypatch):
    w, _ = _writer(FakeCursor())
    beats = []
    monkeypatch.setattr(w, "beat_once", lambda: beats.append(1) or {})
    w.interval_seconds = 0.01  # test-only: fast tick (the ctor still enforces real bounds)
    w.start()
    deadline = datetime.now() + timedelta(seconds=2)
    while len(beats) < 3 and datetime.now() < deadline:
        pass
    w.stop()
    assert len(beats) >= 3
