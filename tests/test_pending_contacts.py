"""TG2.0 section 6: new-contact alert + one-tap Approve.

An inbound from a chat no channel row claims is logged under the group's
fallback tag and, today, nothing else happens. These tests pin the state
machine that turns that into ONE alert with two buttons, and pin the floors:
Approve FILES a gate request and grants nothing; Ignore is not a blacklist;
a replayed or stale button acts on nothing; and a callback from anyone but
the reviewer is refused.

RED first: the module did not exist when these were written.
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE = Path(__file__).parent.parent / "nervous_system" / "pending_contacts.py"

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def pc():
    spec = importlib.util.spec_from_file_location("pending_contacts", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCursor:
    """Answers by SQL shape and records every statement.

    Shape-matched rather than a mock framework: the tests assert the SQL the
    module issues (one pending row per chat; bot_channels never touched),
    which a generic mock would hide.
    """

    def __init__(self, live=None, resolved=None, row=None):
        self.live = live          # (pending_id, expires_at) of a `pending` row, or None
        self.resolved = resolved  # (state, resolved_at) of the latest row, or None
        self.row = row            # (id, state, chat_id, channel_tag, first_name, username)
        self.calls = []
        self._result = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        s = " ".join(sql.split())
        self.rowcount = 0
        if s.startswith("SELECT pending_id, expires_at"):
            self._result = self.live
        elif s.startswith("SELECT state, resolved_at"):
            self._result = self.resolved
        elif s.startswith("SELECT id, state, chat_id, channel_tag"):
            self._result = self.row
        elif s.startswith("INSERT INTO tg_pending_contacts"):
            self.rowcount = 1
            self._result = ("pcid-new",)
        elif s.startswith("UPDATE tg_pending_contacts"):
            self.rowcount = 1
            self._result = None
        else:
            self._result = None
        return self

    def fetchone(self):
        return self._result

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def statements(self):
        return [" ".join(c[0].split()) for c in self.calls]


class FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1


LIVE_ROW = ("1", "pending", "777", "coffeemedia", "Afiq", "afiq")


# ── fire once per chat ──────────────────────────────────────────────────────

def test_first_unmatched_dm_creates_one_pending_and_returns_a_notification():
    m = pc()
    cur = FakeCursor()
    out = m.record_unmatched(FakeConn(cur), bot="wingmendevbot", chat_id="777",
                             channel_tag="coffeemedia", first_name="A", username="a",
                             now=NOW)
    assert out is not None
    assert out["pending_id"]
    assert "777" not in out["pending_id"]          # opaque: no PII in callback_data
    assert len(out["pending_id"]) <= 32
    assert sum(s.startswith("INSERT INTO tg_pending_contacts") for s in cur.statements()) == 1


def test_repeat_dms_from_the_same_pending_chat_do_not_renotify():
    m = pc()
    cur = FakeCursor(live=("pcid-1", NOW + timedelta(days=1)))
    out = m.record_unmatched(FakeConn(cur), bot="wingmendevbot", chat_id="777",
                             channel_tag="coffeemedia", first_name="A", username="a",
                             now=NOW)
    assert out is None
    assert not any(s.startswith("INSERT INTO tg_pending_contacts") for s in cur.statements())


def test_an_expired_pending_row_re_alerts_with_a_fresh_id():
    m = pc()
    cur = FakeCursor(live=("pcid-old", NOW - timedelta(days=1)))
    out = m.record_unmatched(FakeConn(cur), bot="wingmendevbot", chat_id="777",
                             channel_tag="coffeemedia", first_name="A", username="a",
                             now=NOW)
    assert out is not None
    assert out["pending_id"] != "pcid-old"


def test_ignore_is_not_a_permanent_blacklist():
    """Design 6 note 2: a later genuine DM after an Ignore must re-surface."""
    m = pc()
    cur = FakeCursor(resolved=("ignored", NOW - timedelta(minutes=5)))
    conn = FakeConn(cur)
    out = m.record_unmatched(conn, bot="wingmendevbot", chat_id="777",
                             channel_tag="coffeemedia", first_name="A", username="a",
                             now=NOW)
    assert out is not None
    assert conn.commits >= 1


# ── the alert ───────────────────────────────────────────────────────────────

def test_alert_text_carries_identity_but_never_a_message_body():
    m = pc()
    text, keyboard = m.alert(bot="wingmendevbot", chat_id="777", first_name="Afiq",
                             username="afiq", channel_tag="coffeemedia", pending_id="pcid-1")
    assert "Afiq" in text and "@afiq" in text
    assert "coffeemedia" in text
    flat = json.dumps(keyboard)
    assert "pcid-1" in flat
    assert "approve" in flat and "ignore" in flat
    for row in keyboard["inline_keyboard"]:
        for button in row:
            assert len(button["callback_data"].encode()) <= 64


def test_callback_data_carries_the_opaque_id_only():
    m = pc()
    _, keyboard = m.alert(bot="b", chat_id="777", first_name="A", username=None,
                          channel_tag="t", pending_id="pcid-1")
    datas = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
    assert all("777" not in d for d in datas)


# ── the buttons ─────────────────────────────────────────────────────────────

def test_approve_files_a_gate_request_and_grants_nothing():
    m = pc()
    filed = []
    cur = FakeCursor(row=LIVE_ROW)
    res = m.handle_button(FakeConn(cur), data="pc:approve:pcid-1", presser="999",
                          reviewer_chat="999", file_gate_request=filed.append, now=NOW)
    assert res["ok"] is True
    assert len(filed) == 1
    req = filed[0]
    assert req["chat_id"] == "777"
    assert req["powers"] == ["reply_text"]
    assert req["phase"] == "supervised"
    # Nothing may enable anything: bot_channels is never touched.
    assert not any("bot_channels" in s for s in cur.statements())


def test_a_callback_from_anyone_but_the_reviewer_is_ignored():
    m = pc()
    filed = []
    cur = FakeCursor(row=LIVE_ROW)
    res = m.handle_button(FakeConn(cur), data="pc:approve:pcid-1", presser="1234",
                          reviewer_chat="999", file_gate_request=filed.append, now=NOW)
    assert res["ok"] is False
    assert res["reason"] == "not_reviewer"
    assert filed == []
    assert not any(s.startswith("UPDATE tg_pending_contacts") for s in cur.statements())


def test_unknown_or_malformed_callback_data_acts_on_nothing():
    m = pc()
    filed = []
    for data in ("pc:approve:", "pc:delete:pcid-1", "pc:approve:pcid-1:extra", "something"):
        cur = FakeCursor(row=LIVE_ROW)
        res = m.handle_button(FakeConn(cur), data=data, presser="999",
                              reviewer_chat="999", file_gate_request=filed.append, now=NOW)
        assert res["ok"] is False, data
    assert filed == []


def test_a_stale_or_replayed_pending_id_acts_on_nothing():
    m = pc()
    filed = []
    cur = FakeCursor(row=None)      # no live pending row for this id
    res = m.handle_button(FakeConn(cur), data="pc:approve:pcid-gone", presser="999",
                          reviewer_chat="999", file_gate_request=filed.append, now=NOW)
    assert res["ok"] is False
    assert res["reason"] == "expired_or_unknown"
    assert filed == []


def test_approve_is_idempotent():
    """A resolved row cannot be approved twice (a redelivered button)."""
    m = pc()
    filed = []
    cur = FakeCursor(row=("1", "approved", "777", "coffeemedia", "Afiq", "afiq"))
    res = m.handle_button(FakeConn(cur), data="pc:approve:pcid-1", presser="999",
                          reviewer_chat="999", file_gate_request=filed.append, now=NOW)
    assert res["ok"] is False
    assert filed == []


def test_ignore_records_the_decision_and_files_nothing():
    m = pc()
    filed = []
    cur = FakeCursor(row=LIVE_ROW)
    res = m.handle_button(FakeConn(cur), data="pc:ignore:pcid-1", presser="999",
                          reviewer_chat="999", file_gate_request=filed.append, now=NOW)
    assert res["ok"] is True
    assert filed == []
    updated = [s for s in cur.statements() if s.startswith("UPDATE tg_pending_contacts")]
    assert updated and "ignored" in updated[0]


# ── the gate request an Approve tap files ───────────────────────────────────

REQ = {"chat_id": "777", "channel_tag": "coffeemedia", "first_name": "Afiq",
       "username": "afiq", "pending_id": "pcid-1",
       "powers": ["reply_text"], "phase": "supervised", "audience": "client"}


def test_gate_request_body_carries_the_contact_and_the_least_privilege_defaults():
    m = pc()
    body = m.gate_request_body(REQ)
    assert "chat_id=777" in body and "Afiq" in body and "coffeemedia" in body
    assert "reply_text" in body and "supervised" in body
    assert "nothing is enabled" in body.lower()
    assert "\n" not in body            # one line: the bus guard's inline shape


def test_gate_request_files_one_bus_row_to_the_console_via_bus_send():
    m = pc()
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "SENT", "stderr": ""})()

    m.file_gate_request_via_bus(REQ, runner=runner)
    assert len(seen) == 1
    cmd = seen[0]
    assert "bus_send.py" in cmd[1]
    assert cmd[cmd.index("--to") + 1] == "orch-console"
    assert cmd[cmd.index("--priority") + 1] == "P2"
    body = cmd[cmd.index("--body") + 1]
    assert "chat_id=777" in body
    # the body must travel inline, never via a file flag
    assert not any(arg.startswith("--body-") for arg in cmd)
