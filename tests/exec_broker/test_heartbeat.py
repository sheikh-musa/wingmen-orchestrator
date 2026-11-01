"""hc#95 exec-broker dead-man heartbeat writer (spec bus #89814 §5.2)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker import heartbeat  # noqa: E402
from exec_broker.heartbeat import HeartbeatWriter, is_stale, may_take, takeover_stamp, ttl_for  # noqa: E402

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
            ref = params["ref"]
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


def _ok_probe(sock_path):
    return True, 3, None


def _writer(cur, refs=(_QA, _G), interval=60, probe=_ok_probe):
    conn = FakeConn(cur)
    return HeartbeatWriter(lambda: conn, refs, host="gzb", pid=4242, boot_id="b-1",
                           interval_seconds=interval, sock_path="/nonexistent.sock", probe=probe), conn


def test_take_sql_is_a_cas_that_never_steals_a_fresh_foreign_holder():
    sql = heartbeat._TAKE_SQL
    assert "ON CONFLICT (project_ref) DO UPDATE" in sql
    assert "WHERE exec_broker_heartbeat.holder_host = EXCLUDED.holder_host" in sql
    assert "make_interval(secs => exec_broker_heartbeat.ttl_seconds) < now()" in sql
    assert "RETURNING boot_id" in sql


def test_renew_only_touches_this_boots_row_and_uses_the_db_clock():
    sql = heartbeat._RENEW_SQL
    assert "beat_at = now()" in sql and "WHERE project_ref = %(ref)s AND boot_id = %(boot)s" in sql
    assert "loop_checked_at = now()" in sql and "loop_ok = %(loop_ok)s" in sql


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
    renews = [p for s, p in cur.calls if s.startswith("UPDATE")]
    assert [(p["ref"], p["boot"]) for p in renews] == [(_G, "b-1"), (_QA, "b-1")]
    assert all(p["loop_ok"] is True and p["latency"] == 3 and p["error"] is None for p in renews)
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


# --- listener self-probe (cc-fleet-health bus #89888) ------------------------

import os  # noqa: E402
import socket  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from exec_broker.heartbeat import probe_listener  # noqa: E402


def _short_sock_path():
    return os.path.join(tempfile.mkdtemp(prefix="hb-"), "s.sock")


def test_probe_failure_still_writes_the_beat_with_loop_ok_false():
    cur = FakeCursor()
    w, _ = _writer(cur, probe=lambda p: (False, 2000, "timeout after 2s (accept loop not responding)"))
    assert w.beat_once() == {_G: "beat", _QA: "beat"}
    renews = [p for s, p in cur.calls if s.startswith("UPDATE")]
    assert len(renews) == 2
    assert all(p["loop_ok"] is False and p["error"].startswith("timeout") for p in renews)


def test_a_probe_that_raises_never_stops_the_beat():
    def _boom(p):
        raise RuntimeError("bug")
    cur = FakeCursor()
    w, _ = _writer(cur, probe=_boom)
    assert w.beat_once() == {_G: "beat", _QA: "beat"}
    assert all(p["loop_ok"] is False for s, p in cur.calls if s.startswith("UPDATE"))


def test_unconfigured_probe_target_is_loop_ok_false():
    assert probe_listener(None) == (False, None, "probe not configured")


def test_probe_against_a_wedged_accept_loop_times_out_even_though_connect_succeeds():
    """listen() but never accept(): the kernel completes connect() into the
    backlog, so a bare connect would look healthy -- the probe must not."""
    path = _short_sock_path()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(5)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:   # bare connect succeeds
            c.connect(path)
        ok, latency, err = probe_listener(path, timeout=0.3)
        assert ok is False and "timeout" in err and latency >= 250
    finally:
        srv.close()


def test_wedged_loop_end_to_end_beat_written_with_loop_ok_false():
    path = _short_sock_path()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(5)
    try:
        cur = FakeCursor()
        conn = FakeConn(cur)
        w = HeartbeatWriter(lambda: conn, (_QA,), host="gzb", pid=1, boot_id="b-1",
                            interval_seconds=60, sock_path=path,
                            probe=lambda p: probe_listener(p, timeout=0.3))
        assert w.beat_once() == {_QA: "beat"}
        (renew_params,) = [p for s, p in cur.calls if s.startswith("UPDATE")]
        assert renew_params["loop_ok"] is False and "timeout" in renew_params["error"]
    finally:
        srv.close()


def test_probe_rejects_a_listener_that_answers_garbage_or_closes():
    for reply in (b"", b"not json\n", b'{"status": "ok", "rows": []}\n'):
        path = _short_sock_path()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)

        def _serve():
            c, _ = srv.accept()
            c.recv(16)
            if reply:
                c.sendall(reply)
            c.close()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        try:
            ok, _, err = probe_listener(path, timeout=2.0)
            assert ok is False and err
        finally:
            t.join(2)
            srv.close()


def _real_server(allowed_uids, peer_uid):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_server import FakeDb  # the broker's own fake DB (records every cursor call)
    from exec_broker.server import BrokerServer

    path = _short_sock_path()
    db = FakeDb()
    server = BrokerServer(sock_path=path, allowed_uids=allowed_uids, db_conn_factory=db,
                          token="t", mgmt_api_fn=lambda *a, **k: [], peer_uid_fn=lambda c: peer_uid)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    deadline = time.monotonic() + 2
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.01)
    return path, db


@pytest.mark.parametrize("allowed", [True, False])
def test_probe_against_the_real_broker_is_loop_ok_and_leaves_no_audit_row(allowed, caplog):
    uid = os.getuid()
    path, db = _real_server(frozenset({uid}) if allowed else frozenset({uid + 1}), uid)
    deadline = time.monotonic() + 2
    ok = False
    while time.monotonic() < deadline and not ok:
        ok, latency, err = probe_listener(path)
        if not ok:
            time.sleep(0.02)
    assert ok is True and err is None
    assert db.cursor_obj.calls == [] and db.vault_rows == {}       # no DB work, no log_started
    assert db.conn.commits == 0
    assert not [r for r in caplog.records if "ACL denied" in r.getMessage()]   # no per-tick ACL noise


def test_non_blank_request_from_an_unlisted_uid_is_still_acl_denied():
    """The blank-line early answer is not a bypass: any real request still hits the ACL."""
    uid = os.getuid()
    path, db = _real_server(frozenset({uid + 1}), uid)
    import json as _json
    deadline = time.monotonic() + 2
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(2)
        c.connect(path)
        c.sendall(b'{"project_ref": "x", "guarded_sql": "select 1", "reason": "", "gate_id": null, '
                  b'"caller": "c", "exec_ledger_id": "e"}\n')
        resp = _json.loads(c.makefile("rb").readline())
    assert resp["error"]["code"] == "ACL_DENIED"
    assert db.cursor_obj.calls == []



# --- cc-fleet-health review fixes ------------------------------------------

def test_same_host_second_boot_take_is_stamped_with_the_first_boot_id():
    first = {"holder_host": "gzbai", "boot_id": "boot-1", "taken_over_from": None, "takeover_reason": None}
    assert takeover_stamp(first, "gzbai", "boot-2") == ("boot-1", "same_host_new_boot")
    assert takeover_stamp(first, "other", "boot-2") == ("boot-1", "host_change")
    assert takeover_stamp(first, "gzbai", "boot-1") == (None, None)        # same boot re-take: unchanged
    assert takeover_stamp(None, "gzbai", "boot-1") == (None, None)


def test_take_sql_stamps_on_any_boot_id_change():
    sql = heartbeat._TAKE_SQL
    assert "CASE WHEN exec_broker_heartbeat.boot_id <> EXCLUDED.boot_id" in sql
    assert "THEN exec_broker_heartbeat.boot_id::text" in sql
    assert "'same_host_new_boot'" in sql and "'host_change'" in sql
    assert "%(reason)s" not in sql


def test_oversized_all_whitespace_line_is_too_large_not_the_probe_answer():
    import json as _json
    from exec_broker import server as srv
    uid = os.getuid()
    path, db = _real_server(frozenset({uid}), uid)
    deadline = time.monotonic() + 2
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(5)
        c.connect(path)
        c.sendall(b" " * (srv._MAX_REQUEST_BYTES + 10) + b"\n")
        resp = _json.loads(c.makefile("rb").readline())
    assert resp["error"]["message"] == "request too large"
    assert db.cursor_obj.calls == []
