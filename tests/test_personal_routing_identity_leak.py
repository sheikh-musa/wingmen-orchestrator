"""orch-console #47912 point 3 (required before PR #240 merge): regression
coverage for the personal-routing split-write path in process_update(),
specifically the cc-quality PR #240 HIGH fix (sender identity is in-silo for
a personal-routed tag — the substrate envelope must get NULL/sentinel
from_user_id/from_username/from_name; the real values land only in
wingmen-personal).

Pure-logic mocks at the DB boundary (same _FakeConn/_FakeCur shape as
tests/test_operator_log_vault_leak_guard.py) — no live DB, no network. The
prod-DSN guard in conftest.py is untouched: DATABASE_URL is a fake
placeholder string, never read for a real connection, since psycopg.connect
itself is monkeypatched out.

(a) personal-routed inbound -> substrate row has sentinel text + sentinel
    cos_triage + NO real from_* values; wingmen-personal still gets the real
    identity + real text.
(b) a forced write_personal_content() failure -> the whole envelope write is
    rolled back (conn.rollback() called), never committed.
(c) a non-personal tag's path is byte-identical to before this fix: real
    text + real identity land on the substrate row, write_personal_content()
    is never called.
"""
from __future__ import annotations

import importlib

import pytest

ingest = importlib.import_module("nervous_system.ingest")
personal_routing = importlib.import_module("nervous_system.personal_routing")


class _FakeCur:
    def __init__(self, store, fetch_plan):
        self.store = store
        self.fetch_plan = fetch_plan
        self._last_sql = None

    def execute(self, sql, params=None):
        self.store.append((sql.strip(), params))
        self._last_sql = sql.strip()

    def fetchone(self):
        for prefix, value in self.fetch_plan:
            if self._last_sql.startswith(prefix):
                return value
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, store, fetch_plan):
        self.store = store
        self.fetch_plan = fetch_plan
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return _FakeCur(self.store, self.fetch_plan)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _conn_won(op_msg_id=42):
    store = []
    fetch_plan = [
        ("INSERT INTO ingest_dedup", ("mamadah",)),  # non-None => "won" the dedupe
        ("INSERT INTO operator_messages", (op_msg_id,)),
    ]
    return _FakeConn(store, fetch_plan), store


# Deliberately NOT in either channel's allowed_chat_ids: the LOG section under
# test (dedupe -> envelope write -> personal-content write -> commit) runs
# fully before the GATE check, and a gated update returns True right after —
# never reaching the ROUTE section (nudge/ack/tmux), which this pure-logic
# fake DB doesn't model. Gating itself is covered elsewhere (gate_allows is a
# pure function with its own unit tests).
_UNLISTED_CHAT_ID = 1


def _mamadah_channel():
    row = (
        "mamadah", "MAMADAH_BOT_TOKEN", "agent-session", "mamadah", None,
        None, [286619815], [], {}, "mamadah",
        "substrate", 0, "family", "cc-mamadah", None,
    )
    return ingest.Channel(row)


def _oeh_channel():
    row = (
        "oeh", "OEH_BOT_TOKEN", "agent-session", "oeh", None,
        None, [999], [], {}, "oeh",
        "substrate", 0, "client", "cc-oeh", None,
    )
    return ingest.Channel(row)


def _synthetic_update(upd_id: int, chat_id: int, text: str, user_id: int,
                       first_name: str, username: str) -> dict:
    return {
        "update_id": upd_id,
        "message": {
            "message_id": upd_id,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "first_name": first_name, "username": username},
            "text": text,
        },
    }


def _op_msg_insert_params(store):
    for sql, params in store:
        if sql.startswith("INSERT INTO operator_messages"):
            return params
    raise AssertionError("no operator_messages INSERT captured")


def test_a_personal_routed_inbound_is_sentinel_free_of_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(
        personal_routing, "write_personal_content",
        lambda *a, **k: calls.append((a, k)) or 123,
    )
    ch = _mamadah_channel()
    conn, store = _conn_won()
    upd = _synthetic_update(1001, _UNLISTED_CHAT_ID, "hi mama dah", 6606903261, "Zahidah", "zah")

    won = ingest.process_update(conn, ch, upd)

    assert won is True
    params = _op_msg_insert_params(store)
    # params = (chat_id, tag, stored_content, stored_from_user_id,
    #           stored_from_username, stored_from_name, cos_triage)
    _, tag, text, from_user_id, from_username, from_name, cos_triage = params
    assert tag == "mamadah"
    assert text == personal_routing.SENTINEL_TEXT
    assert "hi mama dah" not in text
    assert from_user_id is None
    assert from_username is None
    assert from_name is None
    assert cos_triage == personal_routing.SENTINEL_COS_TRIAGE
    assert "hi mama dah" not in cos_triage

    # wingmen-personal still gets the real content + real identity.
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["text"] == "hi mama dah"
    assert kwargs["from_user_id"] == "6606903261"
    assert kwargs["from_name"] == "Zahidah"
    assert kwargs["from_username"] == "zah"

    # One commit for the envelope+personal-content write, one more from the
    # gate's best-effort handled_at stamp on this deliberately-unlisted
    # chat_id (see _UNLISTED_CHAT_ID) — both are expected, not a double-write
    # of the same content.
    assert conn.committed == 2
    assert conn.rolled_back == 0


def test_b_forced_personal_write_failure_rolls_back_the_envelope(monkeypatch):
    def _boom(*a, **k):
        raise personal_routing.PersonalRouteError("wingmen-personal insert failed: 500")

    monkeypatch.setattr(personal_routing, "write_personal_content", _boom)
    ch = _mamadah_channel()
    conn, store = _conn_won()
    upd = _synthetic_update(1002, _UNLISTED_CHAT_ID, "this must never land anywhere", 6606903261,
                             "Zahidah", "zah")

    with pytest.raises(personal_routing.PersonalRouteError):
        ingest.process_update(conn, ch, upd)

    assert conn.rolled_back == 1
    assert conn.committed == 0
    # the envelope INSERT was attempted (in-transaction) before the raise —
    # what matters is it was never committed, which the rollback assertion
    # above already covers; this just documents the sequencing.
    assert any(sql.startswith("INSERT INTO operator_messages") for sql, _ in store)
    # process_update() never touches bot_channels/poll_offset itself (that's
    # channel_loop's job, after a successful return) — on a raise, channel_loop
    # never reaches its offset-advance code for this batch, so Telegram
    # redelivers. Asserting process_update issued no such statement here
    # documents that invariant at the unit-test level (cc-quality #47923).
    assert not any("poll_offset" in sql for sql, _ in store)


def test_c_non_personal_tag_path_is_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(
        personal_routing, "write_personal_content",
        lambda *a, **k: calls.append((a, k)) or 999,
    )
    ch = _oeh_channel()
    conn, store = _conn_won()
    upd = _synthetic_update(1003, _UNLISTED_CHAT_ID, "real client text", 555, "Client Contact", "clientco")

    won = ingest.process_update(conn, ch, upd)

    assert won is True
    params = _op_msg_insert_params(store)
    _, tag, text, from_user_id, from_username, from_name, cos_triage = params
    assert tag == "oeh"
    assert text == "real client text"
    assert from_user_id == "555"
    assert from_username == "clientco"
    assert from_name == "Client Contact"
    # non-personal path never touches wingmen-personal at all.
    assert calls == []
    assert conn.committed == 2  # envelope write + the gate's handled_at stamp
    assert conn.rolled_back == 0
