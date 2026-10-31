"""Regression tests for tg_out.deliver_one — no text re-delivery on file failure.

Guards the 2026-08-07 bug (operator-flagged op#11218): a tg_out row carrying
text + a file_path the daemon host can't open re-sent the TEXT on every retry
(up to MAX_ATTEMPTS), so one logical message reached the operator 11x. The two
'dead' rows (Mac-local image paths, unreadable on the VPS daemon) each delivered
their text 5x before dying; deliver_one now NULLs the text the instant it is
delivered, so a retry (for the file) can never re-send it, and a missing file is
non-retriable.
"""
from unittest.mock import patch

from nervous_system import tg_out


class _FakeCursor:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.store["sql"].append((sql, params))

    def fetchone(self):
        # only reached by the stale-ack guard SELECT; pretend inbound is unhandled
        return (1,)


class _FakeConn:
    def __init__(self):
        self.store = {"sql": []}

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        pass


def _resolve_stub(conn, channel_key):
    return ("TOKEN", 999, "operator-orch")


def _sql_text(conn):
    return " || ".join(s for s, _ in conn.store["sql"])


def _all_params(conn):
    return [p for _, p in conn.store["sql"] if p]


def test_file_failure_does_not_resend_text():
    calls = {"text": 0, "file": 0}

    def fake_text(tok, chat, txt):
        calls["text"] += 1

    def fake_file(tok, chat, path):
        calls["file"] += 1
        raise FileNotFoundError(path)

    with patch.object(tg_out, "_resolve", _resolve_stub), \
         patch.object(tg_out, "_send_text", fake_text), \
         patch.object(tg_out, "_send_file", fake_file):
        conn = _FakeConn()
        row = (1187, "operator-orch", 12345, "HK hero text", "/missing/on/vps.png", 0)
        tg_out.deliver_one(conn, row)

        sql = _sql_text(conn)
        assert calls["text"] == 1, "text must deliver exactly once"
        assert "UPDATE tg_out SET text=NULL" in sql, "text NULLed right after delivery"
        assert "status='dead'" in sql, "missing file is non-retriable -> dead"

        # simulate the retry: drain re-fetches the row, text now NULL
        conn2 = _FakeConn()
        row_retry = (1187, "operator-orch", 12345, None, "/missing/on/vps.png", 1)
        tg_out.deliver_one(conn2, row_retry)
        assert calls["text"] == 1, "retry must NOT re-send already-delivered text"
        assert calls["file"] == 2, "retry re-attempts only the file"


def test_text_only_row_delivers_once_and_marks_sent():
    calls = {"text": 0}

    def fake_text(tok, chat, txt):
        calls["text"] += 1

    with patch.object(tg_out, "_resolve", _resolve_stub), \
         patch.object(tg_out, "_send_text", fake_text):
        conn = _FakeConn()
        tg_out.deliver_one(conn, (1192, "operator-orch", 12345, "clean text", None, 0))

    sql = _sql_text(conn)
    assert calls["text"] == 1
    assert "status='sent'" in sql
    assert "INSERT INTO operator_messages" in sql


def test_transient_file_error_retries_file_not_text():
    calls = {"text": 0, "file": 0}

    def fake_text(tok, chat, txt):
        calls["text"] += 1

    def fake_file(tok, chat, path):
        calls["file"] += 1
        raise RuntimeError("network timeout")

    with patch.object(tg_out, "_resolve", _resolve_stub), \
         patch.object(tg_out, "_send_text", fake_text), \
         patch.object(tg_out, "_send_file", fake_file):
        conn = _FakeConn()
        tg_out.deliver_one(conn, (1300, "operator-orch", 12345, "t", "/some.png", 0))

    sql = _sql_text(conn)
    assert calls["text"] == 1
    assert "UPDATE tg_out SET text=NULL" in sql
    # generic except -> retriable 'failed' (attempts 1 < MAX_ATTEMPTS)
    assert any("failed" in (p or ()) for p in _all_params(conn)), "transient file error stays retriable"
