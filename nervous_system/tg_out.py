"""tg_out.py — the ONE outbound Telegram gateway (CAI-RESP-357).

Replaces the 8-script send sprawl (tg_send / tg_send_file / *_mamadah / cai_send /
irsyad_support_send / _tg_chunked_send): outbound = a row in the `tg_out` queue
(migration 014); this gateway drains it with chunking, retries, and durable
audit logging — at-least-once outbound, symmetric with inbound ingest.

Enqueue (from any agent/process, incl. headless):
    from nervous_system import tg_out
    tg_out.enqueue("operator-orch", "message text")             # default chat
    tg_out.enqueue("mamadah", "answer", chat_id=12345)          # explicit chat
CLI (drop-in for the send scripts during migration):
    .venv/bin/python3 -m nervous_system.tg_out send <channel_key> "text"
Daemon (launchd dev.wingmen.tg-out):
    .venv/bin/python3 -m nervous_system.tg_out

Delivery: chunked at Telegram's 4096-char limit (the b48bfe5 lesson), token via
the channel's token_env_key (never argv, never the DB), MAX_ATTEMPTS then 'dead'
(never silently dropped — dead rows are a watchdog surface). Every delivered
chunk-set is logged once, full text, to operator_messages (outbound, channel_tag).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

CHUNK = 4096
MAX_ATTEMPTS = 5
DRAIN_INTERVAL = 2          # seconds between queue polls
RETRY_BACKOFF = 30          # seconds a failed row waits before re-attempt


def _dsn() -> str:
    return (os.environ.get("INGEST_DSN")
            or os.environ.get("DATABASE_URL")
            or os.environ.get("SUPABASE_DB_URL"))


def _log_line(msg: str) -> None:
    print(f"[tg_out] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def chunk_text(text: str, limit: int = CHUNK) -> list[str]:
    """Split at the limit, preferring newline/space boundaries (never mid-word
    when a boundary exists in the tail 10% of the window)."""
    out = []
    while text:
        if len(text) <= limit:
            out.append(text)
            break
        window = text[:limit]
        cut = max(window.rfind("\n"), window.rfind(" "))
        if cut < limit * 0.9:
            cut = limit
        out.append(text[:cut])
        text = text[cut:].lstrip("\n ")
    return out


def enqueue(channel_key: str, text: str | None = None, chat_id: int | None = None,
            file_path: str | None = None, reply_to: int | None = None) -> int:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
        cur.execute(
            "INSERT INTO tg_out (channel_key, chat_id, text, file_path, reply_to_operator_msg_id) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (channel_key, chat_id, text, file_path, reply_to),
        )
        rid = cur.fetchone()[0]
        conn.commit()
        return rid


# ── Delivery ──────────────────────────────────────────────────────────────────

def _resolve(conn, channel_key: str) -> tuple[str, int | None, str]:
    """(token, default_chat_id, channel_tag) for a channel row."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT token_env_key, allowed_chat_ids, channel_tag FROM bot_channels "
            "WHERE channel_key=%s", (channel_key,))
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"unknown channel '{channel_key}'")
    token_env, allowed, tag = row
    token = os.environ.get(token_env or "", "")
    if not token:
        raise LookupError(f"token env '{token_env}' empty for '{channel_key}'")
    default_chat = None
    op = os.environ.get("MUSA_TELEGRAM_ID")
    if op and int(op) in (allowed or []):
        default_chat = int(op)
    elif allowed:
        default_chat = allowed[0]
    return token, default_chat, tag


def _send_text(token: str, chat_id: int, text: str) -> None:
    for part in chunk_text(text):
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": part}).encode()
        with urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data),
            timeout=30,
        ) as r:
            payload = json.loads(r.read().decode())
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "sendMessage failed"))


def _send_file(token: str, chat_id: int, path: str) -> None:
    # multipart upload via the same stdlib idiom used by tg_send_file.sh's helper
    import mimetypes, uuid
    boundary = uuid.uuid4().hex
    fname = os.path.basename(path)
    ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        blob = f.read()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{fname}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "sendDocument failed"))


def deliver_one(conn, row: tuple) -> None:
    rid, channel_key, chat_id, text, file_path, attempts = row
    try:
        token, default_chat, tag = _resolve(conn, channel_key)
        target = chat_id or default_chat
        if target is None:
            raise LookupError(f"no chat target for '{channel_key}' (empty allowlist, no override)")
        if text:
            _send_text(token, target, text)
        if file_path:
            _send_file(token, target, file_path)
        with conn.cursor() as cur:
            cur.execute("UPDATE tg_out SET status='sent', sent_at=now(), attempts=%s WHERE id=%s",
                        (attempts + 1, rid))
            cur.execute(
                "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered) "
                "VALUES ('outbound','telegram',%s,%s,%s,true)",
                (str(target), tag, text or f"[file] {file_path}"),
            )
        conn.commit()
    except Exception as e:
        status = "dead" if attempts + 1 >= MAX_ATTEMPTS else "failed"
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tg_out SET status=%s, attempts=%s, last_error=%s WHERE id=%s",
                (status, attempts + 1, f"{type(e).__name__}: {e}", rid))
        conn.commit()
        _log_line(f"row {rid} -> {status}: {type(e).__name__}: {e}")


def drain_once(conn) -> int:
    """One queue pass. 'failed' rows re-enter after RETRY_BACKOFF; 'dead' never."""
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
        cur.execute(
            "UPDATE tg_out SET status='queued' "
            "WHERE status='failed' AND created_at < now() - make_interval(secs => %s)",
            (RETRY_BACKOFF,))
        # Crash recovery: a drainer that died mid-delivery leaves rows stuck in
        # 'sending' — re-queue them after 2 minutes (at-least-once beats a loss).
        cur.execute(
            "UPDATE tg_out SET status='queued' "
            "WHERE status='sending' AND created_at < now() - interval '120 seconds'")
        # Atomic claim (FOR UPDATE SKIP LOCKED): the CLI one-shot drain and the
        # daemon drain can run concurrently — without claiming, both pick the
        # same queued row and the operator gets every message twice.
        cur.execute(
            "UPDATE tg_out SET status='sending' WHERE id IN ("
            "  SELECT id FROM tg_out WHERE status='queued' "
            "  ORDER BY created_at LIMIT 20 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, channel_key, chat_id, text, file_path, attempts")
        rows = cur.fetchall()
    conn.commit()
    for row in rows:
        deliver_one(conn, row)
    return len(rows)


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "send":
        rid = enqueue(sys.argv[2], sys.argv[3])
        with psycopg.connect(_dsn()) as conn:
            drain_once(conn)
            # The daemon drain may have claimed the row first — wait for a
            # terminal status rather than misreporting a race as failure.
            status, err = "queued", None
            for _ in range(15):
                with conn.cursor() as cur:
                    cur.execute("SELECT status, last_error FROM tg_out WHERE id=%s", (rid,))
                    status, err = cur.fetchone()
                if status in ("sent", "dead"):
                    break
                time.sleep(1)
        print(f"tg_out #{rid}: {status}" + (f" ({err})" if err else ""))
        return 0 if status == "sent" else 1
    _log_line("outbound gateway daemon starting")
    while True:
        try:
            with psycopg.connect(_dsn()) as conn:
                drain_once(conn)
        except psycopg.Error as e:
            _log_line(f"WATCHDOG: substrate DB unreachable from tg_out ({type(e).__name__}) — queue held")
        time.sleep(DRAIN_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
