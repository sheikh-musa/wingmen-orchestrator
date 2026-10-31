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
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import socket

import psycopg
from dotenv import load_dotenv

from nervous_system import triage  # PASSIVE CoS triage annotation (read-only; no routing)
from nervous_system import operator_log  # op#22669 asks-ledger reply-match (maybe_track_ask)
from nervous_system import personal_routing  # bus #47837: private-family content routing
from nervous_system import pending_contacts  # TG2.0 s6: new-contact alert + one-tap approve
from scripts.lib import fire_window  # quiesce keystrokes during a recycle's fire window
from scripts.lib import pane_busy  # footer-scoped busy check (one implementation)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Force IPv4 for api.telegram.org ONLY. This network's IPv6 route to Telegram is
# broken (2001:67c:4e8:f004::9 times out ~20s on the TLS handshake) while IPv4
# works instantly and IPv6 is fine to everything else. Python prefers the IPv6
# result getaddrinfo returns first, so every long-poll wasted ~18s hanging on the
# dead route before falling back — 83 timeouts/hr and the operator's message lag.
# Leaves Postgres (Supabase is IPv6) and all other hosts untouched (2026-07-03).
_ORIG_GETADDRINFO = socket.getaddrinfo
def _getaddrinfo_ipv4_telegram(host, *args, **kwargs):
    res = _ORIG_GETADDRINFO(host, *args, **kwargs)
    if isinstance(host, str) and "telegram.org" in host:
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return v4 or res
    return res
socket.getaddrinfo = _getaddrinfo_ipv4_telegram

POLL_TIMEOUT = 25          # Telegram long-poll seconds
ERROR_BACKOFF = 5          # seconds after a per-channel error
CONFIG_REFRESH = 60        # seconds between bot_channels re-reads
DB_ALERT_EVERY = 60        # throttle for the watchdog-visible DB-failure line

# Self-hosted Telegram Bot API server support (bus #53128/#53139/#53143).
# STAGED, NOT CUT OVER: unset (default) is byte-identical current behavior
# against the cloud API. When set (e.g. http://127.0.0.1:8081 for a local
# `telegram-bot-api --local` instance on this same host), getFile's
# file_path comes back as an ABSOLUTE LOCAL FILESYSTEM PATH already on disk
# (TDLib local-mode semantics, not a relative CDN path) -- _tg_download_file
# below copies it directly instead of an HTTP round-trip to ourselves. Local
# mode has no cloud 20MB cap (files up to ~2GB), which is the whole point.
TELEGRAM_BOT_API_BASE_URL = os.environ.get("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org").rstrip("/")


def _dsn() -> str:
    return (os.environ.get("INGEST_DSN")
            or os.environ.get("DATABASE_URL")
            or os.environ.get("SUPABASE_DB_URL"))


def _log_line(msg: str) -> None:
    print(f"[ingest] {time.strftime('%H:%M:%S')} {msg}", flush=True)


# ── Telegram (stdlib, matching the bridge idiom — no SDK dependency) ──────────

def tg_call(token: str, method: str, params: dict, timeout: int = POLL_TIMEOUT + 10):
    url = f"{TELEGRAM_BOT_API_BASE_URL}/bot{token}/{method}"
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
    """Stream <base>/file/bot<token>/<file_path> -> dest with retry+backoff.
    Telegram's file CDN intermittently resets mid-download (Errno 54); a bare
    fetch silently lost the operator's DPA + ADCDA photos twice (2026-06-28).

    Opens with 'xb' (O_EXCL), never 'wb': dest is built by the caller from a
    channel+update+file_unique_id key that is unique per actual piece of
    content, so a FileExistsError here means dest already holds THIS file's
    bytes (idempotent re-process or a concurrent download of the identical
    update) — never a different client's content silently clobbered (bus
    #47469: the old basename-only naming let one bot's getFile response
    overwrite another bot's client media under the same name).

    Local-mode short-circuit (TELEGRAM_BOT_API_BASE_URL set): a local
    telegram-bot-api server returns file_path as an ABSOLUTE path already on
    this host's disk, not a relative CDN path -- copy it directly rather than
    HTTP-fetching it from ourselves. Same idempotency as the xb path below:
    a dest that already has bytes is a prior/concurrent run of this exact
    file, never overwritten."""
    if os.path.isabs(file_path) and os.path.exists(file_path):
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        shutil.copy(file_path, dest)
        return dest
    url = f"{TELEGRAM_BOT_API_BASE_URL}/file/bot{token}/{file_path}"
    last = None
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(dest, "xb") as f:
                shutil.copyfileobj(r, f, length=65536)
            if os.path.getsize(dest) > 0:
                return dest
            last = "empty download"
        except FileExistsError:
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                return dest
            last = "dest exists but empty (lost a concurrent-write race)"
        except Exception as e:
            last = e
        try:
            if os.path.exists(dest) and os.path.getsize(dest) == 0:
                os.remove(dest)
        except OSError:
            pass
        if i < 3:
            time.sleep(min(2 ** i, 8))
    raise RuntimeError(f"telegram file download failed after 4 attempts: {last}")


def _media_dest_name(channel_key: str, upd_id: int, file_unique_id: str,
                      file_path: str, name: str | None) -> str:
    """<channel>_<update_id>_<file_unique_id>.<ext> — collision-proof across
    bots (channel_key) and across messages (update_id + file_unique_id, both
    Telegram-assigned and unique per actual file). The old scheme used only
    Telegram's own file_path basename (e.g. photos/file_6.jpg), which is
    numbered PER BOT and let two different clients' bots collide on the same
    local filename (bus #47469)."""
    ext = os.path.splitext(file_path)[1] or (os.path.splitext(name)[1] if name else "")
    safe_channel = "".join(c if (c.isalnum() or c in "._-") else "_" for c in channel_key) or "ch"
    safe_unique = "".join(c if (c.isalnum() or c in "._-") else "_" for c in file_unique_id)
    return f"{safe_channel}_{upd_id}_{safe_unique}{ext}"


# getFile had NO retry (orch-console #77889, 2026-10-27): both of today's lost operator photos
# (updates 440382277 / 440382285) failed on a single Errno 54 reset at this call, before the
# retrying file download ever ran. Same schedule as _tg_chunked_send.RETRY_DELAYS.
GETFILE_RETRY_DELAYS = (2, 6, 15)


def _get_file_path(token: str, file_id: str, sleep=time.sleep) -> str:
    url = f"{TELEGRAM_BOT_API_BASE_URL}/bot{token}/getFile?file_id={file_id}"
    for i, delay in enumerate((0,) + GETFILE_RETRY_DELAYS):
        if delay:
            sleep(delay)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)["result"]["file_path"]
        except urllib.error.HTTPError:
            raise                      # a real API answer (e.g. 400/404) — retrying won't help
        except (OSError, ValueError):  # URLError/reset/timeout, or a truncated JSON body
            if i == len(GETFILE_RETRY_DELAYS):
                raise


def _download_media(token: str, file_id: str, file_unique_id: str, channel_key: str,
                     upd_id: int, name: str | None = None) -> str:
    fp = _get_file_path(token, file_id)
    os.makedirs(_MEDIA_DIR, exist_ok=True)
    safe = _media_dest_name(channel_key, upd_id, file_unique_id, fp, name)
    dest = os.path.join(_MEDIA_DIR, safe)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    return _tg_download_file(token, fp, dest)


def _log_media_fail(ch: "Channel", kind: str, upd_id: int, e: Exception) -> None:
    """LOUD failure line for a media download that fell through all retries.

    The durable row still records the failure inline ('download failed: …',
    Option B — the log is the truth), but that lives in operator_messages and is
    invisible without a DB query. Emitting a WARNING to the ingest log too makes a
    getFile 404 / file-CDN reset visible in logs/*-ingest.log at a glance
    (reference_bridge_media_download_retry: a swallowed media failure is exactly
    how the operator's DPA + ADCDA photos were lost twice). NEVER silent."""
    _log_line(f"{ch.key}: MEDIA download FAILED ({kind}) on update {upd_id} "
              f"— {type(e).__name__}: {e} — row marked, agent CANNOT Read this attachment")


SHAPE_KEYS_MAX = 400       # cap on the key-list in the last-resort marker


def _unknown_marker(ch: "Channel", msg: dict, upd_id: int) -> str:
    """Last-resort marker for an update shape we have no handler for.

    WHY THE KEY LIST (2026-07-26 incident): at 01:36:49Z the operator sent a
    message on nazim-console that landed as the bare `[non-text update
    440376558]` — `message.from` was present, so it WAS his message, and its
    content was lost. The raw update is consumed by getUpdates and gone, so
    after the fact NOBODY could say what arrived: video? sticker? story? The
    bare marker is unreconstructable, which is exactly why that incident cannot
    be explained today. Recording the SHAPE (the message object's own key
    names) makes the next unknown diagnosable in one look at the row.

    KEYS ONLY, NEVER VALUES: this row is durable and widely read (agents, the
    console, exports). A value may carry PII or a secret; a key name cannot.
    That constraint is why this is a marker and not a payload dump — it is a
    forensic breadcrumb pointing at the handler we still owe, not content.
    """
    keys = ",".join(sorted(str(k) for k in msg))[:SHAPE_KEYS_MAX]
    # WARNING to the ingest log so the next occurrence is visible in
    # logs/nazim-ingest.log without anyone querying the table first.
    _log_line(f"{ch.key}: WARNING unhandled message shape on update {upd_id} — "
              f"keys: {keys} (no handler in message_content — content NOT captured)")
    return f"[non-text update {upd_id} — keys: {keys}]"


