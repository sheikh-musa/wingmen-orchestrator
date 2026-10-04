"""test_tg_out_cant_open_guard.py — tg_out.enqueue() refuses the "I can't open
your file" framing on CLIENT channels (orch-console bus #51060, Musa
op#25437-25440). Operator/internal channels are unaffected (e.g. a lane
legitimately reporting a download failure internally).

Pure-logic / fake-cursor tests — no real DB (tg_out.py has no existing test
file at all; this is the first).
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from nervous_system import tg_out  # noqa: E402


class _FakeCursor:
    def __init__(self, audience_row):
        self.executed = []
        self._audience_row = audience_row

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._audience_row


def test_refuses_cant_open_phrasing_on_client_channel():
    cur = _FakeCursor(audience_row=("client",))
    with pytest.raises(tg_out.CantOpenFileRefusal, match="refusing to enqueue"):
        tg_out._refuse_cant_open_file_framing(cur, "cosem-exams", "sorry, I can't open your file")


def test_passes_cant_open_phrasing_on_operator_channel():
    cur = _FakeCursor(audience_row=("operator",))
    tg_out._refuse_cant_open_file_framing(cur, "operator-orch", "heads up, I can't open the file you sent")
    # must not raise -- operator channels may legitimately report this internally


def test_passes_normal_text_on_client_channel():
    cur = _FakeCursor(audience_row=("client",))
    tg_out._refuse_cant_open_file_framing(cur, "cosem-exams", "thanks, we received your file")


def test_passes_when_channel_has_no_bot_channels_row():
    cur = _FakeCursor(audience_row=None)
    tg_out._refuse_cant_open_file_framing(cur, "unknown-channel", "I can't open your file")


def test_passes_when_text_is_none():
    cur = _FakeCursor(audience_row=("client",))
    tg_out._refuse_cant_open_file_framing(cur, "cosem-exams", None)


def test_enqueue_refuses_before_insert(monkeypatch):
    class _FakeFullCursor(_FakeCursor):
        def fetchone(self):
            # enqueue() executes set_config() first (no fetchone), then the
            # guard's own SELECT audience (2nd execute) -- that fetchone call
            # returns the audience row. A 3rd execute (the INSERT ... RETURNING
            # id) must never happen when the guard refuses.
            if len(self.executed) == 2:
                return ("client",)
            raise AssertionError("INSERT must not run when the guard refuses")

    class _FakeConn:
        def __init__(self, cur):
            self._cur = cur

        def cursor(self):
            return self._cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            raise AssertionError("commit must not run when the guard refuses")

    fake_cur = _FakeFullCursor(audience_row=None)

    class _FakeCurCtx:
        def __enter__(self):
            return fake_cur

        def __exit__(self, *a):
            return False

    fake_conn = _FakeConn(_FakeCurCtx())
    monkeypatch.setattr(tg_out.psycopg, "connect", lambda *a, **k: fake_conn)

    with pytest.raises(tg_out.CantOpenFileRefusal):
        tg_out.enqueue("cosem-exams", text="I can't open your file")
