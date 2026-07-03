"""ingest.py — the ONE unified Telegram ingest daemon (BOT-INGEST-TOPOLOGY-001,
implementing design ratified CAI-RESP-357 w/ amendments A1/A2/A3).

Replaces the per-bot bridge sprawl (tg_bridge / cai_bridge / irsyad_support_bridge
+ the standalone bot pollers): every bot channel is a CONFIG ROW in `bot_channels`
(migration 014), and this single daemon long-polls every enabled channel.

Per inbound update, strictly in this order (A2: transport ONLY — no brains here):
  1. DEDUPE (A1)  — INSERT INTO ingest_dedup ON CONFLICT DO NOTHING; only the
                    winning insert proceeds. At-least-once redelivery (see offset
                    fail-safe below) therefore never double-logs/nudges/replies.
  2. LOG          — durable-first to operator_messages (channel_tag; full text).
                    Nothing else is load-bearing (Option B: the log is the truth).
  3. GATE         — deny-by-default from config: allowed_chat_ids / allowed_usernames.
                    Disallowed => logged-and-skipped (audit stays, no routing).
  4. ROUTE by mode:
       agent-session : NUDGE-ONLY tmux injection — "N unread on <channel>" —
                       never the payload (CAI-RESP-357 supersedes the 150-char
                       preview convention; kills the paste-verify fragility class).
       ai-responder  : nothing extra — the deduped log row IS the queue; the
                       responder_runner drain unit (separate process, A2) picks it
                       up from operator_messages by channel_tag.
       log-and-route : nudge the hub ('orch') with the channel's inject_prefix
                       count line; the hub owns all outbound (perimeter posture,
                       CAI-RESP-332/345 unchanged).

Offset fail-safe (REQUIRED, CAI-RESP-357): poll_offset lives in bot_channels and
is advanced ONLY after a batch is fully processed. If the DB is unreachable the
offset is never acked, Telegram redelivers (~24h retention buffers), and the A1
dedupe absorbs the replay. DB-connect failure is surfaced loudly for the watchdog.

Run: .venv/bin/python3 -m nervous_system.ingest       (launchd: dev.wingmen.ingest)
Env: INGEST_DSN overrides the substrate DSN (tests); tokens resolve via each
     channel's token_env_key from the environment/.env — NEVER from the DB.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

POLL_TIMEOUT = 25          # Telegram long-poll seconds
ERROR_BACKOFF = 5          # seconds after a per-channel error
CONFIG_REFRESH = 60        # seconds between bot_channels re-reads
DB_ALERT_EVERY = 60        # throttle for the watchdog-visible DB-failure line


def _dsn() -> str:
    return (os.environ.get("INGEST_DSN")
            or os.environ.get("DATABASE_URL")
            or os.environ.get("SUPABASE_DB_URL"))


def _log_line(msg: str) -> None:
    print(f"[ingest] {time.strftime('%H:%M:%S')} {msg}", flush=True)


# ── Telegram (stdlib, matching the bridge idiom — no SDK dependency) ──────────

def tg_call(token: str, method: str, params: dict, timeout: int = POLL_TIMEOUT + 10):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
        payload = json.loads(r.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(f"{method}: {payload.get('description', 'not ok')}")
    return payload["result"]


# ── Media (photos / documents) — download to logs/tg_media, log the path ──────
# Parity with the retired tg_bridge: a non-text update (screenshot, PDF, xlsx)
# must be DOWNLOADED and its local path logged, or cc-orchestrator can't Read it.
# Token stays in the download URL only — never logged. Per-channel token (ch.token).

_MEDIA_DIR = os.path.join(os.path.dirname(__file__), "..", "logs", "tg_media")


def _tg_download_file(token: str, file_path: str, dest: str) -> str:
    """Stream api.telegram.org/file/<file_path> -> dest with retry+backoff.
    Telegram's file CDN intermittently resets mid-download (Errno 54); a bare
    fetch silently lost the operator's DPA + ADCDA photos twice (2026-06-28)."""
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    last = None
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, length=65536)
            if os.path.getsize(dest) > 0:
                return dest
            last = "empty download"
        except Exception as e:
            last = e
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass
        if i < 3:
            time.sleep(min(2 ** i, 8))
    raise RuntimeError(f"telegram file download failed after 4 attempts: {last}")


def _download_media(token: str, file_id: str, name: str | None = None) -> str:
    with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}", timeout=30) as r:
        fp = json.load(r)["result"]["file_path"]
    os.makedirs(_MEDIA_DIR, exist_ok=True)
    if name:
        safe = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in name).strip() or "file"
    else:
        safe = fp.replace("/", "_")
    return _tg_download_file(token, fp, os.path.join(_MEDIA_DIR, safe))