def _media_content(ch: "Channel", msg: dict, upd_id: int, text: str) -> tuple[str, dict | None]:
    """The type-dispatch half of message_content. Split out so message_content
    can guard the WHOLE dispatch: a raise here must degrade to the old marker,
    never propagate — the durable log write (step 2 of process_update) is the
    one thing that must always happen.

    Returns (content, media_meta). media_meta is None for a non-media update
    (text, sticker, poll, location, ...); for a photo/animation/document/
    voice/audio/video/video_note it is always a dict — even when the download
    failed after all retries — so the caller can persist the Telegram file_id
    identity REGARDLESS of download outcome (bus #74111: previously a failed
    download degraded to a dead-end text marker and the file_id that could
    have re-fetched it was simply discarded, never written anywhere)."""
    media_meta: dict | None = None

    def _dl(file_id: str, file_unique_id: str, kind: str, name: str | None = None) -> str:
        nonlocal media_meta
        media_meta = {"file_id": file_id, "file_unique_id": file_unique_id,
                      "kind": kind, "ok": False}
        path = _download_media(ch.token, file_id, file_unique_id, ch.key, upd_id, name)
        media_meta["ok"] = True
        return path

    if msg.get("photo"):
        try:
            photo = msg["photo"][-1]
            path = _dl(photo["file_id"], photo["file_unique_id"], "photo")
            content = f"sent a SCREENSHOT → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            _log_media_fail(ch, "photo", upd_id, e)
            content = f"sent a photo (download failed: {e})" + (f" | {text}" if text else "")
    # animation (GIF/soundless mp4) BEFORE document: Telegram sets `document`
    # too on animation messages for backward compatibility, so the document
    # branch would otherwise swallow every GIF and label it a FILE.
    elif msg.get("animation"):
        anim = msg["animation"]
        try:
            name = anim.get("file_name") or f"anim_{anim['file_id'][:12]}.mp4"
            path = _dl(anim["file_id"], anim["file_unique_id"], "animation", name)
            content = f"sent an ANIMATION/GIF → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            _log_media_fail(ch, "animation", upd_id, e)
            content = f"sent an animation (download failed: {e})" + (f" | {text}" if text else "")
    elif msg.get("document"):
        doc = msg["document"]
        try:
            path = _dl(doc["file_id"], doc["file_unique_id"], "document", doc.get("file_name"))
            content = f"sent a FILE → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            _log_media_fail(ch, "document", upd_id, e)
            content = f"sent a file (download failed: {e})" + (f" | {text}" if text else "")
    elif msg.get("voice") or msg.get("audio"):
        voice_or_audio = msg.get("voice") or msg.get("audio")
        try:
            name = voice_or_audio.get("file_name") or f"voice_{voice_or_audio['file_id'][:12]}.ogg"
            path = _dl(voice_or_audio["file_id"], voice_or_audio["file_unique_id"], "voice/audio", name)
            dur = voice_or_audio.get("duration")
            content = (f"sent a VOICE note ({dur}s) → {path}"
                       + (f"  | caption: {text}" if text else ""))
        except Exception as e:
            _log_media_fail(ch, "voice/audio", upd_id, e)
            content = f"sent a voice note (download failed: {e})" + (f" | {text}" if text else "")
    # A video is an ARTIFACT like a photo/doc — download it, or an agent asked
    # to look at a screen recording has nothing to open (same reasoning that
    # put the photo download here in the first place).
    elif msg.get("video"):
        vid = msg["video"]
        try:
            name = vid.get("file_name") or f"video_{vid['file_id'][:12]}.mp4"
            path = _dl(vid["file_id"], vid["file_unique_id"], "video", name)
            dur = vid.get("duration")
            content = (f"sent a VIDEO ({dur}s) → {path}"
                       + (f"  | caption: {text}" if text else ""))
        except Exception as e:
            _log_media_fail(ch, "video", upd_id, e)
            content = f"sent a video (download failed: {e})" + (f" | {text}" if text else "")
    elif msg.get("video_note"):
        note = msg["video_note"]
        try:
            path = _dl(note["file_id"], note["file_unique_id"], "video_note",
                       f"videonote_{note['file_id'][:12]}.mp4")
            dur = note.get("duration")
            content = (f"sent a VIDEO NOTE ({dur}s) → {path}"
                       + (f"  | caption: {text}" if text else ""))
        except Exception as e:
            _log_media_fail(ch, "video_note", upd_id, e)
            content = f"sent a video note (download failed: {e})" + (f" | {text}" if text else "")
    # Stickers are deliberately NOT downloaded: a .webp/.tgs of a cartoon is not
    # an artifact any agent will Read, and writing one per sticker just litters
    # logs/tg_media. The emoji IS the content — it is what the operator meant.
    elif msg.get("sticker"):
        st = msg["sticker"]
        emoji = st.get("emoji") or ""
        pack = st.get("set_name")
        content = ("sent a STICKER" + (f" {emoji}" if emoji else "")
                   + (f" (set: {pack})" if pack else "")
                   + (f"  | caption: {text}" if text else ""))
    # A poll's question + options are the operator's own words — same class of
    # content as message.text, and useless to us as "[non-text update]".
    elif msg.get("poll"):
        poll = msg["poll"]
        q = str(poll.get("question") or "").strip().replace("\n", " ")[:300]
        opts = " | ".join(str((o or {}).get("text") or "")
                          for o in (poll.get("options") or []))[:300]
        content = f'sent a POLL: "{q}"' + (f" — options: {opts}" if opts else "")
    # venue before location: a venue message carries BOTH, and the title/address
    # is the part the operator actually cared about.
    elif msg.get("venue"):
        ven = msg["venue"]
        loc = ven.get("location") or {}
        content = (f"shared a VENUE: {ven.get('title') or ''} — {ven.get('address') or ''}"
                   f" ({loc.get('latitude')}, {loc.get('longitude')})").strip()
    elif msg.get("location"):
        loc = msg["location"]
        live = loc.get("live_period")
        content = (f"shared a LOCATION → {loc.get('latitude')}, {loc.get('longitude')}"
                   + (f" (live, {live}s)" if live else ""))
    # Contact: logged in full (name + number). A phone number typed as plain
    # text is already logged verbatim in this same column, so redacting it only
    # here would lose the message's entire point without changing the posture.
    elif msg.get("contact"):
        con = msg["contact"]
        name = " ".join(p for p in (con.get("first_name"), con.get("last_name")) if p).strip()
        content = (f"shared a CONTACT: {name or '(no name)'}"
                   + (f" — {con['phone_number']}" if con.get("phone_number") else ""))
    elif msg.get("dice"):
        dice = msg["dice"]
        content = f"sent a DICE {dice.get('emoji') or ''} → {dice.get('value')}".replace("  ", " ")
    # GROUP->SUPERGROUP MIGRATION (orch-console bus #50332): Telegram sends this
    # as a service message on the OLD chat_id, carrying the NEW one — the one
    # and only place the new id ever appears. Capture it explicitly in the
    # durable row instead of falling through to the bare "[non-text update,
    # keys: ...]" marker, which names the key but not the value (op#22669's
    # whole point: a value this load-bearing must never be lost to a shape-only
    # marker). bot_channels.allowed_chat_ids is NOT auto-updated here — that's a
    # security allowlist mutation and stays a human/orch-console action; see
    # _page_migrate_to_chat_id_once (process_update) for the loud page.
    elif msg.get("migrate_to_chat_id") is not None:
        new_chat_id = msg["migrate_to_chat_id"]
        old_chat_id = (msg.get("chat") or {}).get("id")
        content = (f"GROUP MIGRATED TO SUPERGROUP: chat_id {old_chat_id} -> {new_chat_id} "
                   f"— bot_channels.allowed_chat_ids NOT auto-updated, needs orch-console action")
    else:
        content = text or _unknown_marker(ch, msg, upd_id)
    return content, media_meta


def message_content(ch: "Channel", msg: dict, upd_id: int) -> str:
    """Human-readable content for the log row — text, or a downloaded-media
    pointer cc-orchestrator can Read, or a last-resort shape marker. Reply
    context is preserved so cc-orch knows which message the operator answered.

    Thin wrapper over message_content_with_media() that drops the media
    identity — kept so every existing caller/test with this exact signature
    is untouched (bus #74111 added the identity as an addition, not a
    behavior change to this function)."""
    return message_content_with_media(ch, msg, upd_id)[0]


def message_content_with_media(ch: "Channel", msg: dict, upd_id: int) -> tuple[str, dict | None]:
    """Same as message_content(), plus the Telegram media identity (file_id/
    file_unique_id/kind/ok) when this update carried a media attachment —
    None for a text/sticker/poll/location/... update. process_update uses
    the identity to persist a re-fetchable pointer into operator_messages
    EVEN WHEN the download failed (bus #74111)."""
    text = msg.get("text") or msg.get("caption") or ""
    try:
        content, media_meta = _media_content(ch, msg, upd_id, text)
    except Exception as e:                                          # noqa: BLE001
        # Dead-man's rule: extraction is best-effort, the LOG is load-bearing
        # (Option B — the durable row is the truth). A bug in any branch above
        # degrades to the pre-2026-07-26 marker; it never costs us the row.
        _log_line(f"{ch.key}: content extraction raised on update {upd_id} "
                  f"({type(e).__name__}: {e}) — degrading to bare marker")
        content = text or f"[non-text update {upd_id}]"
        media_meta = None

    # Reply-threading: prepend the quoted message so cc-orch knows the referent
    # (parity with the retired bridge — an operator reply loses its meaning
    # without the thing it replies to).
    rep = msg.get("reply_to_message")
    if rep:
        snip = (rep.get("text") or rep.get("caption") or "[media]").strip().replace("\n", " ")[:80]
        content = f'↩️ re "{snip}": {content}'
    return content, media_meta


# ── Config ────────────────────────────────────────────────────────────────────

class Channel:
    def __init__(self, row: tuple):
        (self.key, self.token_env_key, self.mode, self.inject_target,
         self.inject_prefix, self.responder_ref, self.allowed_chat_ids,
         self.allowed_usernames, self.group_routing, self.channel_tag,
         self.log_target, self.poll_offset, self.audience, self.owner_lane,
         self.stage_file_to_agent) = row
        self.token = os.environ.get(self.token_env_key or "", "")
        # Per-channel override (in the group_routing JSONB bag): nudge EVEN WHEN the
        # target reads WORKING, instead of busy-deferring (CAI-RESP-382). ON for the
        # operator's CTO console (nazim-console) — he wants immediate delivery and
        # Nazim handles a mid-task injection gracefully. A build lane keeps the
        # default busy-defer so it isn't interrupted mid-task.
        self.nudge_when_busy = bool((self.group_routing or {}).get("nudge_when_busy"))

    COLS = ("channel_key, token_env_key, mode, inject_target, inject_prefix, "
            "responder_ref, allowed_chat_ids, allowed_usernames, group_routing, "
            "channel_tag, log_target, poll_offset, audience, owner_lane, "
            "stage_file_to_agent")


def load_channels(conn) -> dict[str, Channel]:
    with conn.cursor() as cur:
        # INGEST_CHANNELS (comma-separated channel_keys) PINS this daemon to a
        # specific set of channels regardless of the `enabled` flag — the per-host
        # isolation that lets a non-hub box (the Mini running Nazim's console)
        # poll ONLY its own bot (@nazim_cto_bot) while the hub keeps polling every
        # `enabled` channel. A channel pinned here stays enabled=false in the
        # registry, so the hub's ingest never touches it → the dual-poller 409
        # class (two daemons fighting one bot token, seen Jul 3) can't recur.
        override = os.environ.get("INGEST_CHANNELS", "").strip()
        if override:
            keys = [k.strip() for k in override.split(",") if k.strip()]
            cur.execute(f"SELECT {Channel.COLS} FROM bot_channels "
                        f"WHERE channel_key = ANY(%s)", (keys,))
        else:
            cur.execute(f"SELECT {Channel.COLS} FROM bot_channels WHERE enabled")
        return {c.key: c for c in (Channel(r) for r in cur.fetchall())}


# ── Pinned-channel drift (op#22669 item 3, orch-console bus #43933) ───────────
#
# tests/migrations/test_ingest_channels_enabled_false.py only proves the
# invariant ("a channel pinned via INGEST_CHANNELS ships enabled=false") at
# migration-authoring time — it parses migrations/*.sql and boot script env,
# never the live row. A manual UPDATE, a future migration flipping it back, or
# replication skew can still drift bot_channels.enabled=true under a pinned
# key AFTER deploy, which is the exact precondition for the dual-poller 409
# class this host and the hub fighting one bot token (bus #43775/#43833,
# channel 'oeh', fixed in PR #178). This is the runtime backstop for that.

PAGE_FROM_AGENT = "ingest-watchdog"   # mirrors priority_sla_watchdog.py's 'sla-watchdog'
PAGE_TO_AGENT = "orch-console"


def pinned_keys() -> list[str]:
    override = os.environ.get("INGEST_CHANNELS", "").strip()
    return [k.strip() for k in override.split(",") if k.strip()]


def drifted_pinned_channels(conn, keys: list[str]) -> list[str]:
    """Pinned channel_keys that are LIVE enabled=true right now. Empty on the
    hub (no INGEST_CHANNELS pin — it is SUPPOSED to poll WHERE enabled)."""
    if not keys:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT channel_key FROM bot_channels WHERE channel_key = ANY(%s) AND enabled",
            (keys,))
        return [r[0] for r in cur.fetchall()]


def _page_pinned_drift_once(conn, key: str, host: str) -> None:
    """Page ONCE per (channel, host) — durable dedup via the bus itself (a
    marker substring in `body`), not in-memory state, so a daemon restart
    never re-pages an already-reported drift. Page-once-EVER, same shape as
    priority_sla_watchdog.py's already_paged_on_bus dedup: this is a rare,
    should-never-happen invariant break, not a recurring metric to re-alert
    on a timer — clearing it is an explicit human/DB action either way.

    The dedup lookup stays a raw SELECT (read-only), but the actual INSERT
    goes through scripts/bus_send.send() — CLAUDE.md forbids a hand-written
    `INSERT INTO agent_messages` (bus #43651: a hand-rolled insert once
    omitted `priority` and silently landed below the hub's wake floor)."""
    marker = f"PINNED-CHANNEL-DRIFT:{key}:{host}"
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agent_messages WHERE body LIKE %s LIMIT 1", (f"{marker}%",))
        if cur.fetchone():
            return
    from scripts import bus_send
    bus_send.send(
        from_agent=PAGE_FROM_AGENT, to=PAGE_TO_AGENT, mtype="blocker",
        subject=f"pinned channel '{key}' enabled=true LIVE on {host}",
        body=(f"{marker}: bot_channels.enabled=true for a channel pinned via "
              f"INGEST_CHANNELS on {host} — dual-poller 409 risk against the hub "
              f"(bus #43775/#43833 precedent, channel 'oeh'). Fix: "
              f"UPDATE bot_channels SET enabled=false WHERE channel_key='{key}'. "
              f"Page-once-ever for this channel+host; won't repeat unless this row is removed."),
        priority="P1", req=True, dsn=_dsn(),
    )


