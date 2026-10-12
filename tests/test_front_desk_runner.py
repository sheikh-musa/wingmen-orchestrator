"""front_desk_runner.py integration tests (bus #63383, orch-console gate
fixes bus #63427): DB-stubbed, no real DB/Telegram, same fake-cursor pattern
as test_bug_report_channel_poll.py.

Covers: the ANSWER path (live-list query, front_desk_acked_at-only stamp,
handled_at left untouched, one tg_out send AFTER the stamp commits), the ACK
path (stamp only, NO Telegram send — ingest.py already acked), irsyad
exclusion inside the ANSWER path, the host/role guard (item 1), the kill
switch re-read every pass (item 2), the Musa-DM-only sender/chat gate
(item 3, the most severe finding), and stamp-before-send so a failed stamp
never sends and a failed send-after-stamp never re-sends (item 4)."""
from __future__ import annotations

import pytest

from nervous_system import front_desk_runner as fdr


# ── Fake DB ────────────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []
        self._mode = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM operator_messages" in s and "front_desk_acked_at IS NULL" in s and "SELECT" in s:
            self._mode = "messages"
            self._result = [
                (m["id"], m.get("chat_id"), m.get("from_user_id"), m["text"],
                 m.get("cos_triage"), m.get("created_at"))
                for m in self.db.messages
                if m.get("handled_at") is None and m.get("front_desk_acked_at") is None
            ]
        elif "FROM operator_asks" in s:
            self._mode = "asks"
            self._result = [
                (r["id"], r["triage_summary"], r.get("delegated_to"),
                 r.get("waiting_on_operator", False), r.get("chase_by"), r.get("created_at"))
                for r in self.db.asks
            ]
        elif "SELECT set_config" in s:
            self._mode = None
            self._result = []
        elif "UPDATE operator_messages SET front_desk_acked_at=now()" in s:
            self._mode = None
            mid = params[0]
            if self.db.fail_stamp_for == mid:
                raise RuntimeError("simulated stamp failure")
            for m in self.db.messages:
                if m["id"] == mid:
                    m["front_desk_acked_at"] = "now"
            self._result = []
        elif "INSERT INTO front_desk_latency" in s:
            self._mode = None
            self.db.latency_rows.append(params)
            self._result = []
        else:
            self._mode = None
            self._result = []

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    @property
    def description(self):
        if self._mode == "asks":
            return [("id",), ("triage_summary",), ("delegated_to",), ("waiting_on_operator",),
                    ("chase_by",), ("created_at",)]
        return []


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

    def rollback(self):
        self.db.rollbacks += 1


class _FakeDB:
    def __init__(self, messages, asks=None):
        self.messages = messages
        self.asks = asks or []
        self.latency_rows = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_stamp_for = None


DIRECT_ANSWER_FLEET = {"category": "direct-answer", "domain": "fleet"}
MUSA_ID = "111222333"


@pytest.fixture
def wired(monkeypatch):
    sent = []

    def install(db):
        monkeypatch.setenv("MUSA_TELEGRAM_ID", MUSA_ID)
        monkeypatch.setattr(fdr, "_dsn", lambda: "postgresql://fake")
        monkeypatch.setattr(fdr.psycopg, "connect", lambda *a, **k: _FakeConn(db))

        def _fake_enqueue(channel, text=None, **k):
            sent.append((channel, text, k))
            return 1
        monkeypatch.setattr(fdr.tg_out, "enqueue", _fake_enqueue)
        return sent
    return install


def _musa_dm_msg(**overrides):
    base = {"id": 1, "chat_id": 999, "from_user_id": MUSA_ID, "text": "live list",
            "cos_triage": DIRECT_ANSWER_FLEET, "created_at": "t0"}
    base.update(overrides)
    return base


# ── drain_once: ANSWER path ─────────────────────────────────────────────────

def test_drain_once_answers_live_list_stamps_ack_only_leaves_handled_untouched(wired):
    db = _FakeDB(
        messages=[_musa_dm_msg()],
        asks=[{"id": 10, "triage_summary": "ship the thing", "delegated_to": "cc-cosem",
               "waiting_on_operator": True}],
    )
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 1
    assert db.messages[0]["front_desk_acked_at"] == "now"
    assert db.messages[0].get("handled_at") is None  # item 7: never stamped, even on ANSWER
    assert len(sent) == 1
    channel, text, kwargs = sent[0]
    assert channel == fdr.CHANNEL_KEY
    assert "ship the thing" in text
    assert kwargs["reply_to"] == 1
    assert db.latency_rows[0][1] == "answer"