def message_content(ch: "Channel", msg: dict, upd_id: int) -> str:
    """Human-readable content for the log row — text, or a downloaded-media
    pointer cc-orchestrator can Read, or a last-resort non-text marker. Reply
    context is preserved so cc-orch knows which message the operator answered."""
    text = msg.get("text") or msg.get("caption") or ""
    if msg.get("photo"):
        try:
            path = _download_media(ch.token, msg["photo"][-1]["file_id"])
            content = f"sent a SCREENSHOT → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            content = f"sent a photo (download failed: {e})" + (f" | {text}" if text else "")
    elif msg.get("document"):
        doc = msg["document"]
        try:
            path = _download_media(ch.token, doc["file_id"], doc.get("file_name"))
            content = f"sent a FILE → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            content = f"sent a file (download failed: {e})" + (f" | {text}" if text else "")
    else:
        content = text or f"[non-text update {upd_id}]"

    # Reply-threading: prepend the quoted message so cc-orch knows the referent
    # (parity with the retired bridge — an operator reply loses its meaning
    # without the thing it replies to).
    rep = msg.get("reply_to_message")
    if rep:
        snip = (rep.get("text") or rep.get("caption") or "[media]").strip().replace("\n", " ")[:80]
        content = f'↩️ re "{snip}": {content}'
    return content


# ── Config ────────────────────────────────────────────────────────────────────

class Channel:
    def __init__(self, row: tuple):
        (self.key, self.token_env_key, self.mode, self.inject_target,
         self.inject_prefix, self.responder_ref, self.allowed_chat_ids,
         self.allowed_usernames, self.group_routing, self.channel_tag,
         self.log_target, self.poll_offset) = row
        self.token = os.environ.get(self.token_env_key or "", "")

    COLS = ("channel_key, token_env_key, mode, inject_target, inject_prefix, "
            "responder_ref, allowed_chat_ids, allowed_usernames, group_routing, "
            "channel_tag, log_target, poll_offset")


def load_channels(conn) -> dict[str, Channel]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {Channel.COLS} FROM bot_channels WHERE enabled")
        return {c.key: c for c in (Channel(r) for r in cur.fetchall())}


# ── Gate (deny-by-default, pure function — unit-tested) ───────────────────────

def gate_allows(ch: Channel, chat_id: int, username: str | None) -> bool:
    """Config-enforced perimeter: empty allowlists accept NOTHING."""
    if chat_id in (ch.allowed_chat_ids or []):
        return True
    if username and username.lstrip("@").lower() in [
        u.lstrip("@").lower() for u in (ch.allowed_usernames or [])
    ]:
        return True
    return False


# ── Route: agent-session / log-and-route => nudge-only tmux line ─────────────

def nudge_session(target: str, channel_key: str, n_unread: int) -> bool:
    """Count-only nudge. NEVER carries payload (CAI-RESP-357). Best-effort
    signal — delivery is guaranteed by the log + reconciliation, not by this."""
    tmux = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
    line = (f"\U0001F4E5 {n_unread} unread on '{channel_key}' — "
            f"reconcile operator_log.unprocessed()")
    try:
        subprocess.run([tmux, "has-session", "-t", f"={target}"],
                       check=True, capture_output=True, timeout=5)
        # '=name:0.0' — exact session match AND explicit pane: tmux 3.7a's
        # send-keys can fail to resolve a bare '=name' to a pane ("can't find
        # pane") even when has-session passes.
        subprocess.run([tmux, "send-keys", "-t", f"={target}:0.0", "-l", line],
                       check=True, capture_output=True, timeout=5)
        time.sleep(1)
        subprocess.run([tmux, "send-keys", "-t", f"={target}:0.0", "Enter"],
                       check=True, capture_output=True, timeout=5)
        return True
    except Exception:
        return False   # headless: the durable log already has it


# ── Per-update processing (sync, called from the channel task) ────────────────