def _page_migrate_to_chat_id_once(conn, ch: Channel, old_chat_id, new_chat_id, upd_id: int) -> None:
    """Page ONCE per (channel, old_chat_id, new_chat_id) — same durable
    page-once-ever dedup as _page_pinned_drift_once (a marker substring in
    `body`, not in-memory state). A group->supergroup migration is rare and
    each one is a distinct, should-never-recur event, not a recurring metric.

    bot_channels.allowed_chat_ids is deliberately NOT auto-rewritten here:
    it is the gate's deny-by-default allowlist (CLAUDE.md fleet doctrine),
    and silently widening a security gate from an inbound Telegram payload
    is exactly the kind of auto-action that doctrine keeps human/orch-gated.
    This page carries the exact fix so the gap (orch-console bus #50332:
    the bot would otherwise go silently deaf with no way to recover the new
    id after the fact) can be closed in one step."""
    marker = f"MIGRATE-TO-CHAT-ID:{ch.key}:{old_chat_id}:{new_chat_id}"
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agent_messages WHERE body LIKE %s LIMIT 1", (f"{marker}%",))
        if cur.fetchone():
            return
    from scripts import bus_send
    bus_send.send(
        from_agent=PAGE_FROM_AGENT, to=PAGE_TO_AGENT, mtype="blocker",
        subject=f"channel '{ch.key}' group migrated to supergroup — chat_id changed",
        body=(f"{marker}: Telegram update {upd_id} on channel '{ch.key}' — chat_id "
              f"{old_chat_id} -> {new_chat_id}. The bot goes silently deaf on this "
              f"group once Telegram actually cuts over, unless the new id is added. "
              f"Fix: UPDATE bot_channels SET allowed_chat_ids = allowed_chat_ids || "
              f"'{{{new_chat_id}}}'::bigint[] WHERE channel_key='{ch.key}'. "
              f"Page-once-ever for this old->new pair; won't repeat unless this row is removed."),
        priority="P2", req=True, dsn=_dsn(),
    )


def is_staged_file_eligible(ch: "Channel", msg: dict) -> bool:
    """True iff this inbound update is a real FILE/document (not a GIF —
    Telegram sets `document` on animation messages too, same gotcha
    _media_content already guards against) on a non-operator CLIENT channel
    whose import pipeline isn't already owned by coord (orch-console #51060,
    Musa op#25437-25440: lanes keep telling clients "I can't open your
    file" instead of staging it — this auto-routes so nobody has to
    remember to ask)."""
    if not (msg.get("document") and not msg.get("animation")):
        return False
    return ch.audience == "client" and ch.owner_lane != "irsyad-coord"


def _page_stage_file_once(conn, ch: "Channel", upd_id: int, op_msg_id: int,
                           local_path: str, caption: str) -> None:
    """Page ONCE per (channel, update) — same durable page-once dedup as
    _page_pinned_drift_once / _page_migrate_to_chat_id_once (a marker
    substring in `body`, not in-memory state). Auto-routes an inbound client
    file for staging: a lane must never open the file itself and tell the
    client it "can't open" it — it waits for a STAGE verdict
    (scripts/stage_client_file.py) instead.

    Target is orch-console UNLESS this channel opts into a specific owning
    lane via bot_channels.stage_file_to_agent (migration 092, bus #53147) —
    e.g. cosem-exams -> cc-cosem-exams, which already owns staging+handling
    Hariz's files end-to-end, making the console a redundant relay hop.
    orch-console stays the reviewer for supervised client REPLIES on every
    channel regardless — this override only moves the pre-read STAGING
    handoff, a separate path this function does not touch."""
    marker = f"STAGE-FILE:{ch.key}:{upd_id}"
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agent_messages WHERE body LIKE %s LIMIT 1", (f"{marker}%",))
        if cur.fetchone():
            return
    from scripts import bus_send
    caption_note = f"caption: {caption}" if caption else "(no caption)"
    page_to = ch.stage_file_to_agent or PAGE_TO_AGENT
    bus_send.send(
        from_agent=PAGE_FROM_AGENT, to=page_to, mtype="blocker",
        subject=f"STAGE: {ch.key} op#{op_msg_id} {local_path}",
        body=(f"{marker}\nSTAGE: {ch.key} op#{op_msg_id} {local_path}\n{caption_note}\n\n"
              f"Run scripts/stage_client_file.py {local_path} {op_msg_id} --export to check "
              "it before handing anything to a lane — a lane's own outbound guard now "
              "refuses a message that just says it can't open a client's file; it must "
              "wait for this instead."),
        priority="P1", req=True, dsn=_dsn(),
    )


def check_pinned_channels_not_enabled(conn, host: str | None = None) -> list[str]:
    """Loud log + page-once for every pinned channel currently enabled=true
    live. Returns the drifted keys (empty list = clean). Called once per
    CONFIG_REFRESH cycle from main() — cheap (one indexed lookup on a handful
    of pinned keys) and self-heals its own log noise once the row is fixed."""
    host = host or socket.gethostname()
    drifted = drifted_pinned_channels(conn, pinned_keys())
    for key in drifted:
        _log_line(f"WATCHDOG: pinned channel '{key}' is enabled=true LIVE on {host} "
                   f"(INGEST_CHANNELS pins it here) — dual-poller 409 risk. Fix: "
                   f"UPDATE bot_channels SET enabled=false WHERE channel_key='{key}'")
        try:
            _page_pinned_drift_once(conn, key, host)
        except Exception as e:
            _log_line(f"WATCHDOG: pinned-channel-drift page for '{key}' failed ({type(e).__name__}: {e})")
    return drifted


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


# ── Token groups: one-bot-many-clients (op#31298/31307, one getUpdates loop per
# UNIQUE token, not per channel_key — the dual-poller 409 fix) ────────────────

def group_by_token(channels: dict[str, Channel]) -> dict[str, list[Channel]]:
    """Channels sharing one token_env_key poll together. Sorted by channel_key
    for a deterministic first-match order (see match_channel_in_group)."""
    groups: dict[str, list[Channel]] = {}
    for ch in channels.values():
        groups.setdefault(ch.token_env_key, []).append(ch)
    for group in groups.values():
        group.sort(key=lambda c: c.key)
    return groups


def overlapping_chat_ids(group: list[Channel]) -> dict[int, list[str]]:
    """chat_ids claimed by more than one row sharing a token — ambiguous
    routing. Refused (not first-match) by the caller: a shared token's
    group must have disjoint allowed_chat_ids, or there's no way to tell
    which client a message belongs to. Empty dict = no overlap."""
    seen: dict[int, list[str]] = {}
    for ch in group:
        for cid in (ch.allowed_chat_ids or []):
            seen.setdefault(cid, []).append(ch.key)
    return {cid: keys for cid, keys in seen.items() if len(keys) > 1}


def empty_allowlist_rows(group: list[Channel]) -> list[str]:
    """A row with no allowed_chat_ids/allowed_usernames can never match in a
    SHARED-token group (gate_allows is always False for it) — not dangerous
    (deny-by-default fails safe either way) but silent in a multi-row group
    is how a client looks 'live' while actually never receiving anything.
    A solo-token group (today's shape for every channel) is unaffected —
    an unconfigured standalone channel is the normal pre-cutover state."""
    if len(group) <= 1:
        return []
    return [ch.key for ch in group if not (ch.allowed_chat_ids or ch.allowed_usernames)]


def invite_pending(conn, channel_key: str) -> bool:
    """op#34846: True while an unused, unexpired invite is outstanding for the
    channel — the window in which its empty allowlist is the designed pre-bind
    state (the pre-GATE bind hook answers the '/start <token>' deep-link tap),
    not a misconfiguration."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM bot_channel_invites "
            "WHERE channel_key = %s AND used_at IS NULL AND expires_at > now() "
            "LIMIT 1",
            (channel_key,),
        )
        return cur.fetchone() is not None


def match_channel_in_group(group: list[Channel], chat_id: int | None,
                            username: str | None) -> Channel | None:
    """Which row in this token's group handles an update, by chat_id/username
    — reuses gate_allows so matching IS the gate (no divergent logic). Returns
    None (unmatched) for an update from a chat no row in the group claims;
    the caller still logs it (under the group's first row, for audit) and
    gates it exactly like today's single-channel 'random stranger' case."""
    if chat_id is None:
        return None
    for ch in group:
        if gate_allows(ch, chat_id, username):
            return ch
    return None


# ── Self-serve invite deep-link bind (Musa op#34846, orch-console #78263) ────
#
# Onboarding without waiting for a first 'hi': onboard_client_channel.py
# --invite mints a single-use, expiring token and stores ONLY its sha256 in
# bot_channel_invites — the plaintext lives in the t.me/<bot>?start=TOKEN deep
# link CA/Musa relays to the client and nowhere at rest. The first '/start
# TOKEN' inbound on the bot BINDS the sending chat to the invite's channel:
# allowlist widened, invite consumed, bound chat audited on the invite row.
# This hooks AFTER the durable LOG and BEFORE the GATE inside process_update
# — A1/LOG/GATE/ROUTE order is untouched; the bind is pure admission-control.

GENERIC_INVITE_REPLY = (
    "This link isn't valid anymore. Please ask for a fresh link and tap it "
    "again — I can't connect you from this message.")
GENERIC_BIND_WELCOME = (
    "✅ Connected — this channel is now active. You can send your messages here.")


def hash_invite_token(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def _start_invite_payload(text: str) -> str | None:
    """'/start TOKEN' -> TOKEN; the '/start@bot TOKEN' group form -> TOKEN;
    bare '/start' -> ''. None when the text is not a /start at all."""
    t = (text or "").strip()
    if not t.lower().startswith("/start"):
        return None
    rest = t[len("/start"):].strip()
    if rest.startswith("@"):
        rest = rest.split(" ", 1)[1] if " " in rest else ""
    return rest.strip()


def try_invite_bind(conn, ch: Channel, channels: dict | None, msg: dict,
                    op_msg_id: int) -> bool:
    """Handle a '/start' carrying (or lacking) an invite token. Returns True
    when fully handled here — bind, refusal or re-welcome: always replied to +
    audited, never routed. Returns False when this update is not ours to
    intercept: not a /start at all, or a BARE '/start' (plain Start button)
    with no unused+unexpired invite outstanding on this token — that keeps
    today's logged+gated silence. A deep-link tap '/start TOKEN' is ALWAYS
    answered: bind on a valid token, the generic reply on an invalid, used or
    expired one (op#34846 — never silent).

    Sibling-leak discipline: the success reply is the INVITE's channel's own
    self_description fetched from its bot_channels row — never anything
    derived from sibling channels sharing this token."""
    text = msg.get("text") or ""
    payload = _start_invite_payload(text)
    if payload is None:
        return False
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return False

    if not payload:
        # Bare button-tap: helpful (generic 'ask for a fresh link') only
        # while an onboarding invite is actually outstanding on this token.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM bot_channel_invites i "
                "JOIN bot_channels c ON c.channel_key = i.channel_key "
                "WHERE c.token_env_key = %s AND i.used_at IS NULL "
                "AND i.expires_at > now() LIMIT 1", (ch.token_env_key,))
            if cur.fetchone() is None:
                return False

    reply, reply_tag, outcome = None, ch.channel_tag, "generic"
    if payload:
        with conn.cursor() as cur:
            # Validity lives in SQL (used_at IS NULL AND expires_at > now()):
            # unknown, replayed and expired tokens are indistinguishable BY
            # DESIGN — each gets the same generic reply, nothing bound.
            cur.execute(
                "SELECT i.id, i.channel_key, "
                "c.group_routing->>'self_description', c.channel_tag "
                "FROM bot_channel_invites i "
                "JOIN bot_channels c ON c.channel_key = i.channel_key "
                "WHERE i.token_hash = %s AND i.used_at IS NULL "
                "AND i.expires_at > now()", (hash_invite_token(payload),))
            inv = cur.fetchone()
        if inv:
            inv_id, inv_key, self_desc, inv_tag = inv
            with conn.cursor() as cur:
                # Overlap guard (op#33571 per-channel isolation): a chat
                # already bound on this token never cross-binds. Checked
                # BEFORE the consume so a refused attempt does not burn the
                # invite — the right client can still use it.
                cur.execute(
                    "SELECT channel_key FROM bot_channels "
                    "WHERE token_env_key = %s AND %s = ANY(allowed_chat_ids)",
                    (ch.token_env_key, chat_id))
                bound = cur.fetchone()
            if bound and bound[0] != inv_key:
                outcome = "cross-bind-refused"
            elif bound:
                # already this channel's chat re-tapping its link: idempotent
                # re-welcome, no state change.
                reply, reply_tag, outcome = \
                    self_desc or GENERIC_BIND_WELCOME, inv_tag, "re-welcome"
            else:
                with conn.cursor() as cur:
                    # Guarded consume = single-use under races: the rowcount
                    # decides, not the earlier read.
                    cur.execute(
                        "UPDATE bot_channel_invites SET used_at = now(), "
                        "bound_chat_id = %s WHERE id = %s AND used_at IS NULL",
                        (chat_id, inv_id))
                    won = cur.rowcount == 1
                if won:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE bot_channels SET allowed_chat_ids = "
                            "(SELECT ARRAY(SELECT DISTINCT unnest("
                            "allowed_chat_ids || ARRAY[%s]::bigint[]))), "
                            "updated_at = now() WHERE channel_key = %s",
                            (chat_id, inv_key))
                        # Self-activation — but NEVER for a channel pinned on
                        # THIS host via INGEST_CHANNELS: a pinned row flipping
                        # enabled=true is the dual-poller 409 class the
                        # pinned-drift watchdog pages on (see load_channels).
                        # On this host the pin IS the poll switch; the bind's
                        # job here is the allowlist only.
                        if inv_key not in pinned_keys():
                            cur.execute(
                                "UPDATE bot_channels SET enabled = true, "
                                "updated_at = now() "
                                "WHERE channel_key = %s AND NOT enabled",
                                (inv_key,))
                    conn.commit()
                    # Hot in-memory update: routing picks the bound chat up in
                    # THIS cycle's remaining updates without waiting for the
                    # CONFIG_REFRESH reload (which re-reads the row anyway).
                    if channels and inv_key in channels:
                        hot = channels[inv_key]
                        ids = list(hot.allowed_chat_ids or [])
                        if chat_id not in ids:
                            hot.allowed_chat_ids = ids + [chat_id]
                    reply, reply_tag, outcome = \
                        self_desc or GENERIC_BIND_WELCOME, inv_tag, \
                        f"bound to {inv_key}"
                else:
                    outcome = "lost-single-use-race"
    if reply is None:
        reply = GENERIC_INVITE_REPLY

    send_ok = True
    try:
        tg_call(ch.token, "sendMessage", {"chat_id": chat_id, "text": reply})
    except Exception as e:                                      # noqa: BLE001
        send_ok = False
        _log_line(f"{ch.key}: invite-bind reply send failed "
                  f"({type(e).__name__}: {e}) — bind/refusal already durable")
    try:
        with conn.cursor() as cur:
            # handled_at: the reply is delivered and needs no operator action
            # (an unstamped outbound row would read as pending work).
            cur.execute(
                "INSERT INTO operator_messages (direction, channel, chat_id, tag, "
                "text, delivered, from_name, handled_at) "
                "VALUES ('outbound','telegram',%s,%s,%s,%s,'invite-bind',now())",
                (str(chat_id), reply_tag, reply, send_ok))
        conn.commit()
    except Exception as e:                                      # noqa: BLE001
        conn.rollback()
        _log_line(f"{ch.key}: invite-bind outbound audit insert failed "
                  f"({type(e).__name__}: {e})")
    _log_line(f"{ch.key}: invite-bind chat={chat_id}: {outcome}")
    return True


