#!/usr/bin/env python3
"""tg_group_send — reliable, NO-DUPE Telegram send for the CLIENT-facing dev-group sender.

Replaces dev_group_send.sh's inline 20s single-shot urlopen (no retry), which read-timed-out on a
partner group (Nazim 38090). The contract, gate-confirmed 38094 (no-dupe > auto-recovery for a
partner channel — a doubled message to a client is worse than a loud fail):

  * PRE-ACK failure — the TCP connection never established (connect timeout / refused / DNS). Telegram
    provably never received the request, so a retry CANNOT double-post. -> retry with exponential backoff.
  * AMBIGUOUS failure — connected, then a timeout while sending or awaiting the response. Telegram MAY
    have processed it. -> NEVER auto-retry; fail LOUD with an ACTIONABLE message (what to check before
    any manual resend), so the human decision is a 10-second check, not a guess.
  * SUCCESS -> exactly ONE send.

The token is resolved from .env and NEVER printed or written to the DB (unchanged discipline).
"""
from __future__ import annotations

import json
import socket


class SendError(Exception):
    """Base for send outcomes the caller must handle."""


class PreAckError(SendError):
    """The request provably never reached Telegram (connection never established). Safe to retry."""


class AmbiguousSendError(SendError):
    """Connected, then timed out sending/awaiting the response — Telegram MAY have processed it.
    Retrying risks a double-post to the partner group, so we never do. The caller must surface this
    loudly and actionably; a human decides whether the message actually landed before any resend."""


class TelegramError(SendError):
    """Telegram returned a completed HTTP response with ok=false (e.g. bad chat_id). Definitive —
    Telegram received and rejected it, so this is NOT a timeout and is never retried."""


def send_with_retry(send_fn, *, attempts: int = 3, base_backoff: float = 1.0, sleep=None):
    """Call send_fn() (one full send attempt) with the NO-DUPE retry policy.

    Retries ONLY PreAckError (provably-not-delivered) up to `attempts` total tries, sleeping
    base_backoff * 2**i between them. AmbiguousSendError and TelegramError are re-raised IMMEDIATELY
    (never retried — they may have landed / are definitive). Returns send_fn()'s value on success.
    `sleep` is injected for tests; defaults to time.sleep."""
    if sleep is None:
        import time
        sleep = time.sleep
    last: Exception | None = None
    for i in range(attempts):
        try:
            return send_fn()
        except PreAckError as e:
            last = e
            if i < attempts - 1:            # back off between tries, not after the last
                sleep(base_backoff * (2 ** i))
        # AmbiguousSendError / TelegramError propagate immediately — no retry (NO-DUPE).
    assert last is not None
    raise last


def send_via_http(conn_factory, host: str, path: str, body: bytes, headers: dict,
                  *, connect_timeout: float, read_timeout: float) -> dict:
    """ONE send attempt over HTTPS with a SPLIT timeout so the phase is knowable — urllib's single
    timeout cannot tell connect from read. `conn_factory(host, timeout=...)` returns an
    http.client.HTTPSConnection-like object (injected for tests).

    connect() failing (timeout/refused/DNS) -> PreAckError (never reached Telegram).
    ANY failure after connect() succeeds (request write / getresponse read timeout, dropped socket)
    -> AmbiguousSendError (the bytes may have reached Telegram). A completed HTTP response is parsed
    and returned; ok=false -> TelegramError. This is the pre-ack/post-ack split Nazim re-verifies."""
    conn = conn_factory(host, timeout=connect_timeout)
    try:
        try:
            conn.connect()
        except (socket.timeout, TimeoutError, ConnectionRefusedError, socket.gaierror, OSError) as e:
            # Connection never established -> Telegram provably did not receive the request.
            raise PreAckError(f"connect failed (pre-ack, safe to retry): {e!r}") from e

        # From here the socket is up: a timeout could mean the request bytes already reached
        # Telegram, so every failure below is AMBIGUOUS and must NOT auto-retry.
        conn.sock.settimeout(read_timeout)
        try:
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        except (socket.timeout, TimeoutError, OSError) as e:
            raise AmbiguousSendError(
                f"connected but timed out sending/awaiting response — the message MAY have been "
                f"delivered (do NOT auto-retry): {e!r}") from e
    finally:
        try:
            conn.close()
        except Exception:
            pass

    try:
        parsed = json.loads(raw)
    except Exception as e:
        # We got SOME response but it isn't JSON — Telegram received the request, so ambiguous
        # rather than pre-ack; never auto-retry.
        raise AmbiguousSendError(f"non-JSON response after send: {e!r}; raw={raw[:200]!r}") from e
    if not parsed.get("ok"):
        raise TelegramError(f"telegram rejected the send: {parsed.get('description')!r}")
    return parsed


