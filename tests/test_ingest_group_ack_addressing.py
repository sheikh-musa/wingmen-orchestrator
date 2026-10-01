"""Group-chat ack addressing (bus #47483).

Regression cover for the '📨 Got your message — I'm mid-task...' auto-ack
firing on plain banter in a GROUP chat not addressed to the bot at all
(op#24073/#24083: Musa + Ray banter in the cosem-caai group triggered it
twice in 10 minutes). Two separate code paths needed the fix:

  1. throttled_busy_ack's call site (process_update, per-update, a live `msg`
     is available) — gated on _is_group_chat(msg) and _bot_is_addressed(ch, msg).
  2. reassure_if_unhandled (a periodic aggregate sweep with NO live `msg`) —
     gated on the chat's resolved type (getChat, cached) plus a best-effort
     @mention check against unhandled rows' stored text.

DMs are untouched in both paths: _is_group_chat / the resolved chat type is
only ever true for a 'group'/'supergroup' chat, so the gate is a no-op there.

Pure-function tests: tg_call is monkeypatched, nothing touches the network;
reassure_if_unhandled is exercised against a minimal fake conn/cursor (no DB).
"""
import pytest

from nervous_system import ingest


class _Ch:
    key = "cosem-caai"
    token = "TESTTOKEN"
    channel_tag = "cosem-caai"
    mode = "log-and-route"
    allowed_chat_ids = [-555]


@pytest.fixture
def ch():
    return _Ch()


@pytest.fixture(autouse=True)
def clear_identity_caches():
    """Both lookups are module-level cached by token/chat — reset between
    tests so one test's monkeypatched tg_call can't leak into another."""
    ingest._bot_identity_cache.clear()
    ingest._chat_type_cache.clear()
    yield
    ingest._bot_identity_cache.clear()
    ingest._chat_type_cache.clear()


def _msg(**kw):
    base = {"message_id": 1, "date": 0, "chat": {"id": -555, "type": "group"},
            "from": {"id": 7}}
    base.update(kw)
    return base


# ── _is_group_chat ─────────────────────────────────────────────────────────

def test_is_group_chat_true_for_group_and_supergroup():
    assert ingest._is_group_chat({"chat": {"type": "group"}}) is True
    assert ingest._is_group_chat({"chat": {"type": "supergroup"}}) is True


def test_is_group_chat_false_for_private():
    assert ingest._is_group_chat({"chat": {"type": "private"}}) is False
    assert ingest._is_group_chat({"chat": {}}) is False


# ── _bot_is_addressed ───────────────────────────────────────────────────────

def test_bot_is_addressed_by_mention_entity(monkeypatch):
    monkeypatch.setattr(ingest, "tg_call",
                        lambda token, method, params: {"id": 99, "username": "cosem_bot"})
    msg = _msg(text="hey @cosem_bot can you check this",
               entities=[{"type": "mention", "offset": 4, "length": 10}])
    assert ingest._bot_is_addressed(_Ch(), msg) is True


def test_bot_is_addressed_by_reply_to_bot(monkeypatch):
    monkeypatch.setattr(ingest, "tg_call",
                        lambda token, method, params: {"id": 99, "username": "cosem_bot"})
    msg = _msg(text="yes please", reply_to_message={"from": {"id": 99}})
    assert ingest._bot_is_addressed(_Ch(), msg) is True


def test_bot_is_addressed_substring_fallback_for_utf16_offset_drift(monkeypatch):
    """Entity offsets are UTF-16 code units; an emoji-preceded mention can
    drift a Python-index slice. The plain substring fallback still catches it."""
    monkeypatch.setattr(ingest, "tg_call",
                        lambda token, method, params: {"id": 99, "username": "cosem_bot"})
    msg = _msg(text="\U0001F600 @cosem_bot ping",
               entities=[{"type": "mention", "offset": 0, "length": 10}])  # deliberately wrong
    assert ingest._bot_is_addressed(_Ch(), msg) is True