# ── Route: agent-session / log-and-route => nudge-only tmux line ─────────────

_ABSENT_TARGET_LOGGED: set = set()


def _session_exists(target) -> bool:
    """Same tmux binary + exact-match form nudge_session uses. On a tmux error we can't
    tell, so say True and let nudge_session's own has-session decide (old behavior)."""
    if not target:
        return False
    tmux = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
    try:
        r = subprocess.run([tmux, "has-session", "-t", f"={target}"],
                           capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return r.returncode == 0


def nudge_session(target: str, channel_key: str, n_unread: int, *, urgent: bool = False) -> bool:
    """Count-only nudge. NEVER carries payload (CAI-RESP-357). Best-effort
    signal — delivery is guaranteed by the log + reconciliation, not by this.

    `urgent`: when True AND `target` is in ESCAPE_PREEMPT_TARGETS, sends
    Escape BEFORE the clear+type+Enter sequence below (Musa op#36085/36098)
    — a busy/mid-tool-call pane otherwise just queues these keystrokes
    behind whatever it's doing, which is the exact 'URGENT did not interrupt
    anything' failure this closes. Tested clean on a throwaway pane,
    2026-10-30 — see ESCAPE_PREEMPT_TARGETS' comment for what was verified
    and what Escape does NOT do (it doesn't force-kill a running command)."""
    tmux = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
    line = (f"\U0001F4E5 {n_unread} unread on '{channel_key}' — "
            f"reconcile operator_log.unprocessed()")
    # A recycle owns this pane for a few seconds. A keystroke landing between the
    # composer wipe and the /clear Enter jams the clear and the body comes back
    # half-initialised. Skipping is free: this nudge is signal only, and the
    # operator's message is already durable in operator_messages (Option B).
    if fire_window.is_held(target):
        return False
    try:
        subprocess.run([tmux, "has-session", "-t", f"={target}"],
                       check=True, capture_output=True, timeout=5)
        if urgent and target in ESCAPE_PREEMPT_TARGETS:
            subprocess.run([tmux, "send-keys", "-t", f"={target}:0.0", "Escape"],
                           check=True, capture_output=True, timeout=5)
            time.sleep(0.5)
        # Clear any leftover unsent text FIRST (C-u). 2026-07-04: the orch idled
        # with an unsent draft ('ping me when...') and the nudge got appended onto
        # it instead of landing — the operator's 'status' was silently swallowed.
        # A nudge only fires when the target is idle, so clearing an abandoned draft
        # is safe. This is send-keys of a CONTROL key only — never payload text
        # (still CAI-RESP-357 compliant: the nudge line below carries no content).
        subprocess.run([tmux, "send-keys", "-t", f"={target}:0.0", "C-u"],
                       check=True, capture_output=True, timeout=5)
        time.sleep(0.3)
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


# ── Busy-aware nudge policy (CAI-RESP-382) ────────────────────────────────────
# The operator's complaint: the daemon interrupted him mid-task on EVERY message.
# Fix: nudge immediately only when the target agent is IDLE; when it's WORKING a
# default-priority message DEFERS (no keystroke) — Option B's durable log + the
# agent's per-turn reconciliation deliver it at the next natural pause — and one
# throttled tg_out ack tells the operator it landed. URGENT overrides (always
# nudge); BTW is never-nudge (drains at the next idle/boot). Sensing is
# capture-pane only — zero send-keys, no auto-submit (unlike the watchdog).
URGENT_KW = "urgent"
BTW_KW = "btw"
ACK_THROTTLE_SEC = 600   # at most one "I'm mid-task" ack per 10 min per channel
ACK_AFTER_SEC = 150      # belt-and-suspenders (ihsan): reassure the operator if
                         # (raised 45->150 2026-07-10 per operator: the client group
                         #  was getting a "got your message" on nearly every message
                         #  since 45s is often < one mid-task turn; only reassure when
                         #  genuinely slow so quick replies stand alone, no redundant ack)
                         # his message sits unhandled this long — busy, idle, OR
                         # wedged. He is NEVER left wondering whether it landed.
MAX_DEFER_SEC = 600      # busy-defer CAP: a message unhandled past this nudges
                         # ANYWAY (CAI-RESP-382) — deferral must never strand the
                         # operator, even if the agent stays busy indefinitely.
# Channels (by channel_tag) where the CLIENT-facing "📨 Got your message" ack is
# SUPPRESSED — a human hand-responds and the bot ack reads as "no one's looking"
# (gazzabyte-irsyad: client op#15034 explicitly asked, coord #29009, 2026-08-20;
# cosem-exams: operator op#17235 2026-08-27 — Hariz was seeing bot busy-acks
# instead of the exams-lane agent replying directly; same agent-hand-responds case).
# ONLY the client ack is skipped; logging, routing, and the handler-nudge
# (drain_stale_deferred) stay — so a message is still logged + the handler still
# woken, it just isn't bot-acked at the sender. Env-overridable (comma-sep tags).
SUPPRESS_CLIENT_ACK_TAGS = {
    t.strip() for t in os.environ.get("SUPPRESS_CLIENT_ACK_TAGS", "gazzabyte-irsyad,cosem-exams").split(",") if t.strip()
}

# ── Operator-channel SLA tightening (Musa op#36085/36087/36098, 2026-10-30) ──
# ACK_AFTER_SEC/MAX_DEFER_SEC above are tuned for CLIENT channels (ACK_AFTER_SEC
# was deliberately raised 45->150 on 2026-07-10 after an over-acking complaint)
# and MUST stay untouched there. The operator's own two channels — his direct
# line to each body — get a tighter SLA: a substantive reply inside ~60s even
# mid-task, and a forced nudge inside ~2 min rather than 10. Client-channel
# timing is read from the SAME two constants unchanged; this is an override,
# not a replacement.
OPERATOR_CHANNEL_TAGS = {"orch-channel", "nazim-console"}
OPERATOR_ACK_AFTER_SEC = 60
OPERATOR_MAX_DEFER_SEC = 120

# target (tmux session) -> the agent_id it posts agent_messages under, so the
# substantive ack below can show genuinely LIVE activity. agent_status.current_task
# is boot-time-static fleet-wide (verified live 2026-10-30: every body reads
# 'session-launch model=...'/'hub — always-on...' regardless of what it's
# actually doing right now) so it is NOT used here — the most recent outbound
# agent_messages row is the live signal instead.
TARGET_TO_AGENT_ID = {"orch": "cc-orchestrator", "nazim": "orch-console"}

# Escape-first hard preemption on URGENT (same op refs). Tested clean on a
# disposable throwaway tmux pane, 2026-10-30, both required cases: Escape
# mid-Bash-tool-call returns the pane to an idle, input-ready prompt within
# ~1s (the underlying shell command is NOT force-killed — it keeps running
# detached to its own natural completion, which is fine for our purpose:
# this means "stop waiting, read input now," not "abort what's running");
# Escape during an active permission-confirmation dialog cleanly rejects that
# one action and returns to idle, no corruption either case. Per-target
# ALLOWLIST, not a global flag: 'nazim' ships first (lower blast radius, Musa
# accepted the risk there, bus #84092); 'orch' (the hub) stays OFF until
# explicitly ungated — a bad interrupt on the hub's live pane is a different,
# higher-blast-radius risk than on the console's.
ESCAPE_PREEMPT_TARGETS = {
    t.strip() for t in os.environ.get("ESCAPE_PREEMPT_TARGETS", "nazim").split(",") if t.strip()
}


def pane_working(session: str) -> bool:
    """SENSE ONLY: is the target agent mid-task? 'esc to interrupt' in the pane
    footer = WORKING (same signal the lane-watchdog reads). Never sends keys."""
    tmux = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
    try:
        r = subprocess.run([tmux, "capture-pane", "-t", f"={session}:0.0", "-p"],
                           check=True, capture_output=True, timeout=5, text=True)
        # LIVE FOOTER only. This said "footer" in the docstring and greped the whole pane, so a
        # body with an old busy marker in its scrollback read busy forever (2026-08-16).
        # Unreadable => idle, matching this function's existing fail-toward-delivery stance.
        return pane_busy.is_busy_text(r.stdout, on_unreadable=False)
    except Exception:
        return False   # can't sense → treat as idle (nudge); fail toward delivery


def urgency(text: str) -> str:
    # Token-boundary match, not bare startswith: 'btworks'/'between' must NOT
    # read as BTW (a BTW false-positive SUPPRESSES the nudge — fails toward
    # non-delivery, the one direction that hurts).
    t = (text or "").strip().lower()
    for kw, tag in ((URGENT_KW, "urgent"), (BTW_KW, "btw")):
        if t == kw or t.startswith(kw + " ") or t.startswith(kw + ":"):
            return tag
    return "default"


# ── Group-chat addressing (bus #47483) ────────────────────────────────────────
# The busy/reassurance acks were written when every polled chat was effectively
# 1:1 with the bot. In a GROUP (cosem-caai), every line any human types fires
# `process_update`, so plain banter between two humans not addressed to the bot
# at all ("Musa + Ray banter", op#24073/#24083) got the '📨 Got your message'
# ack twice in 10 minutes. Fix: in a group, only ack when the bot was actually
# addressed (an @mention, or a reply to one of the bot's own messages). DMs are
# untouched — _is_group_chat is false there, so the gate is always a no-op.
_bot_identity_cache: dict[str, dict] = {}


def _bot_identity(token: str) -> dict:
    """Cached getMe() -> {'id': int, 'username': str}. Best-effort: a lookup
    failure returns {} and every caller here treats that as 'can't tell' —
    never suppress an ack on an identity we failed to resolve (fail toward
    the pre-fix delivery behavior, not toward new silence)."""
    if token in _bot_identity_cache:
        return _bot_identity_cache[token]
    try:
        me = tg_call(token, "getMe", {})
        identity = {"id": me.get("id"), "username": me.get("username")}
    except Exception:
        identity = {}
    _bot_identity_cache[token] = identity
    return identity


def _is_group_chat(msg: dict) -> bool:
    return (msg.get("chat") or {}).get("type") in ("group", "supergroup")


def _bot_is_addressed(ch: "Channel", msg: dict) -> bool:
    """Only for a group chat: True if this specific message @mentions the bot
    or replies to one of the bot's own messages. Unknown identity -> True
    (don't suppress on an unknown, see _bot_identity)."""
    identity = _bot_identity(ch.token)
    bot_id, bot_username = identity.get("id"), identity.get("username")
    if not bot_id and not bot_username:
        return True
    if (msg.get("reply_to_message") or {}).get("from", {}).get("id") == bot_id:
        return True
    if not bot_username:
        return False
    mention = f"@{bot_username}".lower()
    text = msg.get("text") or msg.get("caption") or ""
    for ent in (msg.get("entities") or []) + (msg.get("caption_entities") or []):
        if ent.get("type") == "text_mention" and (ent.get("user") or {}).get("id") == bot_id:
            return True
        if ent.get("type") == "mention":
            # Entity offset/length are UTF-16 code units; a Python-index slice
            # can drift when astral-plane chars (emoji) precede the mention.
            # Acceptable here — a miss just falls through to the substring
            # check below, which still catches the common plain-ASCII mention.
            off, length = ent.get("offset", 0), ent.get("length", 0)
            if text[off:off + length].lower() == mention:
                return True
    return mention in text.lower()


_chat_type_cache: dict[tuple[str, str], str] = {}


def _chat_type(token: str, chat_id) -> str:
    """Cached getChat().type. Best-effort: a lookup failure returns 'private'
    — the pre-fix behavior (never suppress) for every channel this can't
    resolve, not a new silent-failure mode."""
    key = (token, str(chat_id))
    if key in _chat_type_cache:
        return _chat_type_cache[key]
    try:
        chat = tg_call(token, "getChat", {"chat_id": chat_id})
        t = chat.get("type") or "private"
    except Exception:
        t = "private"
    _chat_type_cache[key] = t
    return t


def _live_status_suffix(conn, target: str) -> str:
    """Best-effort live-status fragment for the operator-channel ack (Musa
    op#36085/36098): the most recent agent_messages row THIS target itself
    posted, as a genuinely-live substitute for agent_status.current_task
    (verified boot-time-static fleet-wide 2026-10-30 — every body reads
    'session-launch model=...'/'hub — always-on...' regardless of what it's
    actually doing, so it would show stale boot info here). Empty string on
    any miss or staleness — never fabricate activity.

    INVARIANT (cc-quality, PR #675): SUBJECT ONLY, never body/text. This
    reaches the operator's own phone via a tg_out ack, and the only two
    from_agent ids ever looked up are TARGET_TO_AGENT_ID's two brain agents
    (cc-orchestrator, orch-console) — never a client-facing or lane agent_id.
    Do not widen this to pull `body` or to resolve arbitrary agent_ids."""
    agent_id = TARGET_TO_AGENT_ID.get(target)
    if not agent_id:
        return ""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT subject, extract(epoch from (now() - created_at))::int "
                "FROM agent_messages WHERE from_agent=%s ORDER BY id DESC LIMIT 1",
                (agent_id,))
            row = cur.fetchone()
    except Exception:
        return ""   # best-effort; never let this block an ack
    if not row or not row[0] or row[1] is None or row[1] > 1800:   # >30 min isn't "live"
        return ""
    return f" Last update: {row[0]}"


