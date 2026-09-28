"""operator_log.log() wires nervous_system.vault_leak_guard.defensive_redact in
before every INSERT (bus #44378), tightened per orch-console's PR #197 review
(bus #44388): a fired redaction must be DURABLE and VISIBLE (not just a
stderr print every send script pipes to /dev/null), and "could not check"
must never look the same as "checked, clean". Mocks psycopg entirely (same
_FakeConn/_FakeCur shape as tests/test_asks_daily_digest.py) so this is a
pure-logic test of the WIRING, not a live-DB integration test — the
redaction logic itself is covered by tests/test_vault_leak_guard.py."""
from __future__ import annotations

import importlib
import json

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


@pytest.fixture
def no_bus_alert(monkeypatch):
    """Isolate log()'s DB-write behavior from the bus-post side effect —
    _alert_vault_redaction is exercised by its own dedicated tests below."""
    calls = []
    monkeypatch.setattr(ol, "_alert_vault_redaction", lambda *a, **k: calls.append(a))
    return calls


def _insert_params(store):
    for sql, params in store:
        if sql.strip().startswith("INSERT INTO operator_messages"):
            return params
    raise AssertionError("no operator_messages INSERT captured")


def _cos(store):
    params = _insert_params(store)
    raw = params[6]  # cos_triage column
    return json.loads(raw) if raw else None


def test_log_redacts_text_when_defensive_redact_fires(monkeypatch, fake_db, no_bus_alert, capsys):
    monkeypatch.setattr(
        ol,
        "defensive_redact",
        lambda text, tag=None: (text.replace("hunter2", "[REDACTED: oeh_preview_password]"), ["oeh_preview_password"], []),
    )
    ol.log("outbound", "password is hunter2", chat_id="123", tag="oeh")

    logged_text = _insert_params(fake_db)[4]
    assert "hunter2" not in logged_text
    assert "[REDACTED: oeh_preview_password]" in logged_text
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "oeh_preview_password" in err


def test_fired_redaction_is_recorded_on_the_row_not_just_stderr(monkeypatch, fake_db, no_bus_alert):
    """The core of bus #44388 fix 1a: every send script pipes operator_log's
    stderr to /dev/null (oeh_send.sh included — the script that leaked), so
    the WARNING print alone is invisible. The row itself must carry the flag."""
    monkeypatch.setattr(
        ol,
        "defensive_redact",
        lambda text, tag=None: (text.replace("hunter2", "[REDACTED: oeh_preview_password]"), ["oeh_preview_password"], []),
    )
    ol.log("outbound", "password is hunter2", chat_id="123", tag="oeh")

    cos = _cos(fake_db)
    assert cos["vault_redacted"] == ["oeh_preview_password"]


