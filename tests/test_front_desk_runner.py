"""front_desk_runner.py integration tests (bus #63383): DB-stubbed, no real
DB/Telegram, same fake-cursor pattern as test_bug_report_channel_poll.py.

Covers: the ANSWER path (live-list query, both columns stamped together, one
tg_out send), the ACK path (ack-only column stamped, handled_at stays NULL so
Nazim still owns it), irsyad exclusion inside the ANSWER path, the kill switch
(item 6 — disabled front desk touches NOTHING, so ingest/Nazim behavior is
byte-for-byte unchanged), and fail-loud-without-stamp on a send error (at-
least-once retry, same shape as responder_runner.py)."""
from __future__ import annotations

import pytest

from nervous_system import front_desk_runner as fdr


# ── Fake DB ────────────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM operator_messages" in s and "front_desk_acked_at IS NULL" in s and "SELECT" in s:
            self._result = [
                (m["id"], m.get("chat_id"), m["text"], m.get("cos_triage"), m.get("created_at"))
                for m in self.db.messages
                if m.get("handled_at") is None and m.get("front_desk_acked_at") is None
            ]
        elif "FROM operator_asks" in s:
            self._result = [
                (r["id"], r["triage_summary"], r.get("delegated_to"),
                 r.get("waiting_on_operator", False), r.get("chase_by"), r.get("created_at"))
                for r in self.db.asks
            ]
        elif "SELECT set_config" in s:
            self._result = []
        elif "UPDATE operator_messages SET front_desk_acked_at=now(), handled_at=now()" in s:
            mid = params[0]
            for m in self.db.messages:
                if m["id"] == mid:
                    m["front_desk_acked_at"] = "now"
                    m["handled_at"] = "now"
            self._result = []
        elif "UPDATE operator_messages SET front_desk_acked_at=now()" in s:
            mid = params[0]
            for m in self.db.messages:
                if m["id"] == mid:
                    m["front_desk_acked_at"] = "now"
            self._result = []
        elif "INSERT INTO front_desk_latency" in s:
            self.db.latency_rows.append(params)
            self._result = []
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    @property
    def description(self):
        if self._result and len(self._result[0]) == 5:
            return [("id",), ("chat_id",), ("text",), ("cos_triage",), ("created_at",)]
        return [("id",), ("triage_summary",), ("delegated_to",), ("waiting_on_operator",),
                ("chase_by",), ("created_at",)]


class _FakeConn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        self.db.commits += 1


class _FakeDB:
    def __init__(self, messages, asks=None):
        self.messages = messages
        self.asks = asks or []
        self.latency_rows = []
        self.commits = 0


DIRECT_ANSWER_FLEET = {"category": "direct-answer", "domain": "fleet"}


@pytest.fixture
def wired(monkeypatch):
    sent = []

    def install(db):
        monkeypatch.setattr(fdr, "_dsn", lambda: "postgresql://fake")
        monkeypatch.setattr(fdr.psycopg, "connect", lambda *a, **k: _FakeConn(db))

        def _fake_enqueue(channel, text=None, **k):
            sent.append((channel, text, k))
            return 1
        monkeypatch.setattr(fdr.tg_out, "enqueue", _fake_enqueue)
        return sent
    return install


# ── drain_once: ANSWER path ─────────────────────────────────────────────────

def test_drain_once_answers_live_list_and_stamps_both_columns(wired):
    db = _FakeDB(
        messages=[{"id": 1, "chat_id": 999, "text": "live list", "cos_triage": DIRECT_ANSWER_FLEET,
                   "created_at": "t0"}],
        asks=[{"id": 10, "triage_summary": "ship the thing", "delegated_to": "cc-cosem",
               "waiting_on_operator": True}],
    )
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 1
    assert db.messages[0]["handled_at"] == "now"
    assert db.messages[0]["front_desk_acked_at"] == "now"
    assert len(sent) == 1
    channel, text, kwargs = sent[0]
    assert channel == fdr.CHANNEL_KEY
    assert "ship the thing" in text
    assert kwargs["reply_to"] == 1
    assert db.latency_rows[0][1] == "answer"


def test_drain_once_excludes_irsyad_rows_from_answer(wired):
    db = _FakeDB(
        messages=[{"id": 1, "chat_id": 999, "text": "live list", "cos_triage": DIRECT_ANSWER_FLEET,
                   "created_at": "t0"}],
        asks=[
            {"id": 10, "triage_summary": "ship the thing", "delegated_to": "cc-cosem", "waiting_on_operator": False},
            {"id": 11, "triage_summary": "irsyad receipt numbering", "delegated_to": "cc-irsyad-coord",
             "waiting_on_operator": True},
        ],
    )
    sent = wired(db)
    fdr.drain_once(_FakeConn(db))
    _, text, _ = sent[0]
    assert "ship the thing" in text
    assert "irsyad" not in text.lower()
    assert "#11" not in text


