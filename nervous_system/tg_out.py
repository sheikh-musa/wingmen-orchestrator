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
    .venv/bin/python3 -m nervous_system.tg_out replay-dead      # held_commitment #314
Daemon (launchd dev.wingmen.tg-out):
    .venv/bin/python3 -m nervous_system.tg_out

Delivery: chunked at Telegram's 4096-char limit (the b48bfe5 lesson), token via
the channel's token_env_key (never argv, never the DB), MAX_ATTEMPTS then 'dead'
(never silently dropped — dead rows are a watchdog surface). Every delivered
chunk-set is logged once, full text, to operator_messages (outbound, channel_tag).

Dead-row consumer (fable-audit-substrate-20261006 finding, held_commitment #314):
'dead' rows used to just accumulate (352 rows, +11/week) with nothing reading
them. replay_once_then_purge() (launchd dev.wingmen.tg-out-dead-replay) gives
each one exactly one more delivery attempt; a success is bookkept identically
to a normal send, a failure deletes the row (another attempt would fail the
same way) and posts one attributable bus note to orch-console recording what
was dropped — so the loss is visible instead of silent, without letting dead
rows pile up forever.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import psycopg
from dotenv import load_dotenv

from nervous_system.secret_redact import redact

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import socket as _socket_ipv4patch
# Force IPv4 for api.telegram.org only — broken IPv6 route to Telegram on this
# network hangs long-polls ~18s before IPv4 fallback (2026-07-03). Postgres/other
# hosts untouched.
_ORIG_GAI = _socket_ipv4patch.getaddrinfo
def _gai_ipv4_tg(host, *a, **k):
    res = _ORIG_GAI(host, *a, **k)
    if isinstance(host, str) and "telegram.org" in host:
        v4 = [r for r in res if r[0] == _socket_ipv4patch.AF_INET]
        return v4 or res
    return res
_socket_ipv4patch.getaddrinfo = _gai_ipv4_tg


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


class CantOpenFileRefusal(ValueError):
    """Raised by enqueue() when a message to a CLIENT channel uses the "I
    can't open your file" framing (orch-console bus #51060, Musa
    op#25437-25440) — the sender must ack the client and route the file to
    orch-console (scripts/stage_client_file.py) instead of saying it can't be
    opened."""


# orch-console bus #51060: refuse "I can't open/read/access/view the file,
# describe it" framing on CLIENT channels — the exact pattern that made Hariz
# ask cc-cosem-exams to describe his own spreadsheet instead of being staged.
# "open" stays its own unconditional alternative (no trailing object required
# -- "can't open it"/"can't open" alone must still be caught); read/access/
# view require the explicit object, covering "the"/"your"/"this"/etc (cc-
# quality bus #51094 BLOCKING #1: the earlier can.?t-only form missed
# "cannot read your file" / "unable to read this document" entirely --
# "cannot"/"unable to" paired only with "open", never with read/access/view).
_CANT_OPEN_FILE_RE = re.compile(
    r"(can.?t|cannot|unable to) open|"
    r"(can.?t|cannot|unable to) (read|access|view) (the |your |this |that |my |our )?"
    r"(file|attachment|spreadsheet|document)|"
    r"describe (it|what it shows)",
    re.IGNORECASE,
)


def _refuse_cant_open_file_framing(cur, channel_key: str, text: str | None) -> None:
    """Scoped to audience='client' channels only — operator/internal channels
    may legitimately need this phrasing (e.g. reporting a download failure to
    the fleet). Looked up fresh per call (not cached) since a channel's
    audience can change."""
    if not text or not _CANT_OPEN_FILE_RE.search(text):
        return
    cur.execute("SELECT audience FROM bot_channels WHERE channel_key=%s", (channel_key,))
    row = cur.fetchone()
    if row and row[0] == "client":
        raise CantOpenFileRefusal(
            f"refusing to enqueue to client channel '{channel_key}': the message matches the "
            "\"can't open/read/access the file\" framing. Ack the client and route the file to "
            "orch-console (scripts/stage_client_file.py) instead — never tell a client you "
            "can't open their file."
        )


class DateWeekdayRefusal(ValueError):
    """Raised by enqueue() when the text pairs a weekday with the wrong date
    ("Thursday 9 October" when 9 Oct 2026 is a Friday — orch-console #57970/#58047).
    Only a CONFIRMED mismatch raises: a guard crash fails OPEN + pages (#58089)."""


_GUARD_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "lib")
_GUARD_PATH = os.path.join(_GUARD_LIB, "date_weekday_guard.py")
_PAGER_PATH = os.path.join(_GUARD_LIB, "weekday_guard_pager.py")