def throttled_busy_ack(conn, ch: "Channel") -> None:
    """Enqueue ONE 'got it, mid-task' ack per ACK_THROTTLE_SEC per channel (via
    the tg_out queue), so a deferred operator isn't left wondering."""
    if ch.channel_tag in SUPPRESS_CLIENT_ACK_TAGS:
        return   # client-ack suppressed for this channel (human hand-responds)
    ack = (
        # client perimeter (log-and-route): no operator-internal phrasing
        # "shortly" is BANNED on the client perimeter (orch-console, 2026-08-17).
        # It failed twice on the gazzabyte/irsyad account, Nazim told Shuk in writing
        # that it had failed twice — and then this template said it to him a THIRD
        # time, automatically, 3 minutes after a client-flagged URGENT. A vague time
        # promise is worse than none: it spends credibility and buys the reader
        # nothing. State what is TRUE (it is logged and routed) and let the human
        # who picks it up give a real time. The '📨 Got your message' prefix is
        # LOAD-BEARING — reassure_if_unhandled dedups on LIKE '%Got your message%'
        # and tg_out's stale-ack guard startswith()es it. Do not reword the prefix.
        "\U0001F4E8 Got your message — it's logged and someone will come back to you on it."
        if ch.mode == "log-and-route"
        else "\U0001F4E8 Got your message — I'm mid-task right now, I'll reply at "
             "my next pause. (Reply URGENT to interrupt now.)")
    # Operator channels only (Musa op#36085/36098): append a live-status
    # fragment. Client-channel wording stays byte-identical to the above —
    # that text was tuned by operator complaint (2026-08-17/2026-08-20) and
    # this never touches it.
    if ch.channel_tag in OPERATOR_CHANNEL_TAGS:
        target = ch.inject_target if ch.mode == "agent-session" else "orch"
        ack += _live_status_suffix(conn, target)
    with conn.cursor() as cur:
        # LIKE, not exact-text match (op#36085/36098): the live-status suffix
        # above makes the text vary call-to-call, so an exact-match throttle
        # would never dedup and would re-ack every pass. The '📨 Got your
        # message' prefix stays fixed either way (see the LOAD-BEARING note
        # above) so matching on it is still a correct per-channel throttle.
        cur.execute(
            "SELECT 1 FROM tg_out WHERE channel_key=%s AND text LIKE %s "
            "AND created_at > now() - make_interval(secs => %s) LIMIT 1",
            (ch.key, "\U0001F4E8 Got your message%", ACK_THROTTLE_SEC))
        if cur.fetchone():
            return   # already acked within the window
        cur.execute("INSERT INTO tg_out (channel_key, text) VALUES (%s,%s)", (ch.key, ack))
        conn.commit()


def drain_stale_deferred(conn, ch: "Channel") -> None:
    """CAI-RESP-382 max-latency cap: if any inbound message has sat UNHANDLED
    longer than MAX_DEFER_SEC, nudge anyway — busy-defer must never strand the
    operator (the 45-min-silence bug). ONE forced nudge per unhandled batch, keyed
    on the NEWEST unhandled message id — NOT a wall-clock throttle.

    Why id-dedup, not a time throttle (2026-07-08): the old throttle lived on the
    Channel object (`_last_stale_nudge`), but main() recreates every Channel each
    CONFIG_REFRESH (~60s) and carries only poll_offset forward — so the throttle
    reset every refresh and re-fired the SAME forced nudge repeatedly (operator saw
    3 nudges for one message in ~90s). Keying on max(id) of the unhandled batch and
    carrying `_last_forced_nudge_id` forward across refresh survives that, and
    matches reassure_if_unhandled: one nudge per message, repeats add nothing. A
    genuinely NEWER stuck message still re-nudges; Option B + 'reply URGENT' cover a
    persistently-busy agent."""
    if ch.mode not in ("agent-session", "log-and-route"):
        return
    target = ch.inject_target if ch.mode == "agent-session" else "orch"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(created_at), max(id) FROM operator_messages "
            "WHERE direction='inbound' AND tag=%s AND handled_at IS NULL",
            (ch.channel_tag,))
        n, oldest, newest_id = cur.fetchone()
        if not oldest:
            return
        # Operator channels get a tighter cap (Musa op#36085/36098): "no silent
        # run longer than a minute or two." Client channels keep MAX_DEFER_SEC
        # unchanged.
        defer_cap = OPERATOR_MAX_DEFER_SEC if ch.channel_tag in OPERATOR_CHANNEL_TAGS else MAX_DEFER_SEC
        cur.execute("SELECT now() - %s > make_interval(secs => %s)", (oldest, defer_cap))
        if not cur.fetchone()[0]:
            return
    if newest_id <= getattr(ch, "_last_forced_nudge_id", 0):
        return   # already force-nudged this batch — one forced nudge per message
    # ABSENT-TARGET SKIP (orch-console #77913, 2026-10-27): the Mini has no 'orch' session
    # (the hub lives on gzb), so log-and-route channels pinned here were "force-nudged"
    # into nothing every pass and logged as if delivered (brandit-coffeemedia: 3 rows,
    # 10 days, no reader). Skip a target that isn't on this host, saying so ONCE per
    # (channel, target) per process; the rows stay durable and unhandled for their owner.
    if not _session_exists(target):
        if (ch.key, target) not in _ABSENT_TARGET_LOGGED:
            _ABSENT_TARGET_LOGGED.add((ch.key, target))
            _log_line(f"{ch.key}: {n} unhandled past {defer_cap}s but nudge target "
                      f"'{target}' is not a session on this host — NOT nudged (needs an "
                      f"owner_lane/inject_target, or unpin the channel here)")
        return
    ch._last_forced_nudge_id = newest_id
    # Past the (possibly tightened) defer cap is itself the "stop waiting"
    # signal this exists for — treat it as urgent so an Escape-eligible
    # target (ESCAPE_PREEMPT_TARGETS) actually preempts, not just queues.
    if nudge_session(target, ch.key, n, urgent=True):
        _log_line(f"{ch.key}: {n} unhandled past {defer_cap}s — FORCED nudge (max-latency cap)")
    else:
        _log_line(f"{ch.key}: {n} unhandled past {defer_cap}s — FORCED nudge to "
                  f"'{target}' NOT delivered (fire-window hold or tmux error)")