def test_fired_redaction_posts_a_loud_bus_alert(monkeypatch, fake_db):
    calls = []
    monkeypatch.setattr(ol, "_alert_vault_redaction", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(
        ol,
        "defensive_redact",
        lambda text, tag=None: (text.replace("hunter2", "[REDACTED: x]"), ["x"], []),
    )
    ol.log("outbound", "password is hunter2", chat_id="123", tag="oeh")
    assert len(calls) == 1
    leaked_keys, direction, channel, tag = calls[0]
    assert leaked_keys == ["x"]
    assert direction == "outbound"
    assert tag == "oeh"


def test_skipped_scan_is_recorded_distinctly_from_clean(monkeypatch, fake_db, no_bus_alert):
    """A VaultError on an in-scope key ("could not check") must never be
    indistinguishable from "checked, found nothing" (bus #44388 fix 1b)."""
    monkeypatch.setattr(
        ol, "defensive_redact",
        lambda text, tag=None: (text, [], [("oeh_preview_password", "KekNotFoundError")]),
    )
    ol.log("outbound", "clean-looking text", chat_id="123", tag="oeh")

    cos = _cos(fake_db)
    assert cos["vault_scan_skipped"] == [{"key": "oeh_preview_password", "reason": "KekNotFoundError"}]
    assert "vault_redacted" not in cos


def test_log_passes_text_through_unchanged_when_nothing_matches(monkeypatch, fake_db, no_bus_alert, capsys):
    monkeypatch.setattr(ol, "defensive_redact", lambda text, tag=None: (text, [], []))
    ol.log("outbound", "clean message, nothing to see here", chat_id="123", tag="oeh")

    assert _insert_params(fake_db)[4] == "clean message, nothing to see here"
    assert _cos(fake_db) is None
    assert "WARNING" not in capsys.readouterr().err


def test_log_never_fails_when_defensive_redact_raises_non_vault_error(monkeypatch, fake_db, no_bus_alert):
    def _raise(text, tag=None):
        raise RuntimeError("vault plumbing exploded")

    monkeypatch.setattr(ol, "defensive_redact", _raise)
    rid = ol.log("outbound", "message text", chat_id="123", tag="oeh")

    assert rid == 999
    # best-effort: a defensive_redact hiccup must never cost the primary log
    # write, even though it means this particular text wasn't scanned.
    assert _insert_params(fake_db)[4] == "message text"


def test_log_calls_defensive_redact_with_the_tag_for_scoping(monkeypatch, fake_db, no_bus_alert):
    calls = []

    def _spy(text, tag=None):
        calls.append((text, tag))
        return text, [], []

    monkeypatch.setattr(ol, "defensive_redact", _spy)
    ol.log("outbound", "hello from oeh", chat_id="123", tag="oeh")
    assert calls == [("hello from oeh", "oeh")]


def test_log_calls_defensive_redact_for_inbound_too(monkeypatch, fake_db, no_bus_alert):
    """The incident this closes was outbound, but a client could just as
    easily paste a secret back inbound — the scan applies to both directions,
    not just outbound sends."""
    calls = []

    def _spy(text, tag=None):
        calls.append(text)
        return text, [], []

    monkeypatch.setattr(ol, "defensive_redact", _spy)
    ol.log("inbound", "hello from a client", chat_id="123", tag="oeh")
    assert calls == ["hello from a client"]


# ── _alert_vault_redaction: the loud bus copy ──────────────────────────────

def test_alert_vault_redaction_posts_a_p1_blocker_to_orch_console(monkeypatch):
    sent = []

    class _FakeBusSend:
        @staticmethod
        def resolve_from_agent(env):
            return "cc-substrate"

        @staticmethod
        def send(from_agent, to, mtype, subject, body, priority, req=False, **kw):
            sent.append(dict(from_agent=from_agent, to=to, mtype=mtype, subject=subject,
                              body=body, priority=priority, req=req))
            return 1, "thread"

    monkeypatch.setitem(__import__("sys").modules, "scripts.bus_send", _FakeBusSend)
    ol._alert_vault_redaction(["oeh_preview_password"], "outbound", "telegram", "oeh")

    assert len(sent) == 1
    row = sent[0]
    assert row["to"] == "orch-console"
    assert row["priority"] == "P1"
    assert row["req"] is True
    assert row["mtype"] == "blocker"
    assert "oeh_preview_password" in row["subject"]


def test_alert_vault_redaction_never_raises_when_bus_send_is_unavailable(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "scripts.bus_send", raising=False)
    monkeypatch.setitem(sys.modules, "scripts", None)  # force the import to blow up
    # must not raise
    ol._alert_vault_redaction(["k"], "outbound", "telegram", "oeh")


def test_alert_vault_redaction_never_raises_when_identity_unresolvable(monkeypatch):
    class _FakeBusSend:
        @staticmethod
        def resolve_from_agent(env):
            raise RuntimeError("cannot resolve bus from_agent identity")

        @staticmethod
        def send(*a, **k):
            raise AssertionError("send() must not be reached")

    monkeypatch.setitem(__import__("sys").modules, "scripts.bus_send", _FakeBusSend)
    # must not raise
    ol._alert_vault_redaction(["k"], "outbound", "telegram", "oeh")