def _load_by_path(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # @dataclass resolves its module via sys.modules
    spec.loader.exec_module(mod)
    return mod


def _refuse_weekday_date_mismatch(text: str | None, source: str = "tg_out.enqueue") -> None:
    if not text:
        return
    try:
        refusal = _load_by_path(_GUARD_PATH, "_date_weekday_guard").refusal_text(text)
    except Exception as e:  # noqa: BLE001 — fail OPEN + page, never block the enqueue
        import traceback
        tb = traceback.format_exc()
        err = f"{type(e).__name__}: {e}"
        print(f"WARNING: weekday/date guard CRASHED in {source} ({err}) — sending UNGUARDED; "
              "paging orch-console.", file=sys.stderr)
        try:
            _load_by_path(_PAGER_PATH, "_weekday_guard_pager").page_guard_crash(source, err, tb)
        except BaseException as pe:  # noqa: BLE001
            print(f"WARNING: could not page orch-console about the guard crash "
                  f"({type(pe).__name__}: {pe}); sending anyway.", file=sys.stderr)
        return
    if refusal:
        raise DateWeekdayRefusal(refusal)


def enqueue(channel_key: str, text: str | None = None, chat_id: int | None = None,
            file_path: str | None = None, reply_to: int | None = None) -> int:
    _refuse_weekday_date_mismatch(text)
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
        _refuse_cant_open_file_framing(cur, channel_key, text)
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
    # Scrub secret patterns BEFORE the Telegram API send (redact the full text
    # before chunking so a secret straddling a chunk boundary can't slip). Clean
    # text is unchanged.
    text = redact(text)
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
        # Stale-ack guard (2026-07-10, operator-flagged): a "📨 Got your message"
        # reassurance/busy-ack is only meaningful while something is still
        # unhandled. It rides this async queue, so a fast DIRECT reply
        # (tg_send.sh) can overtake it — the ack then lands AFTER the real answer,
        # which reads as noise ("you already replied, why say you'll get to it?").
        # So at SEND time, if this channel's tag has NO unhandled inbound left,
        # DROP the ack instead of sending it. Only the ack strings are gated;
        # real replies (any other text) always send.
        if text and text.startswith("\U0001F4E8 Got your message"):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM operator_messages "
                    "WHERE direction='inbound' AND tag=%s AND handled_at IS NULL LIMIT 1",
                    (tag,))
                if not cur.fetchone():
                    cur.execute(
                        "UPDATE tg_out SET status='skipped', sent_at=now(), "
                        "last_error='stale-ack: channel already answered' WHERE id=%s",
                        (rid,))
                    conn.commit()
                    _log_line(f"row {rid} SKIPPED stale ack on '{channel_key}' (already answered)")
                    return
        target = chat_id or default_chat
        if target is None:
            raise LookupError(f"no chat target for '{channel_key}' (empty allowlist, no override)")
        # Deliver text and file as SEPARATE, idempotent-per-part steps. Bug this
        # guards (2026-08-07, operator-flagged op#11218 — HK-hero message delivered
        # 11x): text + file rode ONE all-or-nothing retry, so when _send_file threw
        # (a Mac lane had enqueued a Mac-local file_path the VPS daemon can't open ->
        # FileNotFoundError) AFTER _send_text had already delivered, the whole row
        # retried and RE-SENT the text every attempt (up to MAX_ATTEMPTS). Fix: the
        # instant text is delivered, NULL it in the queue + log it, so no subsequent
        # retry (for the file) can ever re-deliver it.
        if text:
            _send_text(token, target, text)
            with conn.cursor() as cur:
                cur.execute("UPDATE tg_out SET text=NULL WHERE id=%s", (rid,))
                cur.execute(
                    "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered) "
                    "VALUES ('outbound','telegram',%s,%s,%s,true)",
                    (str(target), tag, text))
            conn.commit()
            text = None  # in-memory guard too — nothing below can re-send it
        if file_path:
            _send_file(token, target, file_path)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered) "
                    "VALUES ('outbound','telegram',%s,%s,%s,true)",
                    (str(target), tag, f"[file] {file_path}"))
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("UPDATE tg_out SET status='sent', sent_at=now(), attempts=%s WHERE id=%s",
                        (attempts + 1, rid))
        conn.commit()
    except FileNotFoundError as e:
        # A missing file will NOT reappear on this host (typical cause: a cross-host
        # local path enqueued by a Mac lane, unreadable by the VPS daemon). Retrying
        # only churns to 'dead' AND — pre-fix — re-sent the text; mark it dead NOW.
        # Any text was already delivered + logged + nulled above, so this drops ONLY
        # the unreachable file (a dead-letter the watchdog surfaces), never text.
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tg_out SET status='dead', attempts=%s, last_error=%s WHERE id=%s",
                (attempts + 1, f"FileNotFoundError (non-retriable): {e}", rid))
        conn.commit()
        _log_line(f"row {rid} -> dead (missing file, non-retriable): {e}")
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


