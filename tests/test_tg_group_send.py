"""tg_group_send — send-reliability for the CLIENT-facing dev-group Telegram sender (Nazim 38090/
38094, from cc-irsyad-coord). The 20s single-shot with no retry read-timed-out on a partner group.

The NO-DUPE contract (gate-confirmed 38094 — no-dupe > auto-recovery for a partner channel):
  * PRE-ACK failure (the TCP connection never established — connect timeout / refused / DNS): Telegram
    provably never received the request, so retrying CANNOT double-post. Retry with backoff.
  * AMBIGUOUS failure (connected, then a timeout while sending/awaiting the response): Telegram MAY
    have processed it. NEVER auto-retry — a doubled message to a client is worse than a loud fail.
  * SUCCESS: exactly ONE send.

These lock the classification (send_via_http) and the retry policy (send_with_retry) — the two pieces
Nazim said he'd re-verify (that a read-phase timeout is classified post-ack, not retried).
"""
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import tg_group_send as tg  # noqa: E402


# ── send_with_retry: retry ONLY pre-ack, never ambiguous ─────────────────────────────────────
def test_success_first_try_is_exactly_one_send():
    calls = []
    def send_fn():
        calls.append(1)
        return {"ok": True, "result": {"message_id": 4407}}
    sleeps = []
    out = tg.send_with_retry(send_fn, attempts=3, base_backoff=1.0, sleep=sleeps.append)
    assert out["ok"] is True
    assert len(calls) == 1 and sleeps == []  # one send, no backoff


def test_preack_retries_with_backoff_then_succeeds():
    seq = [tg.PreAckError("conn refused"), tg.PreAckError("conn timeout"), {"ok": True}]
    calls = []
    def send_fn():
        calls.append(1)
        v = seq[len(calls) - 1]
        if isinstance(v, Exception):
            raise v
        return v
    sleeps = []
    out = tg.send_with_retry(send_fn, attempts=3, base_backoff=1.0, sleep=sleeps.append)
    assert out["ok"] is True
    assert len(calls) == 3          # two failures + success
    assert sleeps == [1.0, 2.0]     # exponential backoff between the three attempts


def test_preack_exhausts_all_attempts_then_raises():
    calls = []
    def send_fn():
        calls.append(1)
        raise tg.PreAckError("never connected")
    sleeps = []
    with pytest.raises(tg.PreAckError):
        tg.send_with_retry(send_fn, attempts=3, base_backoff=1.0, sleep=sleeps.append)
    assert len(calls) == 3          # exactly `attempts` tries
    assert sleeps == [1.0, 2.0]     # backed off between them, not after the last


def test_ambiguous_NEVER_retries_and_raises_immediately():
    calls = []
    def send_fn():
        calls.append(1)
        raise tg.AmbiguousSendError("read timeout after request sent")
    sleeps = []
    with pytest.raises(tg.AmbiguousSendError):
        tg.send_with_retry(send_fn, attempts=3, base_backoff=1.0, sleep=sleeps.append)
    assert len(calls) == 1 and sleeps == []  # the whole point: one send, no retry


# ── send_via_http: connect-phase == pre-ack, anything after == ambiguous ──────────────────────
class _FakeConn:
    def __init__(self, *, connect_exc=None, response_exc=None, response=None):
        self._connect_exc, self._response_exc, self._response = connect_exc, response_exc, response
        self.sock = type("S", (), {"settimeout": lambda self, t: None})()
        self.closed = False
    def connect(self):
        if self._connect_exc:
            raise self._connect_exc
    def request(self, *a, **k):
        pass
    def getresponse(self):
        if self._response_exc:
            raise self._response_exc
        return self._response
    def close(self):
        self.closed = True


class _FakeResp:
    def __init__(self, body: bytes, status=200):
        self._body, self.status = body, status
    def read(self):
        return self._body


def _factory(conn):
    return lambda host, timeout: conn


def test_connect_timeout_is_preack():
    conn = _FakeConn(connect_exc=socket.timeout("connect timed out"))
    with pytest.raises(tg.PreAckError):
        tg.send_via_http(_factory(conn), "api.telegram.org", "/bot/x", b"d", {},
                         connect_timeout=10, read_timeout=60)


def test_connection_refused_is_preack():
    conn = _FakeConn(connect_exc=ConnectionRefusedError("refused"))
    with pytest.raises(tg.PreAckError):
        tg.send_via_http(_factory(conn), "api.telegram.org", "/bot/x", b"d", {},
                         connect_timeout=10, read_timeout=60)


def test_read_timeout_AFTER_connect_is_ambiguous():
    # connected fine, then the response never came — Telegram MAY have processed it.
    conn = _FakeConn(response_exc=socket.timeout("read timed out"))
    with pytest.raises(tg.AmbiguousSendError):
        tg.send_via_http(_factory(conn), "api.telegram.org", "/bot/x", b"d", {},
                         connect_timeout=10, read_timeout=60)


def test_success_returns_parsed_body():
    conn = _FakeConn(response=_FakeResp(b'{"ok": true, "result": {"message_id": 4407}}'))
    out = tg.send_via_http(_factory(conn), "api.telegram.org", "/bot/x", b"d", {},
                           connect_timeout=10, read_timeout=60)
    assert out["ok"] is True and out["result"]["message_id"] == 4407
    assert conn.closed is True  # socket always closed


def test_telegram_ok_false_is_definitive_not_ambiguous():
    # a COMPLETED response with ok=false (e.g. bad chat_id) means Telegram received + rejected it —
    # definitive, must NOT be retried (it's a TelegramError, not a PreAckError).
    conn = _FakeConn(response=_FakeResp(b'{"ok": false, "description": "chat not found"}'))
    with pytest.raises(tg.TelegramError):
        tg.send_via_http(_factory(conn), "api.telegram.org", "/bot/x", b"d", {},
                         connect_timeout=10, read_timeout=60)


# ── the ambiguous failure must be ACTIONABLE, not just loud (Nazim 38094 req 3) ──────────────
def test_ambiguous_message_names_exactly_what_to_check():
    msg = tg._actionable_ambiguous_message(tag="cosem-exams", chat_id="-100123", text="hi client")
    assert "operator_messages" in msg          # the logged-send check
    assert "-100123" in msg                     # the partner group to eyeball
    assert "resend" in msg.lower()              # frames it as a human resend decision
    assert "hi client" in msg                   # the actual text, so the human can eyeball it
