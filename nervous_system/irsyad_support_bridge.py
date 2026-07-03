#!/usr/bin/env python3
"""irsyad_support_bridge.py — scoped PERIMETER Telegram bridge for the
Irsyad-Support / Gazzabyte ticket-approval bot.

This bot lives in an EXTERNAL VENDOR group (Gazzabyte). It is a security-
sensitive PERIMETER component, so it is DENY-BY-DEFAULT scoped: it operates in
exactly ONE chat (the Gazzabyte group) and ignores every other chat_id. It does
NOT generate any fleet-wide responses, never surfaces cross-client / personal /
fleet data — it only LOGS each inbound message and ROUTES it to cc-orchestrator
(the human-owned hub) for handling.

Flow for each inbound (and ONLY from the Gazzabyte group chat_id):
  1. LOG every inbound message to operator_messages (durable — nothing lost),
     tagged 'gazzabyte-irsyad' (scoped tag) with the originating chat_id.
  2. If a live `orch` tmux session exists -> INJECT via send-keys, prefixed
     "📩 Irsyad-Support (Gazzabyte group): " so cc-orch recognises it.
  3. Else -> graceful HEADLESS path: the message is already logged; cc-orch
     picks it up on its next reconciliation breath. No auto-reply is generated
     (the hub owns all outbound — this bridge never speaks for the fleet).

Security:
  - Token read from env IRSYAD_SUPPORT_BOT_TOKEN (.env). If MISSING/empty the
    bridge logs a clear message and exits GRACEFULLY — it must NOT crash-loop
    under launchd before the operator wires the token via BotFather.
  - Scope-gate: IRSYAD_SUPPORT_GROUP_CHAT_ID is the ONLY honoured chat. Empty =>
    accept NO chats (logged-and-skipped). The token is NEVER echoed.

This is a SEPARATE bridge from tg_bridge.py / cai_bridge.py: different bot
token, different offset file. It never touches the operator or cai bridges.
"""
from __future__ import annotations
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

from nervous_system import operator_log

ORCH = pathlib.Path(__file__).resolve().parent.parent

# Scoped tag stamped on every Gazzabyte/Irsyad-Support message in operator_messages.
CHANNEL_TAG = "gazzabyte-irsyad"

# Prefix on the text injected into the orch session so cc-orch recognises the source.
INJECT_PREFIX = "📩 Irsyad-Support (Gazzabyte group): "


def _env(key: str) -> str | None:
    env_path = ORCH / ".env"
    if not env_path.exists():
        return None
    for ln in open(env_path):
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    return None


TOKEN = _env("IRSYAD_SUPPORT_BOT_TOKEN")
# The ONLY chat this bridge will act on (deny-by-default perimeter scope).
GROUP_CHAT_ID = (_env("IRSYAD_SUPPORT_GROUP_CHAT_ID") or "").strip()
# Route INTO the cc-orchestrator hub session (NOT cai, NOT a vendor lane).
TMUX_TARGET = os.environ.get("IRSYAD_SUPPORT_BRIDGE_TMUX_TARGET", "orch")
# "=" forces an EXACT tmux match (tmux otherwise prefix/glob-matches -t).
TMUX_EXACT = "=" + TMUX_TARGET
OFFSET_FILE = ORCH / "logs" / ".irsyad_support_bridge_offset"
# Max chars to TYPE into the orch TUI; longer messages inject a preview + pointer
# (full text always lives in operator_messages).
_INJECT_CAP = 150
API = f"https://api.telegram.org/bot{TOKEN}"


def _tmux_socket() -> str | None:
    """Find the live tmux server socket. Under launchd the default socket-path
    computation can miss the user's server (it lives at a fixed /private/tmp
    path, not under $TMPDIR), so we probe a hint file + the standard locations."""
    cands: list[str] = []
    hint = ORCH / "logs" / ".orch_tmux_socket"
    if hint.exists() and hint.read_text().strip():
        cands.append(hint.read_text().strip())
    uid = os.getuid()
    cands += [f"/private/tmp/tmux-{uid}/default", f"/tmp/tmux-{uid}/default"]
    td = os.environ.get("TMPDIR", "").rstrip("/")
    if td:
        cands.append(f"{td}/tmux-{uid}/default")
    for s in cands:
        if os.path.exists(s):
            return s
    return None