# ── CLI: resolve channel -> token/chat_id, send with the NO-DUPE policy, log, report ──────────

def _actionable_ambiguous_message(tag: str, chat_id: str, text: str) -> str:
    preview = (text[:60] + "…") if len(text) > 60 else text
    return (
        "AMBIGUOUS SEND — the request reached the network but Telegram's response was lost; the "
        "message MAY OR MAY NOT have been delivered to the partner group. DO NOT blindly resend "
        "(a doubled message to a client is hard to walk back).\n"
        "BEFORE any manual resend, do this 10-second check:\n"
        f"  1. Open the partner group (chat {chat_id}) — is this text already the latest message? "
        f"    text: {preview!r}\n"
        f"  2. Check operator_messages for a logged outbound row: "
        f"     SELECT id, created_at, left(text,60) FROM operator_messages "
        f"WHERE direction='outbound' AND tag='{tag}' ORDER BY created_at DESC LIMIT 3;\n"
        "  Resend ONLY if BOTH show it did NOT land."
    )


def main(argv=None) -> int:
    import os
    import re
    import sys
    from pathlib import Path

    import psycopg

    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print("usage: tg_group_send.py <channel_key> <text>", file=sys.stderr)
        return 1
    channel, text = argv[0], argv[1]

    orch_dir = Path(__file__).resolve().parent.parent.parent  # scripts/lib -> orchestrator
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 1

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT token_env_key, allowed_chat_ids, channel_tag "
                    "FROM bot_channels WHERE channel_key=%s", (channel,))
        row = cur.fetchone()
        if not row:
            print(f"no such channel: {channel}", file=sys.stderr)
            return 1
        token_env_key, chat_ids, tag = row
        if not chat_ids:
            print(f"channel {channel} has no allowed_chat_ids yet", file=sys.stderr)
            return 1
        chat_id = str(chat_ids[0])

        env = (orch_dir / ".env").read_text()
        m = re.search(r'^%s=(.+)$' % re.escape(token_env_key), env, re.M)
        if not m:
            print(f"token {token_env_key} not in .env", file=sys.stderr)
            return 1
        tok = m.group(1).strip()  # NEVER printed / never written to the DB

        import http.client
        import urllib.parse
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        path = f"/bot{tok}/sendMessage"

        def one_send():
            return send_via_http(http.client.HTTPSConnection, "api.telegram.org", path, body,
                                 headers, connect_timeout=10, read_timeout=60)

        try:
            send_with_retry(one_send, attempts=3, base_backoff=1.0)
        except PreAckError as e:
            # Never delivered — safe to re-run the whole command.
            print(f"SEND FAILED (pre-ack, nothing delivered — safe to re-run): {e}", file=sys.stderr)
            return 1
        except AmbiguousSendError as e:
            print(_actionable_ambiguous_message(tag, chat_id, text), file=sys.stderr)
            print(f"(detail: {e})", file=sys.stderr)
            return 2
        except TelegramError as e:
            print(f"TELEGRAM REJECTED (definitive, not delivered): {e}", file=sys.stderr)
            return 1

        # Delivered exactly once -> durable outbound log. This row is not optional bookkeeping:
        # the exit-2 AMBIGUOUS recovery path (see _actionable_ambiguous_message) tells the human to
        # check operator_messages for a logged send before resending, so a SUCCESSFUL send MUST keep
        # writing this row — drop it and the no-dupe recovery check goes blind (Nazim 38098). The
        # try/except only swallows a LOG failure so it can't turn a delivered send into a false
        # error; it must never become "skip the log".
        try:
            cur.execute(
                "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered, from_name) "
                "VALUES ('outbound', 'telegram', %s, %s, %s, true, 'nazim')",
                (chat_id, tag, text))
        except Exception as e:
            print(f"sent OK but log failed: {e}", file=sys.stderr)
        print(f"sent to {channel} (chat {chat_id})")
        return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