def test_bot_not_addressed_is_plain_banter(monkeypatch):
    """The exact production shape: two humans chatting, bot never named."""
    monkeypatch.setattr(ingest, "tg_call",
                        lambda token, method, params: {"id": 99, "username": "cosem_bot"})
    msg = _msg(text="lol did you see that game last night")
    assert ingest._bot_is_addressed(_Ch(), msg) is False


def test_bot_is_addressed_defaults_true_when_identity_unknown(monkeypatch):
    """getMe failed (network hiccup) -- fail toward the pre-fix behavior
    (ack fires), never toward new silence on an unknown."""
    monkeypatch.setattr(ingest, "tg_call",
                        lambda token, method, params: (_ for _ in ()).throw(RuntimeError("boom")))
    msg = _msg(text="whatever")
    assert ingest._bot_is_addressed(_Ch(), msg) is True


# ── reassure_if_unhandled's group gate (fake conn, no DB) ───────────────────

class _FakeCursor:
    def __init__(self, state):
        self.state = state
        self._last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._last_sql = sql
        if sql.strip().startswith("INSERT INTO tg_out"):
            self.state["inserts"].append(params)

    def fetchone(self):
        sql = self._last_sql
        if "array_agg(chat_id" in sql:
            return self.state["unhandled_agg"]
        if "make_interval(secs => %s)" in sql:
            return (self.state["stale"],)
        if sql.strip().startswith("SELECT 1 FROM tg_out"):
            return self.state["already_acked"]
        if "text ILIKE" in sql:
            return self.state["addressed_row"]
        raise AssertionError(f"unexpected fetchone() for sql: {sql!r}")


class _FakeConn:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _FakeCursor(self.state)

    def commit(self):
        pass


def _state(**over):
    base = dict(
        unhandled_agg=(2, "2026-10-01T00:00:00Z", "2026-10-01T00:05:00Z", -555),
        stale=True, already_acked=None, addressed_row=None, inserts=[],
    )
    base.update(over)
    return base


def test_reassure_stays_quiet_in_group_with_no_addressed_row(monkeypatch, ch):
    monkeypatch.setattr(ingest, "tg_call", lambda token, method, params:
                        {"type": "group"} if method == "getChat" else
                        {"id": 99, "username": "cosem_bot"})
    state = _state(addressed_row=None)
    ingest.reassure_if_unhandled(_FakeConn(state), ch)
    assert state["inserts"] == []


def test_reassure_fires_in_group_when_a_row_addresses_the_bot(monkeypatch, ch):
    monkeypatch.setattr(ingest, "tg_call", lambda token, method, params:
                        {"type": "group"} if method == "getChat" else
                        {"id": 99, "username": "cosem_bot"})
    state = _state(addressed_row=(1,))
    ingest.reassure_if_unhandled(_FakeConn(state), ch)
    assert len(state["inserts"]) == 1


def test_reassure_unchanged_for_private_chat(monkeypatch, ch):
    monkeypatch.setattr(ingest, "tg_call", lambda token, method, params:
                        {"type": "private"} if method == "getChat" else
                        {"id": 99, "username": "cosem_bot"})
    state = _state(addressed_row=None)   # no mention anywhere -- must not matter for a DM
    ingest.reassure_if_unhandled(_FakeConn(state), ch)
    assert len(state["inserts"]) == 1


def test_reassure_fires_when_chat_type_lookup_fails(monkeypatch, ch):
    """getChat errors -- fail toward the pre-fix behavior (ack fires), not a
    new silent-suppression mode."""
    def boom(token, method, params):
        raise RuntimeError("network hiccup")
    monkeypatch.setattr(ingest, "tg_call", boom)
    state = _state(addressed_row=None)
    ingest.reassure_if_unhandled(_FakeConn(state), ch)
    assert len(state["inserts"]) == 1