def _tmux_bin() -> str:
    """Absolute tmux path — launchd's PATH (/usr/bin:/bin:/usr/sbin:/sbin) does
    NOT include /usr/local/bin or /opt/homebrew/bin, so a bare "tmux" raises
    FileNotFoundError under the service."""
    for p in ("/usr/local/bin/tmux", "/opt/homebrew/bin/tmux", "/usr/bin/tmux"):
        if os.path.exists(p):
            return p
    return "tmux"


def _tmux(*args) -> list[str]:
    sock = _tmux_socket()
    base = [_tmux_bin()] + (["-S", sock] if sock else [])
    return base + list(args)


def get_updates(offset: int):
    url = f"{API}/getUpdates?timeout=10" + (f"&offset={offset}" if offset else "")
    req = urllib.request.Request(url, headers={"User-Agent": "wingmen-bridge/1.0 (+substrate)"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r).get("result", [])


def live_session() -> str | None:
    r = subprocess.run(_tmux("has-session", "-t", TMUX_EXACT), capture_output=True)
    return TMUX_EXACT if r.returncode == 0 else None


def _orch_pane() -> str | None:
    """Resolve the orch session's active pane id. `send-keys -t =orch` fails with
    'can't find pane' (the = exact-match prefix doesn't resolve to a pane), so we
    target the unambiguous pane id (e.g. %3) instead."""
    r = subprocess.run(
        _tmux("list-panes", "-t", TMUX_EXACT, "-F", "#{pane_active} #{pane_id}"),
        capture_output=True, text=True)
    panes = [ln.split() for ln in r.stdout.splitlines() if ln.strip()]
    for active, pid in panes:
        if active == "1":
            return pid
    return panes[0][1] if panes else None


def inject(text: str) -> bool:
    """Type the message into the live orch pane and submit it. Best-effort nudge
    only — durability is guaranteed by the operator_messages log + cc-orch's
    reconciliation, NOT by this keystroke landing (Option B discipline)."""
    pane = _orch_pane()
    if not pane:
        return False
    line = INJECT_PREFIX + " ".join(text.splitlines())
    # robust clear before typing
    for _ in range(3):
        subprocess.run(_tmux("send-keys", "-t", pane, "C-a"), check=False)
        subprocess.run(_tmux("send-keys", "-t", pane, "C-k"), check=False)
        subprocess.run(_tmux("send-keys", "-t", pane, "C-u"), check=False)
    time.sleep(0.15)
    subprocess.run(_tmux("send-keys", "-t", pane, "-l", line), check=False)
    time.sleep(0.3)
    subprocess.run(_tmux("send-keys", "-t", pane, "Enter"), check=False)
    return True


def _fetch_telegram_file(fp: str, dest) -> str:
    """Download api.telegram.org/file/<fp> -> dest with retry + backoff.
    Telegram's file CDN intermittently drops the connection mid-download (Errno 54
    'Connection reset by peer'); a bare urlretrieve has no retry, which silently
    lost files twice (2026-06-28, CAI-RESP). Stream in chunks, retry transient
    failures, verify the file is non-empty."""
    url = f"https://api.telegram.org/file/bot{TOKEN}/{fp}"
    last = None
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, length=65536)
            if os.path.getsize(dest) > 0:
                return str(dest)
            last = "empty download"
        except Exception as e:  # ConnectionResetError / URLError / socket timeout / …
            last = e
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass
        if i < 3:
            time.sleep(min(2 ** i, 8))  # 1s, 2s, 4s
    raise RuntimeError(f"telegram file download failed after 4 attempts: {last}")


def _download_photo(photos: list) -> str:
    """Download the largest size of a Telegram photo to logs/tg_media and return
    the local path. Token stays in the download URL only — never logged."""
    file_id = photos[-1]["file_id"]
    with urllib.request.urlopen(f"{API}/getFile?file_id={file_id}", timeout=30) as r:
        fp = json.load(r)["result"]["file_path"]
    media = ORCH / "logs" / "tg_media"
    media.mkdir(parents=True, exist_ok=True)
    dest = media / fp.replace("/", "_")
    return _fetch_telegram_file(fp, dest)