def reassure_if_unhandled(conn, ch: "Channel") -> None:
    """Belt-and-suspenders ack (ihsan — 2026-07-04): if ANY inbound has sat
    unhandled past ACK_AFTER_SEC, send ONE reassurance — regardless of whether the
    target is busy, idle, or wedged. Closes the gap where a stuck-IDLE hub gave the
    operator neither a reply NOR a busy-ack, leaving him wondering if it landed.
    Dedups against the busy-ack (both start '📨 Got your message') so he gets
    exactly one acknowledgment PER UNHANDLED MESSAGE — never two, never zero.
    (2026-07-05: dedup is since-the-newest-unhandled-inbound, not per throttle
    window — a message stuck for an hour used to re-ack every window, spamming
    8+ identical acks at a dead channel. One ack per message says everything;
    repeats add nothing. drain_stale_deferred still owns waking the target.)"""
    if ch.mode not in ("agent-session", "log-and-route"):
        return
    if ch.channel_tag in SUPPRESS_CLIENT_ACK_TAGS:
        return   # client-ack suppressed for this channel (human hand-responds); drain_stale_deferred still nudges the handler
    ack = (
        # client perimeter (log-and-route): no operator-internal phrasing
        # "shortly" is BANNED on the client perimeter (orch-console, 2026-08-17).
        # It failed twice on the gazzabyte/irsyad account, Nazim told Shuk in writing
        # that it had failed twice — and then this template said it to him a THIRD
        # time, automatically, 3 minutes after a client-flagged URGENT. A vague time
        # promise is worse than none: it spends credibility and buys the reader
        # nothing. State what is TRUE (it is logged and routed) and let the human
        # who picks it up give a real time. The '📨 Got your message' prefix is
        # LOAD-BEARING — reassure_if_unhandled dedups on LIKE '%Got your message%'
        # and tg_out's stale-ack guard startswith()es it. Do not reword the prefix.
        "\U0001F4E8 Got your message — it's logged and someone will come back to you on it."
        if ch.mode == "log-and-route"
        else "\U0001F4E8 Got your message — it's logged and I'll pick it up. "
             "(Reply URGENT to bump it now.)")
    # Operator channels only (Musa op#36085/36098): tighter stale-threshold +
    # a live-status fragment. Client wording/timing above is untouched.
    ack_after = ACK_AFTER_SEC
    if ch.channel_tag in OPERATOR_CHANNEL_TAGS:
        ack_after = OPERATOR_ACK_AFTER_SEC
        target = ch.inject_target if ch.mode == "agent-session" else "orch"
        ack += _live_status_suffix(conn, target)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(created_at), max(created_at), "
            "(array_agg(chat_id ORDER BY created_at DESC))[1] FROM operator_messages "
            "WHERE direction='inbound' AND tag=%s AND handled_at IS NULL",
            (ch.channel_tag,))
        n, oldest, newest, chat_id = cur.fetchone()
        if not oldest:
            return
        cur.execute("SELECT now() - %s > make_interval(secs => %s)", (oldest, ack_after))
        if not cur.fetchone()[0]:
            return   # not stale enough yet — a fast reply needs no ack

        # GROUP ADDRESSING (bus #47483): this is an AGGREGATE sweep with no live
        # msg object, so it can't check entities/reply-to-bot per message like
        # throttled_busy_ack does. Best effort: only suppress when we positively
        # know (a) the chat is a group and (b) the bot's own username, AND no
        # unhandled row's stored text mentions it — any lookup failure or
        # unknown stays on the pre-fix behavior (ack fires), never new silence.
        # A quoted reply-TO-the-bot can't be reconstructed from stored text
        # alone once getUpdates has consumed the parent — an acknowledged,
        # disclosed gap for this aggregate path only (throttled_busy_ack, which
        # runs per-message with the live update, still catches that case).
        if chat_id is not None and _chat_type(ch.token, chat_id) in ("group", "supergroup"):
            bot_username = _bot_identity(ch.token).get("username")
            if bot_username:
                cur.execute(
                    "SELECT 1 FROM operator_messages WHERE direction='inbound' AND tag=%s "
                    "AND handled_at IS NULL AND text ILIKE %s LIMIT 1",
                    (ch.channel_tag, f"%@{bot_username}%"))
                if not cur.fetchone():
                    return   # group, unhandled, nothing addressed to the bot — stay quiet
        cur.execute(
            "SELECT 1 FROM tg_out WHERE channel_key=%s AND text LIKE %s "
            "AND created_at > %s LIMIT 1",
            (ch.key, "%Got your message%", newest))
        if cur.fetchone():
            return   # this batch already acked once (busy-ack OR reassurance)
        cur.execute("INSERT INTO tg_out (channel_key, text) VALUES (%s,%s)", (ch.key, ack))
        conn.commit()
    _log_line(f"{ch.key}: {n} unhandled past {ack_after}s — reassurance ack (belt+suspenders)")


# ── Per-update processing (sync, called from the channel task) ────────────────


# Allowlisted button actions. KEY -> (script, human label). Adding one is a deliberate
# code change with review; callback data is matched against these keys EXACTLY and is
# never interpolated into a shell command.
# Repo root, resolved from this file rather than a cwd assumption — the ingest runs under
# launchd, where cwd is not the orchestrator directory.
_ORCH = pathlib.Path(__file__).resolve().parent.parent

BUTTON_ACTIONS = {
    "reset_nazim": ("scripts/reset_nazim.sh", "clear Nazim (console)"),
    "reset_cai":   ("scripts/reset_cai.sh",   "clear cai (governance)"),
    "reset_orch":  ("scripts/reset_hub_remote.sh",  "clear the hub (VPS)"),
}


def _answer_callback(token: str, cb_id: str, text: str) -> None:
    """Always answer — an unanswered callback leaves a spinner on the operator's button."""
    try:
        tg_call(token, "answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:190]})
    except Exception:                                              # noqa: BLE001
        pass


def _handle_callback(conn, ch: "Channel", cb: dict, upd_id: int) -> bool:
    action = (cb.get("data") or "").strip()
    frm = cb.get("from") or {}
    presser = str(frm.get("id") or "")
    operator = (os.environ.get("MUSA_TELEGRAM_ID") or "").strip()
    label = BUTTON_ACTIONS.get(action, (None, action))[1]

    if presser != operator or not operator:
        _log_line(f"{ch.key}: REFUSED button '{action}' from {presser!r} (not the operator)")
        _answer_callback(ch.token, cb.get("id", ""), "Not authorised.")
        return True
    # TG2.0 section 6: new-contact Approve / Ignore. Handled BEFORE the
    # BUTTON_ACTIONS allowlist because its data carries an opaque pending id,
    # not a fixed action name. The operator check above is already the
    # deny-by-default gate for the reviewer's own chat; handle_button re-checks
    # it and then verifies the pending row is live before touching anything.
    if action.startswith("pc:"):
        res = pending_contacts.handle_button(
            conn, data=action, presser=presser, reviewer_chat=operator,
            file_gate_request=pending_contacts.file_gate_request_via_bus)
        _log_line(f"{ch.key}: new-contact button {action!r} from {presser!r} -> {res}")
        if res.get("ok") and res.get("action") == "approve":
            note = "Approved — gate request filed."
        elif res.get("ok"):
            note = "Ignored."
        elif res.get("reason") == "gate_not_filed":
            note = "Could not file the gate request — the contact is still pending, try again."
        else:
            note = "Nothing to do."
        _answer_callback(ch.token, cb.get("id", ""), note)
        return True
    if action not in BUTTON_ACTIONS:
        _log_line(f"{ch.key}: REFUSED unknown button action {action!r}")
        _answer_callback(ch.token, cb.get("id", ""), "Unknown action.")
        return True

    # Idempotency (op#8989, the double-/clear the operator caught 2026-08-01):
    # a callback_query can be REDELIVERED — the poll offset is acked only after a
    # whole batch is durable (see the channel loop), so a restart or transient
    # error between running this reset and acking the offset makes Telegram resend
    # the same update. Message updates are absorbed by the A1 ingest_dedup gate,
    # but this callback path returns BEFORE reaching it — so a redelivered BUTTON
    # ran the destructive reset script twice. Claim the update_id here too, and
    # COMMIT before running: only the winning insert runs the script; a replay
    # just re-answers the callback (clears the spinner) and returns. Covers all
    # three reset buttons (nazim/cai/hub) — they share this handler.
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingest_dedup (channel_key, telegram_update_id) "
            "VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING channel_key",
            (ch.key, upd_id),
        )
        won = cur.fetchone() is not None
    conn.commit()
    if not won:
        _log_line(f"{ch.key}: callback '{action}' upd={upd_id} already processed — skip re-run")
        _answer_callback(ch.token, cb.get("id", ""), f"{label}: already handled")
        return True

    script = _ORCH / BUTTON_ACTIONS[action][0]
    _log_line(f"{ch.key}: operator pressed '{action}' -> {script}")
    _answer_callback(ch.token, cb.get("id", ""), f"Running: {label}…")
    try:
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=180)
        ok = r.returncode == 0
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        detail = tail[-1][:180] if tail else ""
    except Exception as e:                                          # noqa: BLE001
        ok, detail = False, str(e)[:180]
    _log_line(f"{ch.key}: '{action}' finished ok={ok} {detail}")
    # Report the OUTCOME, not the attempt — the script's own guards may refuse (mid-task,
    # missing handoff), and a button that says "done" when the guard refused is the same
    # decoration defect as delivered=true on a failed send.
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_messages (direction, channel, chat_id, tag, text, delivered, "
                "from_name) VALUES ('outbound','telegram',%s,%s,%s,%s,'ingest-button')",
                (str((cb.get("message") or {}).get("chat", {}).get("id") or ""), ch.channel_tag,
                 f"[button] {label}: {'DONE' if ok else 'REFUSED/FAILED'} — {detail}", ok))
            conn.commit()
    except Exception:                                               # noqa: BLE001
        pass
    tg_call(ch.token, "sendMessage",
            {"chat_id": presser,
             "text": (f"{'✅' if ok else '⚠️'} {label}: "
                      f"{'done' if ok else 'refused or failed'}\n{detail}")})
    return True