# ── Dead-row consumer (held_commitment #314) ──────────────────────────────────

_DEAD_REPLAY_LOCK_KEY = 0x74675F6F  # arbitrary; just needs to be stable

# orch-console change-request (bus #69991, after mig107's read-back): the
# first real run found 353 dead rows spanning 2026-07-08..10-09, 290 of them
# on nazim-console -- whose token (@nazim_cto_bot) resolves LIVE today, so a
# bare replay-once would succeed and re-deliver ~290 three-month-old messages
# to the operator's phone (plus 290 stale operator_messages audit rows).
# replay-once is correct for a FRESH transient failure, not a backlog that
# accumulated because the daemon was never running this consumer — a row
# older than this cap is purged WITHOUT a replay attempt (still gets the
# attributable bus note) so the operator is never re-sent a stale backlog.
DEAD_ROW_AGE_CAP_HOURS = 48


def replay_once_then_purge(conn) -> dict:
    """A 'dead' row (MAX_ATTEMPTS exhausted) used to just sit there forever —
    nothing consumed it (352 rows, +11/week, fable-audit-substrate-20261006).
    Give each one exactly one more delivery attempt: success is bookkept the
    same as a normal send ('sent' + the operator_messages audit row); failure
    deletes the row (a further attempt would fail the same way) and posts one
    attributable bus note to orch-console recording what was dropped, so the
    loss is visible instead of silent. A row older than DEAD_ROW_AGE_CAP_HOURS
    skips the replay attempt entirely (see the constant's comment) — it is
    purged the same way a failed replay would be, just without ever dialing
    Telegram.

    Single-flight via a session advisory lock: this runs as its own
    low-frequency pass (CLI / launchd), not inside drain_once's hot loop, and
    'dead' has no atomic-claim status of its own (migration 015's CHECK only
    allows queued/sending/sent/failed/dead) — the lock is what keeps two
    overlapping runs from each grabbing the same row.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_DEAD_REPLAY_LOCK_KEY,))
        got = cur.fetchone()[0]
    if not got:
        _log_line("replay_once_then_purge: another run holds the lock, skipping")
        return {"replayed": 0, "purged": 0, "total": 0}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
            cur.execute(
                "SELECT id, channel_key, chat_id, text, file_path, created_at, "
                "now() - created_at > make_interval(hours => %s) AS is_stale "
                "FROM tg_out WHERE status='dead' ORDER BY id",
                (DEAD_ROW_AGE_CAP_HOURS,))
            rows = cur.fetchall()
        conn.commit()

        replayed = purged = 0
        # orch-console finding (bus #70262): a bulk age-cap drain can purge
        # hundreds of rows in one pass -- collect those into stale_batch and
        # post ONE summary bus note after the loop instead of one per row.
        # A fresh-failure purge (is_stale=False) is rare and keeps its own
        # immediate note.
        stale_batch: list[dict] = []
        for rid, channel_key, chat_id, text, file_path, created_at, is_stale in rows:
            if _replay_one(conn, rid, channel_key, chat_id, text, file_path, is_stale,
                            created_at=created_at, stale_batch=stale_batch):
                replayed += 1
            else:
                purged += 1
        if stale_batch:
            _post_stale_batch_summary(stale_batch)
        return {"replayed": replayed, "purged": purged, "total": len(rows)}
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_DEAD_REPLAY_LOCK_KEY,))
        conn.commit()


def _replay_one(conn, rid, channel_key, chat_id, text, file_path, is_stale: bool = False,
                 created_at=None, stale_batch: list | None = None) -> bool:
    """One more delivery attempt for a dead row — UNLESS it is past the age
    cap, in which case it is purged WITHOUT ever attempting delivery (a
    months-old row re-sending to a LIVE channel is the harm this guards
    against; the bus note still records what was dropped). True on success
    ('sent'), False on failure or age-cap purge (row deleted + bus note
    posted, or appended to stale_batch for a batched summary note — see
    _purge_dead_row)."""
    if is_stale:
        _log_line(f"dead row {rid} purged (age cap: older than "
                  f"{DEAD_ROW_AGE_CAP_HOURS}h, NOT replayed — backlog, not a fresh failure)")
        _purge_dead_row(conn, rid, channel_key, text, file_path, None, stale=True,
                         created_at=created_at, stale_batch=stale_batch)
        return False
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
            cur.execute(
                "UPDATE tg_out SET status='sent', sent_at=now(), attempts=attempts+1 WHERE id=%s",
                (rid,))
            cur.execute(
                "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered) "
                "VALUES ('outbound','telegram',%s,%s,%s,true)",
                (str(target), tag, text or f"[file] {file_path}"),
            )
        conn.commit()
        _log_line(f"dead row {rid} REPLAYED ok on '{channel_key}'")
        return True
    except Exception as e:
        _log_line(f"dead row {rid} purged (replay failed: {type(e).__name__}: {e})")
        _purge_dead_row(conn, rid, channel_key, text, file_path, e)
        return False


def _purge_dead_row(conn, rid, channel_key, text, file_path, err, stale: bool = False,
                     created_at=None, stale_batch: list | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")
        cur.execute("DELETE FROM tg_out WHERE id=%s", (rid,))
    conn.commit()

    if stale and stale_batch is not None:
        # Bulk age-cap drain (orch-console finding, bus #70262): don't send a
        # bus note per row here -- the caller (replay_once_then_purge) collects
        # these and posts ONE summary note after the whole pass, so a
        # hundreds-of-rows backlog drain doesn't flood orch-console's inbox
        # with near-identical receipts. The row is still deleted above.
        stale_batch.append({"rid": rid, "channel_key": channel_key, "created_at": created_at})
        return

    snippet = (text or (f"[file] {file_path}" if file_path else "") or "")[:300]
    if stale:
        detail = (f"older than the {DEAD_ROW_AGE_CAP_HOURS}h age cap, so this pass "
                  "purged it WITHOUT attempting a replay (orch-console change-request "
                  "bus #69991 — a dead row this old is backlog accumulation, not a "
                  "fresh transient failure; replaying it risks re-sending a months-old "
                  "message to a LIVE channel)")
    else:
        detail = (f"exhausted MAX_ATTEMPTS, then failed its one replay.\n"
                  f"Replay error: {type(err).__name__}: {err}")
    body = (
        f"tg_out row #{rid} on channel '{channel_key}' never delivered ({detail}); "
        "purged by the tg_out.dead consumer (held_commitment #314).\n\n"
        f"Content snippet:\n{snippet}"
    )

    # CLAUDE.md forbids a hand-written `INSERT INTO agent_messages` (bus #43651).
    # This purge note goes through scripts/bus_send.py's send() instead, with
    # from_agent='tg-out-dead-replay' — a dedicated identity (migration 107),
    # not the hub's 'cc-orchestrator': this runs as a bare launchd daemon on the
    # Mac Mini with no AGENT_ID/CC_BASE_AGENT_ID in its environment, so it can't
    # resolve a live session identity, and 'cc-orchestrator' names the VPS hub
    # process (ORCH-TOPOLOGY-001), not this daemon. Mirrors the existing
    # 'sla-watchdog' / 'ddl-coverage-watchdog' / 'ingest-watchdog' precedent.
    from scripts import bus_send
    bus_send.send(
        from_agent="tg-out-dead-replay", to="orch-console", mtype="update",
        subject=f"tg_out dead row #{rid} purged ('{channel_key}')", body=body,
        priority="P3", req=False, dsn=_dsn(),
    )


def _post_stale_batch_summary(batch: list) -> None:
    """One bus note for an entire age-cap backlog drain, instead of the one-
    per-row receipt _purge_dead_row posts for a (rare) fresh-failure purge.
    orch-console finding, bus #70262: a one-time backlog drain was firing
    hundreds of near-identical P3 receipts, each tripping a reconcile nudge
    and drowning the inbox-signal the nudge exists to protect."""
    channels = sorted({b["channel_key"] for b in batch})
    timestamps = [b["created_at"] for b in batch if b["created_at"] is not None]
    ids = sorted(b["rid"] for b in batch)
    oldest = min(timestamps) if timestamps else "unknown"
    newest = max(timestamps) if timestamps else "unknown"
    id_range = f"{ids[0]}..{ids[-1]}" if ids else "none"
    body = (
        f"tg_out.dead age-cap drain: purged {len(batch)} row(s) in this pass "
        f"WITHOUT attempting a replay (all older than the {DEAD_ROW_AGE_CAP_HOURS}h "
        "age cap — backlog accumulation, not fresh failures; see bus #69991). "
        "replayed=0 for this batch.\n\n"
        f"Channels affected: {', '.join(channels) if channels else 'none'}\n"
        f"Oldest created_at: {oldest}\n"
        f"Newest created_at: {newest}\n"
        f"Row ids: {id_range} ({len(ids)} total)"
    )
    from scripts import bus_send
    bus_send.send(
        from_agent="tg-out-dead-replay", to="orch-console", mtype="update",
        subject=f"tg_out.dead age-cap drain: {len(batch)} row(s) purged (batched receipt, bus #70262)",
        body=body, priority="P3", req=False, dsn=_dsn(),
    )


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
    if len(sys.argv) >= 2 and sys.argv[1] == "replay-dead":
        with psycopg.connect(_dsn()) as conn:
            result = replay_once_then_purge(conn)
        print(f"tg_out replay-dead: {result['replayed']} replayed, "
              f"{result['purged']} purged, {result['total']} total")
        return 0
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
