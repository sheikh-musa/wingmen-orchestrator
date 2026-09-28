"""operator_log.log() wires nervous_system.vault_leak_guard.defensive_redact in
before every INSERT (bus #44378) — the last line of defense before a secret
value reaches the durable operator_messages log, regardless of which send
script (or lack of one) produced the text. Mocks psycopg entirely (same
_FakeConn/_FakeCur shape as tests/test_asks_daily_digest.py) so this is a
pure-logic test of the WIRING, not a live-DB integration test — the redaction
logic itself is covered by tests/test_vault_leak_guard.py."""
from __future__ import annotations

import importlib

import pytest

ol = importlib.import_module("nervous_system.operator_log")


class _FakeCur:
    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=None):
        self.store.append((sql, params))

    def fetchone(self):
        return (999,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _FakeCur(self.store)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_db(monkeypatch):
    store = []
    monkeypatch.setattr(ol.psycopg, "connect", lambda *a, **k: _FakeConn(store))
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    return store


def _insert_params(store):
    for sql, params in store:
        if sql.strip().startswith("INSERT INTO operator_messages"):
            return params
    raise AssertionError("no operator_messages INSERT captured")


def test_log_redacts_text_when_defensive_redact_fires(monkeypatch, fake_db, capsys):
    monkeypatch.setattr(
        ol,
        "defensive_redact",
        lambda text: (text.replace("hunter2", "[REDACTED: oeh_preview_password]"), ["oeh_preview_password"]),
    )
    ol.log("outbound", "password is hunter2", chat_id="123", tag="oeh")

    logged_text = _insert_params(fake_db)[4]
    assert "hunter2" not in logged_text
    assert "[REDACTED: oeh_preview_password]" in logged_text
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "oeh_preview_password" in err


def test_log_passes_text_through_unchanged_when_nothing_matches(monkeypatch, fake_db, capsys):
    monkeypatch.setattr(ol, "defensive_redact", lambda text: (text, []))
    ol.log("outbound", "clean message, nothing to see here", chat_id="123", tag="oeh")

    assert _insert_params(fake_db)[4] == "clean message, nothing to see here"
    assert "WARNING" not in capsys.readouterr().err


def test_log_never_fails_when_defensive_redact_raises(monkeypatch, fake_db):
    def _raise(text):
        raise RuntimeError("vault plumbing exploded")

    monkeypatch.setattr(ol, "defensive_redact", _raise)
    rid = ol.log("outbound", "message text", chat_id="123", tag="oeh")

    assert rid == 999
    # best-effort: a defensive_redact hiccup must never cost the primary log
    # write, even though it means this particular text wasn't scanned.
    assert _insert_params(fake_db)[4] == "message text"


def test_log_calls_defensive_redact_for_inbound_too(monkeypatch, fake_db):
    """The incident this closes was outbound, but a client could just as
    easily paste a secret back inbound — the scan applies to both directions,
    not just outbound sends."""
    calls = []

    def _spy(text):
        calls.append(text)
        return text, []

    monkeypatch.setattr(ol, "defensive_redact", _spy)
    ol.log("inbound", "hello from a client", chat_id="123", tag="oeh")
    assert calls == ["hello from a client"]