def test_drain_once_excludes_irsyad_rows_from_answer(wired):
    db = _FakeDB(
        messages=[_musa_dm_msg()],
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


def test_drain_once_stamp_is_committed_before_the_send(wired, monkeypatch):
    # item 4: the stamp transaction must commit BEFORE tg_out.enqueue is
    # called, so a send failure after this point can never cause a resend.
    order = []
    db = _FakeDB(messages=[_musa_dm_msg()],
                 asks=[{"id": 1, "triage_summary": "x", "waiting_on_operator": False}])
    wired(db)

    class _TrackedConn(_FakeConn):
        def commit(self):
            order.append("commit")
            super().commit()

    def _enqueue(*a, **k):
        order.append("send")
        return 1
    monkeypatch.setattr(fdr.tg_out, "enqueue", _enqueue)
    fdr.drain_once(_TrackedConn(db))
    assert order == ["commit", "send"]
    assert db.commits == 1


# ── drain_once: ACK path (no Telegram send — ingest.py already acked) ──────

def test_drain_once_acks_non_whitelisted_message_sends_nothing(wired):
    db = _FakeDB(messages=[_musa_dm_msg(
        id=2, text="can you ship the storefront PR today")])
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 1
    assert db.messages[0]["front_desk_acked_at"] == "now"
    assert db.messages[0].get("handled_at") is None
    assert sent == []  # item 5: no duplicate ack -- ingest.py already sent one
    assert db.latency_rows[0][1] == "ack"


def test_drain_once_acks_live_list_text_when_triage_disagrees(wired):
    # Double-guard in action: the words match but triage says governance-fork.
    db = _FakeDB(messages=[_musa_dm_msg(id=3, cos_triage={"category": "governance-fork"})])
    sent = wired(db)
    fdr.drain_once(_FakeConn(db))
    assert db.messages[0].get("handled_at") is None
    assert db.messages[0]["front_desk_acked_at"] == "now"
    assert sent == []


def test_handled_at_never_stamped_by_either_branch(wired):
    db = _FakeDB(messages=[
        _musa_dm_msg(id=1, text="live list"),
        _musa_dm_msg(id=2, text="random question"),
    ], asks=[{"id": 1, "triage_summary": "x", "waiting_on_operator": False}])
    wired(db)
    fdr.drain_once(_FakeConn(db))
    for m in db.messages:
        assert m["front_desk_acked_at"] == "now"
        assert m.get("handled_at") is None


# ── Sender/chat gate (item 3 — the most severe finding) ─────────────────────

def test_drain_once_never_acks_or_answers_a_group_chat(wired):
    db = _FakeDB(messages=[_musa_dm_msg(id=4, chat_id=-100123456789)])  # negative = group
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 0
    assert sent == []
    assert db.messages[0].get("front_desk_acked_at") is None


def test_drain_once_never_acks_or_answers_a_non_musa_sender(wired):
    db = _FakeDB(messages=[_musa_dm_msg(id=5, from_user_id="999999999")])  # not Musa
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 0
    assert sent == []
    assert db.messages[0].get("front_desk_acked_at") is None


def test_drain_once_never_sends_live_list_to_a_group_even_from_musa(wired):
    # Most important case named in the gate review: Musa's own id, but the
    # chat itself is a group -- must still refuse.
    db = _FakeDB(
        messages=[_musa_dm_msg(id=6, chat_id=-100999, text="live list")],
        asks=[{"id": 1, "triage_summary": "secret internal ask", "waiting_on_operator": True}],
    )
    sent = wired(db)
    fdr.drain_once(_FakeConn(db))
    assert sent == []
    assert db.messages[0].get("front_desk_acked_at") is None


def test_is_musa_dm_true_only_for_musa_private_chat(monkeypatch):
    monkeypatch.setenv("MUSA_TELEGRAM_ID", MUSA_ID)
    assert fdr._is_musa_dm(999, MUSA_ID) is True
    assert fdr._is_musa_dm(-100999, MUSA_ID) is False
    assert fdr._is_musa_dm(999, "someone-else") is False
    assert fdr._is_musa_dm(None, MUSA_ID) is False
    assert fdr._is_musa_dm(999, None) is False


def test_is_musa_dm_false_when_musa_telegram_id_unset(monkeypatch):
    monkeypatch.delenv("MUSA_TELEGRAM_ID", raising=False)
    assert fdr._is_musa_dm(999, "111222333") is False


# ── Skips already-touched rows ──────────────────────────────────────────────

def test_drain_once_skips_already_acked_row(wired):
    db = _FakeDB(messages=[_musa_dm_msg(id=7, text="hello", cos_triage=None,
                                         front_desk_acked_at="earlier")])
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 0
    assert sent == []


def test_drain_once_skips_already_handled_row(wired):
    db = _FakeDB(messages=[_musa_dm_msg(id=8, text="hello", cos_triage=None,
                                         handled_at="earlier")])
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 0
    assert sent == []


# ── Transactional correctness (item 4) ──────────────────────────────────────

def test_drain_once_failed_stamp_rolls_back_and_never_sends(wired):
    db = _FakeDB(messages=[_musa_dm_msg(id=9, text="hello", cos_triage=None)])
    db.fail_stamp_for = 9
    sent = wired(db)
    touched = fdr.drain_once(_FakeConn(db))
    assert touched == 0
    assert sent == []  # stamp happens before send -- a failed stamp sends nothing
    assert db.rollbacks == 1
    assert db.messages[0].get("front_desk_acked_at") is None


def test_drain_once_send_failure_after_stamp_does_not_cause_resend(wired, monkeypatch):
    db = _FakeDB(
        messages=[_musa_dm_msg(id=10, text="live list")],
        asks=[{"id": 1, "triage_summary": "x", "waiting_on_operator": False}],
    )
    wired(db)

    def _boom(*a, **k):
        raise RuntimeError("telegram enqueue down")
    monkeypatch.setattr(fdr.tg_out, "enqueue", _boom)
    fdr.drain_once(_FakeConn(db))
    # The row is already stamped (committed before the send attempt), so a
    # second drain pass must NOT pick it up again -- no resend loop.
    assert db.messages[0]["front_desk_acked_at"] == "now"
    touched_again = fdr.drain_once(_FakeConn(db))
    assert touched_again == 0


# ── Host/role guard (item 1) ─────────────────────────────────────────────────

def test_host_role_ok_true_on_darwin_console(monkeypatch):
    monkeypatch.setattr(fdr.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    ok, reason = fdr.host_role_ok()
    assert ok is True
    assert reason == ""


def test_host_role_ok_false_on_non_darwin(monkeypatch):
    monkeypatch.setattr(fdr.platform, "system", lambda: "Linux")
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    ok, reason = fdr.host_role_ok()
    assert ok is False
    assert "Darwin" in reason


def test_host_role_ok_false_when_not_console(monkeypatch):
    monkeypatch.setattr(fdr.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("ORCH_BODY_ROLE", "hub")
    ok, reason = fdr.host_role_ok()
    assert ok is False
    assert "console" in reason


def test_host_role_ok_false_when_role_unset(monkeypatch):
    monkeypatch.setattr(fdr.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    ok, reason = fdr.host_role_ok()
    assert ok is False


def test_main_refuses_to_start_off_host(monkeypatch):
    monkeypatch.setattr(fdr.platform, "system", lambda: "Linux")
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    connect_calls = []
    monkeypatch.setattr(fdr.psycopg, "connect", lambda *a, **k: connect_calls.append(1))
    assert fdr.main() == 1
    assert connect_calls == []


# ── Kill switch (item 2 — re-read every pass, not just at startup) ─────────

def test_enabled_accepts_true_variants(monkeypatch):
    for val in ("1", "true", "True", "yes"):
        monkeypatch.setenv("FRONT_DESK_ENABLED", val)
        assert fdr.enabled() is True
    for val in ("0", "false", "", "no"):
        monkeypatch.setenv("FRONT_DESK_ENABLED", val)
        assert fdr.enabled() is False


def test_tick_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("FRONT_DESK_ENABLED", raising=False)
    connect_calls = []
    monkeypatch.setattr(fdr.psycopg, "connect", lambda *a, **k: connect_calls.append(1))
    fdr._tick()
    assert connect_calls == []


def test_tick_connects_when_enabled(monkeypatch):
    monkeypatch.setenv("FRONT_DESK_ENABLED", "1")
    db = _FakeDB(messages=[])
    monkeypatch.setattr(fdr, "_dsn", lambda: "postgresql://fake")
    connect_calls = []
    monkeypatch.setattr(fdr.psycopg, "connect", lambda *a, **k: (connect_calls.append(1), _FakeConn(db))[1])
    fdr._tick()
    assert connect_calls == [1]


def test_kill_switch_flips_off_mid_run_without_restart(monkeypatch):
    # item 2: the kill switch is re-read every _tick(), not cached from a
    # single check at startup -- flipping it off between two ticks must
    # silence the very next one, with no process restart.
    db = _FakeDB(messages=[])
    monkeypatch.setattr(fdr, "_dsn", lambda: "postgresql://fake")
    connect_calls = []
    monkeypatch.setattr(fdr.psycopg, "connect", lambda *a, **k: (connect_calls.append(1), _FakeConn(db))[1])

    monkeypatch.setenv("FRONT_DESK_ENABLED", "1")
    fdr._tick()
    assert connect_calls == [1]

    monkeypatch.setenv("FRONT_DESK_ENABLED", "0")
    fdr._tick()
    assert connect_calls == [1]  # unchanged -- second tick made no new connection
