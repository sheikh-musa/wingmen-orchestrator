"""test_tg_out_dead_consumer.py — the tg_out 'dead' row consumer (held_commitment
#314 / fable-audit-substrate-20261006 finding 12: "give tg_out.dead a
replay-once-then-purge consumer"). A dead row (MAX_ATTEMPTS exhausted) used to
just sit there forever (352 rows, +11/week, nothing reading them).

Covers: one more delivery attempt per dead row; success is bookkept the same
as a normal send; failure deletes the row and posts exactly one attributable
bus note to orch-console; the whole pass is single-flight via an advisory
lock. Pure-logic / fake-conn tests — no real DB, no real Telegram send.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nervous_system import tg_out  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._sql = ""

    def execute(self, sql, params=None):
        self._sql = sql
        self.conn.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        if "pg_try_advisory_lock" in self._sql:
            return (self.conn.lock_available,)
        if "pg_advisory_unlock" in self._sql:
            return (True,)
        raise AssertionError(f"unexpected fetchone for: {self._sql}")

    def fetchall(self):
        if "status='dead'" in self._sql:
            return list(self.conn.dead_rows)
        raise AssertionError(f"unexpected fetchall for: {self._sql}")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, dead_rows=(), lock_available=True):
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self.dead_rows = list(dead_rows)
        self.lock_available = lock_available

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


def _executed_sql(conn):
    return [sql for sql, _ in conn.executed]


# ---- _replay_one --------------------------------------------------------------

def test_replay_one_success_marks_sent_and_logs_operator_message(monkeypatch):
    monkeypatch.setattr(tg_out, "_resolve", lambda conn, ck: ("tok", 111, "tag1"))
    sent = []
    monkeypatch.setattr(tg_out, "_send_text", lambda token, chat, text: sent.append((chat, text)))

    conn = FakeConn()
    ok = tg_out._replay_one(conn, 5, "c1", None, "hello", None)

    assert ok is True
    assert sent == [(111, "hello")]
    sql = _executed_sql(conn)
    assert any("UPDATE tg_out SET status='sent'" in s for s in sql)
    assert any("INSERT INTO operator_messages" in s for s in sql)
    assert conn.commits == 1


def test_replay_one_failure_deletes_row_and_posts_bus_note(monkeypatch):
    monkeypatch.setattr(tg_out, "_resolve", lambda conn, ck: ("tok", 111, "tag1"))

    def _boom(token, chat, text):
        raise RuntimeError("telegram still down")
    monkeypatch.setattr(tg_out, "_send_text", _boom)

    sent = []

    class _FakeBusSend:
        @staticmethod
        def send(**kwargs):
            sent.append(kwargs)
            return (1, "thread")

    import scripts
    monkeypatch.setattr(scripts, "bus_send", _FakeBusSend, raising=False)

    conn = FakeConn()
    ok = tg_out._replay_one(conn, 7, "c1", None, "undeliverable text", None)

    assert ok is False
    delete = [p for s, p in conn.executed if "DELETE FROM tg_out" in s]
    assert delete == [(7,)]
    assert not any("INSERT INTO agent_messages" in s for s, _ in conn.executed)
    assert len(sent) == 1
    call = sent[0]
    assert call["from_agent"] == "tg-out-dead-replay"
    assert call["to"] == "orch-console"
    assert call["priority"] == "P2" and call["req"] is False
    assert "#7" in call["subject"] and "c1" in call["subject"]
    assert "RuntimeError: telegram still down" in call["body"]
    assert "undeliverable text" in call["body"]


def test_replay_one_no_chat_target_is_treated_as_failure(monkeypatch):
    monkeypatch.setattr(tg_out, "_resolve", lambda conn, ck: ("tok", None, "tag1"))
    monkeypatch.setattr(tg_out, "_send_text", lambda *a: (_ for _ in ()).throw(
        AssertionError("should never be called without a target")))

    class _FakeBusSend:
        @staticmethod
        def send(**kwargs):
            return (1, "thread")

    import scripts
    monkeypatch.setattr(scripts, "bus_send", _FakeBusSend, raising=False)

    conn = FakeConn()
    ok = tg_out._replay_one(conn, 9, "c1", None, "hi", None)

    assert ok is False
    assert any("DELETE FROM tg_out" in s for s, _ in conn.executed)


# ---- replay_once_then_purge ---------------------------------------------------

def test_replay_once_then_purge_processes_all_rows_and_aggregates(monkeypatch):
    calls = []

    def _fake_replay_one(conn, rid, channel_key, chat_id, text, file_path):
        calls.append(rid)
        return rid == 1
    monkeypatch.setattr(tg_out, "_replay_one", _fake_replay_one)

    conn = FakeConn(dead_rows=[(1, "c1", None, "t1", None), (2, "c2", None, "t2", None)])
    result = tg_out.replay_once_then_purge(conn)

    assert calls == [1, 2]
    assert result == {"replayed": 1, "purged": 1, "total": 2}
    sql = _executed_sql(conn)
    assert any("pg_try_advisory_lock" in s for s in sql)
    assert any("pg_advisory_unlock" in s for s in sql)


def test_replay_once_then_purge_skips_when_lock_held(monkeypatch):
    calls = []
    monkeypatch.setattr(tg_out, "_replay_one", lambda *a: calls.append(a) or True)

    conn = FakeConn(dead_rows=[(1, "c1", None, "t1", None)], lock_available=False)
    result = tg_out.replay_once_then_purge(conn)

    assert calls == []
    assert result == {"replayed": 0, "purged": 0, "total": 0}


def test_replay_once_then_purge_noop_on_empty_dead_set(monkeypatch):
    calls = []
    monkeypatch.setattr(tg_out, "_replay_one", lambda *a: calls.append(a) or True)

    conn = FakeConn(dead_rows=[])
    result = tg_out.replay_once_then_purge(conn)

    assert calls == []
    assert result == {"replayed": 0, "purged": 0, "total": 0}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