def _download_document(doc: dict) -> str:
    """Download a Telegram document (PDF/docx/csv/xlsx/etc.) to logs/tg_media and
    return the local path. Preserves the original file name (sanitised). Token
    stays in the download URL only — never logged. (getFile supports ~20MB.)"""
    file_id = doc["file_id"]
    with urllib.request.urlopen(f"{API}/getFile?file_id={file_id}", timeout=30) as r:
        fp = json.load(r)["result"]["file_path"]
    media = ORCH / "logs" / "tg_media"
    media.mkdir(parents=True, exist_ok=True)
    name = doc.get("file_name") or fp.rsplit("/", 1)[-1]
    safe = "".join(ch if (ch.isalnum() or ch in "._- ") else "_" for ch in name).strip() or "file"
    dest = media / safe
    return _fetch_telegram_file(fp, dest)


def handle(m: dict, chat: str) -> None:
    text = m.get("text") or m.get("caption") or ""
    content = text

    # screenshot / photo -> download + hand the path along
    if m.get("photo"):
        try:
            path = _download_photo(m["photo"])
            content = f"sent a SCREENSHOT → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            content = f"sent a photo (download failed: {e})" + (f" | {text}" if text else "")

    # document (PDF/docx/csv/xlsx/etc.) -> download + hand the path along
    elif m.get("document"):
        try:
            path = _download_document(m["document"])
            content = f"sent a FILE → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            content = f"sent a file (download failed: {e})" + (f" | {text}" if text else "")

    # reply-threading -> prepend the quoted message for context
    rep = m.get("reply_to_message")
    if rep:
        snip = (rep.get("text") or rep.get("caption") or "[media]").strip().replace("\n", " ")[:70]
        content = f'↩️ re "{snip}": {content}'

    # who sent it (vendor group has multiple members) — context only, never a token.
    frm = m.get("from") or {}
    who = (frm.get("username") and "@" + frm["username"]) or \
        " ".join(p for p in (frm.get("first_name"), frm.get("last_name")) if p) or \
        str(frm.get("id", "?"))
    if who:
        content = f"[{who}] {content}"

    if not content:
        return

    # DURABLE LOG FIRST — guaranteed even if injection fails (Option B).
    operator_log.log("inbound", content, chat_id=chat, tag=CHANNEL_TAG)

    # Best-effort nudge into the hub session if it's live.
    if live_session():
        inject_text = content
        if len(content) > _INJECT_CAP:
            inject_text = (content[:_INJECT_CAP].rstrip() +
                           f" …[+{len(content) - _INJECT_CAP} chars truncated — "
                           f"full text: operator_log.recent()]")
        inject(inject_text)
    # Headless: already logged; cc-orch reconciles on its next breath. This
    # perimeter bridge generates NO fleet-wide reply of its own.


def main() -> None:
    # GRACEFUL no-token exit: must NOT crash-loop under launchd before the
    # operator wires IRSYAD_SUPPORT_BOT_TOKEN via BotFather.
    if not TOKEN:
        print("[irsyad_support_bridge] IRSYAD_SUPPORT_BOT_TOKEN missing/empty in "
              ".env — bot not provisioned yet. Logging and exiting gracefully "
              "(will start once the token is set). Not crash-looping.", flush=True)
        # Sleep briefly so KeepAlive doesn't hot-spin if the plist is ever loaded
        # before the token exists, then exit 0 (graceful, not a crash).
        time.sleep(30)
        return

    if not GROUP_CHAT_ID:
        print("[irsyad_support_bridge] IRSYAD_SUPPORT_GROUP_CHAT_ID not configured "
              "yet — accepting NO chats (deny-by-default perimeter scope). The "
              "bridge will poll but ignore every update until the group chat_id "
              "is set.", flush=True)

    offset = 0
    if OFFSET_FILE.exists():
        try:
            offset = int(OFFSET_FILE.read_text().strip())
        except ValueError:
            offset = 0
    while True:
        try:
            for u in get_updates(offset):
                offset = u["update_id"] + 1
                OFFSET_FILE.write_text(str(offset))
                m = u.get("message") or u.get("edited_message") or {}
                chat = str(m.get("chat", {}).get("id", ""))
                # SCOPE-GATE (deny-by-default): act ONLY on the one Gazzabyte
                # group. Empty config => no chat ever matches => ignore all.
                if not GROUP_CHAT_ID:
                    print("[irsyad_support_bridge] update ignored: group chat_id "
                          "not configured yet.", flush=True)
                    continue
                if chat != GROUP_CHAT_ID:
                    print(f"[irsyad_support_bridge] update from chat_id {chat} "
                          f"ignored (not the scoped Gazzabyte group).", flush=True)
                    continue
                handle(m, chat)
        except Exception as e:
            print(f"[irsyad_support_bridge] loop error: {type(e).__name__}: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