def process_update(conn, ch: Channel, upd: dict,
                   channels: dict[str, Channel] | None = None) -> bool:
    """A1→LOG→GATE→ROUTE for one getUpdates entry. Returns True if this
    process 'won' the dedupe insert (i.e. the update was newly processed).
    `channels` (the daemon's live channel map) is only used by the invite
    deep-link bind for its hot in-memory allowlist update; None = DB-only
    (the CONFIG_REFRESH reload picks the bind up within 60s)."""
    upd_id = upd["update_id"]

    # ── INLINE BUTTON ACTIONS (op#7326) ──────────────────────────────────────────────
    # The operator asked for a button he can press in Telegram instead of remoting into
    # the Mini to clear a body. Deliberately NARROW: an allowlist of named actions, each
    # mapped to a script — callback data never becomes a command, a path, or an argument.
    #
    # WHY THE INGEST AND NOT THE CONSOLE: the console cannot reset ITSELF (CAI-500 cond 4,
    # and a body that can clear its own context can clear itself out of an instruction).
    # The ingest is a separate always-on process, so it can act on a body that is mid-turn.
    #
    # AUTHORISATION is the same trust model as an operator message: the callback carries
    # from.id, and only MUSA_TELEGRAM_ID is honoured. Anyone else pressing a forwarded
    # button gets a refusal and an audit row.
    if "callback_query" in upd:
        return _handle_callback(conn, ch, upd["callback_query"], upd_id)

    msg = upd.get("message") or upd.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    # Sender identity (BOT-INGEST-SENDER-001): Telegram carries the individual
    # sender in message.from — capture it so a GROUP message (negative chat_id)
    # can be attributed to the human who sent it, not blanket-read as the operator.
    # All-nullable: a service/channel-post update with no `from` logs NULLs.
    frm = msg.get("from") or {}
    username = frm.get("username")
    from_user_id = str(frm["id"]) if frm.get("id") is not None else None
    from_username = frm.get("username")
    from_name = (" ".join(
        p for p in (frm.get("first_name"), frm.get("last_name")) if p).strip()
        or None)

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
        content, media_meta = message_content_with_media(ch, msg, upd_id)
        # GROUP->SUPERGROUP MIGRATION (orch-console bus #50332): the durable
        # row above already captures the new chat_id (message_content), but a
        # row sitting in the log is easy to miss. Page loudly too, same
        # page-once-ever discipline as pinned-channel drift, so this doesn't
        # go silently deaf once Telegram actually cuts the chat_id over.
        if msg.get("migrate_to_chat_id") is not None:
            try:
                _page_migrate_to_chat_id_once(conn, ch, chat_id, msg["migrate_to_chat_id"], upd_id)
            except Exception as e:
                _log_line(f"{ch.key}: migrate_to_chat_id page failed "
                          f"({type(e).__name__}: {e})")
        # PRIVATE-FAMILY routing (bus #47837 C1/C2): a personal-routed tag's
        # content must never reach the substrate. Skip classify() entirely —
        # its governance-fork branch would quote a matched phrase from the
        # text into cos_triage (a named leak path), and the channel tag alone
        # already fixes the domain deterministically (triage._CHANNEL_TAGS).
        # `stored_content` is what the SUBSTRATE row gets; `content` (the real
        # text) is only ever passed to write_personal_content() below.
        personal_routed = personal_routing.is_personal_routed(ch.channel_tag)
        if personal_routed:
            cos_triage = personal_routing.SENTINEL_COS_TRIAGE
            stored_content = personal_routing.SENTINEL_TEXT
            # cc-quality PR #240 HIGH (orch-console ruling, 2026-10-01: sender
            # identity IS in-silo for a personal-routed tag): the envelope is
            # "content-free" for identity too, not just text. Telegram's
            # from_user_id/from_username/from_name are metadata, not text —
            # they weren't text-derived so they slipped the original C1/C2 net.
            # The real values still reach wingmen-personal unchanged (see the
            # write_personal_content() call below, which closes over the
            # original from_user_id/from_username/from_name locals directly,
            # not these stored_ ones) — speaker identity is resolved on the
            # personal side only (286619815->Musa, her id->Zahidah, else
            # unknown), never from the substrate row.
            stored_from_user_id = None
            stored_from_username = None
            stored_from_name = None
            # The file_id is a RETRIEVAL KEY, not metadata like sender identity —
            # anyone with substrate read access + the bot token could re-download
            # the personal content through it. Same containment as stored_content:
            # NEVER written to the substrate row for a personal-routed tag. (The
            # real media, if any, is best-effort-lost here same as before this
            # change — wiring media_refs into write_personal_content() is a
            # separate, out-of-scope fix for the personal silo, not this
            # substrate-only directive, bus #74111.)
            stored_file_id = None
            stored_file_unique_id = None
            stored_media_kind = None
            stored_media_status = None
        else:
            # PASSIVE CoS triage (Step 1): compute the read-only route suggestion and
            # store it in the additive `cos_triage` column. STRICTLY additive — it
            # never routes, sends, or gates. Dead-man's-switch: any classifier error
            # → cos_triage NULL → today's manual behavior, LOG still succeeds. The
            # text-based route (msg.text) is used, not the media pointer, so a
            # screenshot's caption still triages.
            try:
                cos_triage = json.dumps(triage.classify(
                    msg.get("text") or msg.get("caption") or content,
                    tag=ch.channel_tag).to_dict())
            except Exception:
                cos_triage = None
            stored_content = content
            stored_from_user_id = from_user_id
            stored_from_username = from_username
            stored_from_name = from_name
            # bus #74111: persist the Telegram file_id identity in THIS SAME
            # insert regardless of download outcome — media_meta is built
            # before the except branch sets content to a "download failed"
            # marker, so a failed download still leaves a re-fetchable
            # pointer instead of a dead end (message_queue.file_id was the
            # prior, abandoned attempt at this: dead since 2026-04-16,
            # unused by this ingest).
            if media_meta is None:
                stored_file_id = stored_file_unique_id = stored_media_kind = None
                stored_media_status = None
            else:
                stored_file_id = media_meta["file_id"]
                stored_file_unique_id = media_meta["file_unique_id"]
                stored_media_kind = media_meta["kind"]
                stored_media_status = "ok" if media_meta["ok"] else "failed"
        cur.execute(
            "INSERT INTO operator_messages "
            "(direction, channel, chat_id, tag, text, delivered, "
            " from_user_id, from_username, from_name, cos_triage, "
            " file_id, file_unique_id, media_kind, media_status) "
            "VALUES ('inbound','telegram',%s,%s,%s,true,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (str(chat_id) if chat_id is not None else None, ch.channel_tag,
             stored_content, stored_from_user_id, stored_from_username,
             stored_from_name, cos_triage,
             stored_file_id, stored_file_unique_id, stored_media_kind, stored_media_status),
        )
        op_msg_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE ingest_dedup SET operator_msg_id=%s "
            "WHERE channel_key=%s AND telegram_update_id=%s",
            (op_msg_id, ch.key, upd_id),
        )
        # C1 (bus #47837): substrate envelope is NOT committed yet. The real
        # content write to wingmen-personal must succeed and be committed
        # FIRST — if it raises, roll back the envelope (+ the dedupe row, same
        # txn) and re-raise. The caller (channel_loop) then never reaches the
        # poll-offset UPDATE for this batch (Telegram redelivers, A1 dedupe
        # absorbs the replay) — same offset fail-safe shape the file already
        # uses for a DB-unreachable error. channel_loop's `except Exception`
        # handler logs loudly AND increments ingest_poll_health.consec_errors
        # for this channel, which scripts/channel_liveness_watchdog.py already
        # pages on past ERROR_STORM (6) — no new paging mechanism needed. Not
        # best-effort: a swallowed failure here would silently orphan an
        # envelope with no content behind it.
        if personal_routed:
            try:
                personal_routing.write_personal_content(
                    op_msg_id, direction="inbound", channel="telegram",
                    tag=ch.channel_tag, text=content, chat_id=chat_id,
                    from_user_id=from_user_id, from_username=from_username,
                    from_name=from_name, tg_message_id=msg.get("message_id"),
                )
            except Exception as e:                                     # noqa: BLE001
                conn.rollback()
                _log_line(f"{ch.key}: PERSONAL ROUTE WRITE FAILED update {upd_id} "
                          f"({type(e).__name__}: {e}) — substrate rolled back, "
                          f"offset NOT advanced, Telegram will redeliver")
                raise
        conn.commit()

    # 2b. PRE-GATE — self-serve invite deep-link bind (op#34846): a '/start'
    # while an unused+unexpired invite is outstanding on this token is
    # admission-control (bind or generic refusal), handled AFTER the durable
    # LOG above and INSTEAD of routing. The row needs no operator action
    # either way, so it is stamped handled exactly like a gated row.
    if (msg.get("text") or "").strip().lower().startswith("/start"):
        if try_invite_bind(conn, ch, channels, msg, op_msg_id):
            try:
                with conn.cursor() as _icur:
                    _icur.execute(
                        "UPDATE operator_messages SET handled_at=now() "
                        "WHERE id=%s AND handled_at IS NULL",
                        (op_msg_id,))
                conn.commit()
            except Exception:                                   # noqa: BLE001
                pass
            return True

    # 3. GATE — deny-by-default; disallowed stays logged-and-skipped.
    if chat_id is None or not gate_allows(ch, chat_id, username):
        _log_line(f"{ch.key}: update {upd_id} gated (chat {chat_id}) — logged, not routed")
        # Stamp handled_at on the gated row: it is logged for AUDIT but needs NO
        # operator action, so it must NOT count toward the per-channel reassure/
        # drain unhandled-count NOR the hub's operator_log.unprocessed() queue.
        # (2026-09-16: an unauthorized user's '/start' on operator-orch was counted
        # by reassure_if_unhandled and fired a "📨 Got your message" ack into the
        # OPERATOR's own chat — "i didnt message you". A gated message is handled=
        # nothing-to-do.) Best-effort: a failed stamp never blocks the gate return.
        try:
            with conn.cursor() as _gcur:
                _gcur.execute(
                    "UPDATE operator_messages SET handled_at=now() "
                    "WHERE id=%s AND handled_at IS NULL",
                    (op_msg_id,))
            conn.commit()
        except Exception:
            pass
        return True

    # 3b. ASKS LEDGER (op#22669): only for a channel/tag this ledger actually
    # watches (nervous_system.operator_log._is_operator_ask_surface — hub's
    # 'orch-channel', Nazim's 'nazim-console'; a no-op for every other channel
    # this daemon polls, incl. client channels). A genuine Telegram reply to one
    # of OUR outbound messages auto-closes the linked ask instead of opening a
    # new untracked one. Best-effort: never let a tracking hiccup block routing.
    try:
        rep = msg.get("reply_to_message") or {}
        reply_to_tg_message_id = rep.get("message_id")
        operator_log.maybe_track_ask(
            op_msg_id, "inbound", "telegram", ch.channel_tag, content,
            from_user_id=from_user_id, reply_to_tg_message_id=reply_to_tg_message_id,
        )
    except Exception as e:  # noqa: BLE001
        _log_line(f"{ch.key}: asks-ledger tracking raised on update {upd_id} "
                  f"({type(e).__name__}: {e}) — non-fatal")

    # 3c. CLIENT ASKS LEDGER (migration 085, Musa op#23944, bus #47105->#47114):
    # every inbound on a bot_channels.audience='client' channel opens its own
    # operator_asks row (ask_surface='client-channel', delegated_to=ch.owner_lane,
    # REQUIRED chase_by) — the gap that let Shuq's 44h-unanswered ask go
    # untracked. Best-effort: a tracking hiccup here must not block routing.
    if ch.audience == "client":
        try:
            operator_log.maybe_track_client_ask(op_msg_id, content, ch.owner_lane)
        except Exception as e:  # noqa: BLE001
            _log_line(f"{ch.key}: client-ask tracking raised on update {upd_id} "
                      f"({type(e).__name__}: {e}) — non-fatal")

    # 3d. STAGE-FILE AUTO-ROUTE (orch-console #51060, Musa op#25437-25440): an
    # inbound FILE (not a GIF) on a client channel auto-pages orch-console to
    # stage it — lanes/humans no longer have to remember to ask. irsyad
    # channels are excluded (coord's own import pipeline owns those).
    # _download_media is idempotent on disk (_media_content already downloaded
    # this exact file above), so this just resolves the same cached path, no
    # re-download. Best-effort: a staging hiccup must not block routing.
    if is_staged_file_eligible(ch, msg):
        try:
            doc = msg["document"]
            local_path = _download_media(ch.token, doc["file_id"], doc["file_unique_id"],
                                          ch.key, upd_id, doc.get("file_name"))
            _page_stage_file_once(conn, ch, upd_id, op_msg_id, local_path, msg.get("caption") or "")
        except Exception as e:  # noqa: BLE001
            _log_line(f"{ch.key}: STAGE-FILE auto-route raised on update {upd_id} "
                      f"({type(e).__name__}: {e}) — non-fatal")

    # 4. ROUTE (transport only — A2) with busy-aware nudge policy (CAI-RESP-382).
    if ch.mode in ("agent-session", "log-and-route"):
        target = ch.inject_target if ch.mode == "agent-session" else "orch"
        text = msg.get("text") or msg.get("caption") or ""
        urg = urgency(text)
        if urg == "btw":
            _log_line(f"{ch.key}: BTW — logged, not nudged (drains at next idle)")
        elif urg == "urgent" or ch.nudge_when_busy or not pane_working(target):
            nudge_session(target, ch.key, unread_count(conn, ch), urgent=(urg == "urgent"))
        elif _is_group_chat(msg) and not _bot_is_addressed(ch, msg):
            # group banter not addressed to the bot (bus #47483): still logged
            # + routed above, just no '📨 Got your message' ack into the group.
            _log_line(f"{ch.key}: group chat, bot not addressed — busy ack suppressed")
        else:
            # target WORKING + default priority: DEFER (no interrupt). Delivery
            # then relies on the target running Option B unprocessed() each turn
            # (true for orch/cai — the only agent-session targets today) OR a
            # later message riding the accumulated unread_count when the pane
            # next reads idle. Busy-defer is only sound for Option-B targets;
            # a non-reconciling inject_target would need an idle-drain sweep.
            throttled_busy_ack(conn, ch)
            _log_line(f"{ch.key}: '{target}' WORKING — nudge deferred (Option B delivers), throttled ack")
    # ai-responder: nothing — responder_runner drains the log (A2).
    return True


