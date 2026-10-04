"""test_ingest_stage_file.py — STAGE-FILE auto-route (orch-console bus #51060,
Musa op#25437-25440): an inbound FILE on a client channel must auto-page
orch-console to stage it, idempotently, excluding irsyad channels and GIFs.

PURE-LOGIC / fake-DB tests — no real DB, no psycopg connection (mirrors
tests/test_bus_send.py's style, NOT tests/test_unified_ingest.py's ephemeral-
DB-gated style, so this coverage always runs rather than skipping without
INGEST_DSN).
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nervous_system import ingest  # noqa: E402


def _channel(audience="client", owner_lane="some-lane", key="cosem-exams"):
    row = (
        key, "SOME_TOKEN_ENV", "agent-session", "target", None, None,
        [], [], {}, "tag", None, None, audience, owner_lane,
    )
    return ingest.Channel(row)


# ── is_staged_file_eligible: the pure predicate ──────────────────────────────

def test_eligible_for_a_real_document_on_a_client_channel():
    ch = _channel(audience="client", owner_lane="cosem-exams-coord")
    msg = {"document": {"file_id": "f1", "file_unique_id": "u1"}}
    assert ingest.is_staged_file_eligible(ch, msg) is True


def test_not_eligible_without_a_document():
    ch = _channel(audience="client")
    assert ingest.is_staged_file_eligible(ch, {"text": "hello"}) is False


def test_not_eligible_for_an_animation_even_though_document_is_also_set():
    # Telegram sets `document` on animation (GIF) messages too for back-compat
    # — the same gotcha _media_content already guards against.
    ch = _channel(audience="client")
    msg = {"document": {"file_id": "f1"}, "animation": {"file_id": "f1"}}
    assert ingest.is_staged_file_eligible(ch, msg) is False


def test_not_eligible_for_a_non_client_channel():
    ch = _channel(audience="operator")
    msg = {"document": {"file_id": "f1"}}
    assert ingest.is_staged_file_eligible(ch, msg) is False


def test_not_eligible_for_irsyad_owner_lane():
    # coord's own import pipeline owns irsyad channels — excluded.
    ch = _channel(audience="client", owner_lane="irsyad-coord")
    msg = {"document": {"file_id": "f1"}}
    assert ingest.is_staged_file_eligible(ch, msg) is False


def test_eligible_for_client_channel_with_a_different_owner_lane():
    ch = _channel(audience="client", owner_lane="cosem-exams-coord")
    msg = {"document": {"file_id": "f1"}}
    assert ingest.is_staged_file_eligible(ch, msg) is True


# ── _page_stage_file_once: idempotent paging, fake DB + fake bus_send ───────

class _FakeCursor:
    def __init__(self, fetch_result=None):
        self.executed = []
        self._fetch_result = fetch_result

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetch_result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur


def test_page_stage_file_once_sends_when_no_prior_marker(monkeypatch):
    cur = _FakeCursor(fetch_result=None)
    conn = _FakeConn(cur)
    sent = {}

    def _fake_send(**kwargs):
        sent.update(kwargs)
        return (4242, "th-uuid")

    monkeypatch.setattr("scripts.bus_send.send", _fake_send)
    ch = _channel(key="cosem-exams")
    ingest._page_stage_file_once(conn, ch, upd_id=99, op_msg_id=777,
                                  local_path="/tmp/media/x.xlsx", caption="here's the roster")

    assert sent["to"] == "orch-console"
    assert sent["priority"] == "P1"
    assert sent["req"] is True
    assert "STAGE-FILE:cosem-exams:99" in sent["body"]
    assert "op#777" in sent["subject"]
    assert "/tmp/media/x.xlsx" in sent["subject"]
    assert "here's the roster" in sent["body"]
    assert "stage_client_file.py" in sent["body"]


def test_page_stage_file_once_is_idempotent_per_update(monkeypatch):
    cur = _FakeCursor(fetch_result=(1,))  # marker already found
    conn = _FakeConn(cur)

    def _boom(**kwargs):
        raise AssertionError("bus_send.send must not be called when a marker already exists")

    monkeypatch.setattr("scripts.bus_send.send", _boom)
    ch = _channel(key="cosem-exams")
    ingest._page_stage_file_once(conn, ch, upd_id=99, op_msg_id=777,
                                  local_path="/tmp/media/x.xlsx", caption="")
    assert any("STAGE-FILE:cosem-exams:99" in (p[0] if p else "") for _, p in cur.executed)


def test_page_stage_file_once_without_caption_notes_no_caption(monkeypatch):
    cur = _FakeCursor(fetch_result=None)
    conn = _FakeConn(cur)
    sent = {}
    monkeypatch.setattr("scripts.bus_send.send", lambda **kw: sent.update(kw) or (1, "t"))
    ch = _channel(key="cosem-exams")
    ingest._page_stage_file_once(conn, ch, upd_id=1, op_msg_id=2, local_path="/tmp/x.csv", caption="")
    assert "(no caption)" in sent["body"]