def process_update(conn, ch: Channel, upd: dict) -> bool:
    """A1→LOG→GATE→ROUTE for one getUpdates entry. Returns True if this
    process 'won' the dedupe insert (i.e. the update was newly processed)."""
    upd_id = upd["update_id"]
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    username = (msg.get("from") or {}).get("username")

    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
        # 1. A1 dedupe — the processing gate.
        cur.execute(
            "INSERT INTO ingest_dedup (channel_key, telegram_update_id) "
            "VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING channel_key",
            (ch.key, upd_id),
        )
        if cur.fetchone() is None:
            conn.commit()
            return False          # replay of an already-processed update

        # 2. LOG — durable first, always. Media is downloaded here so the row
        #    carries the local path (screenshot/doc), not a bare non-text marker.
        cur.execute(
            "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered) "
            "VALUES ('inbound','telegram',%s,%s,%s,true) RETURNING id",
            (str(chat_id) if chat_id is not None else None, ch.channel_tag,
             message_content(ch, msg, upd_id)),
        )
        op_msg_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE ingest_dedup SET operator_msg_id=%s "
            "WHERE channel_key=%s AND telegram_update_id=%s",
            (op_msg_id, ch.key, upd_id),
        )
        conn.commit()

    # 3. GATE — deny-by-default; disallowed stays logged-and-skipped.
    if chat_id is None or not gate_allows(ch, chat_id, username):
        _log_line(f"{ch.key}: update {upd_id} gated (chat {chat_id}) — logged, not routed")
        return True

    # 4. ROUTE (transport only — A2).
    if ch.mode == "agent-session":
        nudge_session(ch.inject_target, ch.key, unread_count(conn, ch))
    elif ch.mode == "log-and-route":
        nudge_session("orch", ch.key, unread_count(conn, ch))
    # ai-responder: nothing — responder_runner drains the log (A2).
    return True


def unread_count(conn, ch: Channel) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM operator_messages "
            "WHERE direction='inbound' AND tag=%s AND handled_at IS NULL",
            (ch.channel_tag,),
        )
        return cur.fetchone()[0]


# ── Channel task: long-poll loop w/ DB-held offset ────────────────────────────

async def channel_loop(key: str, channels: dict[str, Channel]):
    last_db_alert = 0.0
    while True:
        ch = channels.get(key)
        if ch is None:            # disabled on refresh → task retires
            _log_line(f"{key}: disabled — loop exiting")
            return
        if not ch.token:
            _log_line(f"{key}: token env '{ch.token_env_key}' empty — sleeping (graceful, no crash-loop)")
            await asyncio.sleep(CONFIG_REFRESH)
            continue
        try:
            params = {"timeout": POLL_TIMEOUT, "allowed_updates": '["message","edited_message"]'}
            if ch.poll_offset is not None:
                params["offset"] = ch.poll_offset + 1
            updates = await asyncio.to_thread(tg_call, ch.token, "getUpdates", params)
            if not updates:
                continue
            with psycopg.connect(_dsn()) as conn:
                for upd in updates:
                    process_update(conn, ch, upd)
                # Offset fail-safe: ack ONLY after the whole batch is durable.
                new_off = updates[-1]["update_id"]
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE bot_channels SET poll_offset=%s, updated_at=now() "
                        "WHERE channel_key=%s", (new_off, ch.key))
                conn.commit()
                ch.poll_offset = new_off
        except psycopg.Error as e:
            # DB unreachable: offset stays un-acked → Telegram will redeliver →
            # A1 dedupe absorbs. Surface loudly for the watchdog (CAI-RESP-357).
            now = time.monotonic()
            if now - last_db_alert > DB_ALERT_EVERY:
                _log_line(f"WATCHDOG: substrate DB unreachable from ingest ({type(e).__name__}: {e}) — channels paused, offsets held")
                last_db_alert = now
            await asyncio.sleep(ERROR_BACKOFF)
        except Exception as e:
            _log_line(f"{key}: loop error {type(e).__name__}: {e}")
            await asyncio.sleep(ERROR_BACKOFF)


async def main():
    tasks: dict[str, asyncio.Task] = {}
    channels: dict[str, Channel] = {}
    _log_line("unified ingest daemon starting")
    while True:
        try:
            with psycopg.connect(_dsn()) as conn:
                fresh = load_channels(conn)
        except psycopg.Error as e:
            _log_line(f"WATCHDOG: cannot read bot_channels ({type(e).__name__}) — retrying")
            await asyncio.sleep(ERROR_BACKOFF)
            continue
        # carry live offsets forward; start/stop tasks to match config
        for k, ch in fresh.items():
            if k in channels and channels[k].poll_offset is not None and (
                    ch.poll_offset is None or ch.poll_offset < channels[k].poll_offset):
                ch.poll_offset = channels[k].poll_offset
        channels.clear()
        channels.update(fresh)
        for k in list(channels):
            if k not in tasks or tasks[k].done():
                tasks[k] = asyncio.create_task(channel_loop(k, channels))
                _log_line(f"{k}: channel task up (mode={channels[k].mode})")
        await asyncio.sleep(CONFIG_REFRESH)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