# ── drain_once: ACK path ────────────────────────────────────────────────────

def test_drain_once_acks_non_whitelisted_message_without_marking_handled(wired):
    db = _FakeDB(messages=[
        {"id": 2, "chat_id": 999, "text": "can you ship the storefront PR today",
         "cos_triage": DIRECT_ANSWER_FLEET, "created_at": "t0"},
    ])
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 1
    assert db.messages[0]["front_desk_acked_at"] == "now"
    assert db.messages[0].get("handled_at") is None
    _, text, _ = sent[0]
    assert text == fdr.front_desk.ACK_TEMPLATE
    assert db.latency_rows[0][1] == "ack"


def test_drain_once_acks_live_list_text_when_triage_disagrees(wired):
    # Double-guard in action: the words match but triage says governance-fork.
    db = _FakeDB(messages=[
        {"id": 3, "chat_id": 999, "text": "live list", "cos_triage": {"category": "governance-fork"},
         "created_at": "t0"},
    ])
    wired(db)
    fdr.drain_once(_FakeConn(db))
    assert db.messages[0].get("handled_at") is None
    assert db.messages[0]["front_desk_acked_at"] == "now"


def test_single_message_never_gets_both_ack_and_answer_stamp_shape(wired):
    # By construction the two UPDATE branches are mutually exclusive in
    # drain_once — assert the invariant directly for both kinds in one pass.
    db = _FakeDB(messages=[
        {"id": 1, "chat_id": 1, "text": "live list", "cos_triage": DIRECT_ANSWER_FLEET, "created_at": "t0"},
        {"id": 2, "chat_id": 1, "text": "random question", "cos_triage": DIRECT_ANSWER_FLEET, "created_at": "t0"},
    ], asks=[{"id": 1, "triage_summary": "x", "waiting_on_operator": False}])
    wired(db)
    fdr.drain_once(_FakeConn(db))
    answered = db.messages[0]
    acked_only = db.messages[1]
    assert answered["handled_at"] and answered["front_desk_acked_at"]
    assert acked_only["front_desk_acked_at"] and acked_only.get("handled_at") is None


# ── Skips already-touched rows ──────────────────────────────────────────────

def test_drain_once_skips_already_acked_row(wired):
    db = _FakeDB(messages=[
        {"id": 4, "chat_id": 1, "text": "hello", "cos_triage": None,
         "created_at": "t0", "front_desk_acked_at": "earlier"},
    ])
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 0
    assert sent == []


def test_drain_once_skips_already_handled_row(wired):
    db = _FakeDB(messages=[
        {"id": 5, "chat_id": 1, "text": "hello", "cos_triage": None,
         "created_at": "t0", "handled_at": "earlier"},
    ])
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 0
    assert sent == []


# ── Error handling ───────────────────────────────────────────────────────────

def test_drain_once_leaves_row_unstamped_on_send_failure(wired, monkeypatch):
    db = _FakeDB(messages=[
        {"id": 6, "chat_id": 1, "text": "hello", "cos_triage": None, "created_at": "t0"},
    ])
    wired(db)

    def _boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(fdr.tg_out, "enqueue", _boom)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 0
    assert db.messages[0].get("front_desk_acked_at") is None
    assert db.messages[0].get("handled_at") is None


# ── Kill switch (item 6) ─────────────────────────────────────────────────────

def test_main_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("FRONT_DESK_ENABLED", raising=False)
    connect_calls = []
    monkeypatch.setattr(fdr.psycopg, "connect", lambda *a, **k: connect_calls.append(1))
    assert fdr.main() == 0
    assert connect_calls == []


def test_main_noop_when_explicitly_false(monkeypatch):
    monkeypatch.setenv("FRONT_DESK_ENABLED", "0")
    connect_calls = []
    monkeypatch.setattr(fdr.psycopg, "connect", lambda *a, **k: connect_calls.append(1))
    assert fdr.main() == 0
    assert connect_calls == []


def test_enabled_accepts_true_variants(monkeypatch):
    for val in ("1", "true", "True", "yes"):
        monkeypatch.setenv("FRONT_DESK_ENABLED", val)
        assert fdr.enabled() is True
    for val in ("0", "false", "", "no"):
        monkeypatch.setenv("FRONT_DESK_ENABLED", val)
        assert fdr.enabled() is False