def _maybe_page_new_contact(conn, ch: "Channel", upd: dict, matched) -> None:
    """TG2.0 s6: an inbound from a chat no row claims pages the reviewer ONCE.

    Deny-by-default is unchanged: the message is still logged and gated exactly
    as before, nothing is enabled, and this only tells the reviewer a stranger
    turned up. Best-effort — an alert failure must never block routing.
    """
    if matched is not None:
        return
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    reviewer = (os.environ.get("MUSA_TELEGRAM_ID") or "").strip()
    if chat_id is None or not reviewer:
        return
    frm = msg.get("from") or {}
    try:
        # Savepoint: a failure here (e.g. the gated migration not yet applied)
        # must not abort the caller's transaction — that would silently break
        # the GATE step that follows and turn a cosmetic alert into a routing
        # defect.
        with conn.transaction():
            note = pending_contacts.record_unmatched(
                conn, bot=ch.token_env_key, chat_id=chat_id, channel_tag=ch.channel_tag,
                first_name=frm.get("first_name"), username=frm.get("username"))
        if not note:
            return                      # already pending and already alerted
        text, keyboard = pending_contacts.alert(
            bot=ch.token_env_key, chat_id=note["chat_id"], first_name=note["first_name"],
            username=note["username"], channel_tag=note["channel_tag"],
            pending_id=note["pending_id"])
        # tg_call URL-encodes its params, so reply_markup must be the JSON
        # STRING Telegram's Bot API expects. Passing the dict raised
        # "can't parse reply keyboard markup JSON object" (HTTP 400), which the
        # except below swallowed as non-fatal — the alert never sent at all.
        tg_call(ch.token, "sendMessage",
                {"chat_id": reviewer, "text": text,
                 "reply_markup": json.dumps(keyboard)})
    except Exception as e:                                        # noqa: BLE001
        _log_line(f"{ch.key}: new-contact alert raised ({type(e).__name__}: {e}) — non-fatal")


def unread_count(conn, ch: Channel) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM operator_messages "
            "WHERE direction='inbound' AND tag=%s AND handled_at IS NULL",
            (ch.channel_tag,),
        )
        return cur.fetchone()[0]


# ── Token task: long-poll loop w/ DB-held offset, ONE per unique token ────────
# (op#31298/31307 one-bot-many-clients: a solo-channel token group is this
# exact loop, unchanged — grouping only matters once 2+ channel_keys share a
# token_env_key.)

async def _run_token_cycle(token_env_key: str, channels: dict[str, Channel],
                            last_db_alert: float) -> tuple[float, float | None]:
    """One getUpdates cycle for a token's group. Returns (last_db_alert,
    sleep_for) — sleep_for is None when the caller should proceed straight
    into the next cycle (getUpdates' own long-poll already paced it), or a
    number of seconds otherwise. Split out from channel_loop so a test can
    drive exactly one cycle without an infinite loop (tg_call is the only
    real-network call — monkeypatch it)."""
    # Re-derived every cycle from the live (main()-refreshed) channels dict
    # — a channel joining/leaving this token's group takes effect on the
    # NEXT cycle with no restart and no task replacement needed.
    group = [ch for ch in channels.values() if ch.token_env_key == token_env_key]
    if not group:
        _log_line(f"{token_env_key}: no channels left — loop exiting")
        return last_db_alert, -1.0   # sentinel: caller returns, task retires
    token = group[0].token
    if not token:
        _log_line(f"{token_env_key}: token env empty — sleeping (graceful, no crash-loop)")
        return last_db_alert, CONFIG_REFRESH
    overlap = overlapping_chat_ids(group)
    if overlap:
        _log_line(f"WATCHDOG: {token_env_key}: overlapping allowed_chat_ids across "
                  f"{sorted(c.key for c in group)} ({overlap}) — refusing to poll until fixed")
        return last_db_alert, ERROR_BACKOFF
    misconfigured = empty_allowlist_rows(group)
    if misconfigured:
        # op#34846 fast-follow (console-revamp #78815): an empty allowlist is
        # the DESIGNED pre-bind state while an unused, unexpired invite is
        # outstanding — the pre-GATE bind hook answers the deep-link tap, so
        # the "never match any message" claim below is stale for exactly that
        # window. The check costs one connection only when a group actually
        # has an empty-allowlist row.
        # (#78884, orch-console 78883 — GO condition on PR #556): this
        # connect runs inside the inbound daemon cycle, and the Mini
        # demonstrably loses DNS for minutes at a time — any failure here
        # logs one line and falls back to the pre-fix behavior (keep the
        # warning). Fail loud, never crash the poller.
        try:
            with psycopg.connect(_dsn()) as wconn:
                misconfigured = [k for k in misconfigured if not invite_pending(wconn, k)]
        except Exception as exc:  # noqa: BLE001 — the whole point is "never crash here"
            _log_line(f"WATCHDOG: invite-pending check failed ({type(exc).__name__}) -- "
                      "warning on all empty-allowlist rows")
    for misconfigured_key in misconfigured:
        _log_line(f"WATCHDOG: {token_env_key}: '{misconfigured_key}' has an EMPTY "
                  f"allowed_chat_ids/allowed_usernames in a {len(group)}-channel "
                  "token group — it will never match any message")
    try:
        params = {"timeout": POLL_TIMEOUT,
                  "allowed_updates": '["message","edited_message","callback_query"]'}
        # Telegram's offset is per-BOT (one token), not per-chat — every row
        # sharing this token advances in lockstep, so any one of them carries
        # the shared cursor; max() tolerates a row that drifted (manual edit).
        offsets = [c.poll_offset for c in group if c.poll_offset is not None]
        if offsets:
            params["offset"] = max(offsets) + 1
        updates = await asyncio.to_thread(tg_call, token, "getUpdates", params)
        with psycopg.connect(_dsn()) as conn:
            for upd in (updates or []):
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                username = (msg.get("from") or {}).get("username")
                matched = match_channel_in_group(group, chat_id, username)
                # Unmatched (no row in the group claims this chat): still log it,
                # under the group's first row — same as today's single-channel
                # "random stranger messages the bot" case (logged, then gated,
                # never routed). group[0] is deterministic (sorted by key).
                process_update(conn, matched or group[0], upd, channels)
                _maybe_page_new_contact(conn, matched or group[0], upd, matched)
            if updates:
                # Offset fail-safe: ack ONLY after the whole batch is durable.
                new_off = updates[-1]["update_id"]
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE bot_channels SET poll_offset=%s, updated_at=now() "
                        "WHERE token_env_key=%s", (new_off, token_env_key))
                conn.commit()
                for c in group:
                    c.poll_offset = new_off
            for ch in group:
                # CAI-RESP-382 max-latency cap — runs every cycle (incl. empty
                # polls) so a message deferred while the agent stays busy still
                # nudges once it crosses MAX_DEFER_SEC. This is what was missing
                # when the operator waited 45 min on a deferred message.
                drain_stale_deferred(conn, ch)
                # Belt-and-suspenders (ihsan): shorter-fuse reassurance so the
                # operator is acked within ACK_AFTER_SEC even when the hub is
                # stuck-idle (neither replying nor busy) — never left wondering.
                reassure_if_unhandled(conn, ch)
                # STEP-0 poll-health (channel-liveness watchdog; SRE 38560): record a
                # SUCCESSFUL getUpdates cycle, per channel_key (health stays keyed
                # per logical channel even though one getUpdates served the whole
                # group) — last_ok_at is the liveness clock the rung-1 reader keys
                # on, consec_errors resets to 0.
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ingest_poll_health "
                        "(channel_key, host, last_ok_at, consec_errors, last_error, updated_at) "
                        "VALUES (%s, %s, now(), 0, NULL, now()) "
                        "ON CONFLICT (channel_key, host) DO UPDATE SET "
                        "last_ok_at=now(), consec_errors=0, last_error=NULL, updated_at=now()",
                        (ch.key, socket.gethostname()))
            conn.commit()
        return last_db_alert, None
    except psycopg.Error as e:
        # DB unreachable: offset stays un-acked → Telegram will redeliver →
        # A1 dedupe absorbs. Surface loudly for the watchdog (CAI-RESP-357).
        now = time.monotonic()
        if now - last_db_alert > DB_ALERT_EVERY:
            _log_line(f"WATCHDOG: substrate DB unreachable from ingest ({type(e).__name__}: {e}) — channels paused, offsets held")
            last_db_alert = now
        return last_db_alert, ERROR_BACKOFF
    except Exception as e:
        _log_line(f"{token_env_key}: loop error {type(e).__name__}: {e}")
        # STEP-0 poll-health (SRE 38560): count this FAILED poll (the Errno-54 /
        # 409-conflict path) against EVERY channel_key in the group — the failure
        # (e.g. a stray second poller on this token) affects all of them, not just
        # one. Short-lived conn — the success block's conn isn't open here — in its
        # OWN try/except so a poll-health write failure can never crash or mask the
        # loop's error handling. last_ok_at deliberately NOT touched: preserve the
        # last REAL success time (the watchdog's liveness clock).
        try:
            with psycopg.connect(_dsn()) as _hconn, _hconn.cursor() as _hcur:
                for ch in group:
                    _hcur.execute(
                        "INSERT INTO ingest_poll_health "
                        "(channel_key, host, last_ok_at, consec_errors, last_error, updated_at) "
                        "VALUES (%s, %s, NULL, 1, %s, now()) "
                        "ON CONFLICT (channel_key, host) DO UPDATE SET "
                        "consec_errors = ingest_poll_health.consec_errors + 1, "
                        "last_error = EXCLUDED.last_error, updated_at = now()",
                        (ch.key, socket.gethostname(), f"{type(e).__name__}: {e}"[:200]))
                _hconn.commit()
        except Exception:
            pass  # poll-health write is best-effort; never mask the real loop error
        return last_db_alert, ERROR_BACKOFF


async def channel_loop(token_env_key: str, channels: dict[str, Channel]):
    """Thin driver: re-derives the token's group fresh every cycle from the
    live `channels` dict, so a channel joining/leaving the group takes effect
    on the next cycle with no restart (hot-reload for free)."""
    last_db_alert = 0.0
    while True:
        last_db_alert, sleep_for = await _run_token_cycle(token_env_key, channels, last_db_alert)
        if sleep_for == -1.0:
            return
        if sleep_for:
            await asyncio.sleep(sleep_for)


def _run_pinned_channel_drift_check() -> None:
    """Isolated from load_channels' try/except on purpose: the drift check is
    a side observation, never load-bearing for ingest itself, and must never
    cost the main() loop an ERROR_BACKOFF cycle (or worse, a `continue` that
    skips starting/refreshing channel tasks) if IT fails for any reason (e.g.
    a transient bus_send DB hiccup unrelated to bot_channels). Swallows and
    logs everything — ingest keeps running regardless."""
    try:
        with psycopg.connect(_dsn()) as conn:
            check_pinned_channels_not_enabled(conn)
    except Exception as e:
        _log_line(f"WATCHDOG: pinned-channel-drift check failed ({type(e).__name__}: {e}) — ingest continues")


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
        _run_pinned_channel_drift_check()
        # carry live offsets forward; start/stop tasks to match config
        for k, ch in fresh.items():
            if k in channels and channels[k].poll_offset is not None and (
                    ch.poll_offset is None or ch.poll_offset < channels[k].poll_offset):
                ch.poll_offset = channels[k].poll_offset
            # Carry the forced-nudge dedup marker forward too, else recreating the
            # Channel every refresh resets it and re-fires a forced nudge for the
            # same message (the 3-nudges-for-one-message bug, 2026-07-08).
            if k in channels:
                ch._last_forced_nudge_id = getattr(channels[k], "_last_forced_nudge_id", 0)
        channels.clear()
        channels.update(fresh)
        # One task per UNIQUE token_env_key, not per channel_key (op#31298
        # one-bot-many-clients) — a brand-new token spawns a task here; an
        # EXISTING token's group picking up a new/removed channel_key needs
        # no respawn, since channel_loop re-derives its group from `channels`
        # every cycle (true hot-reload, no restart required).
        for token_env_key in list(group_by_token(channels)):
            if token_env_key not in tasks or tasks[token_env_key].done():
                tasks[token_env_key] = asyncio.create_task(channel_loop(token_env_key, channels))
                _log_line(f"{token_env_key}: token task up")
        await asyncio.sleep(CONFIG_REFRESH)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
