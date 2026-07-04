#!/usr/bin/env python3
"""cai_bridge.py — inbound half of the operator<->cai Telegram bridge.

A clean copy of tg_bridge.py adapted for the singleton "cai" governance node.
Long-polls the cai bot (@cai_orch_bot) for operator messages and routes each one:
  1. LOG every inbound message to operator_messages (durable — nothing lost,
     even if the rest fails). Tagged 'cai-channel' so the cai channel is
     distinguishable from the cc-orchestrator operator channel in the log.
  2. If a live `cai` tmux session exists -> INJECT via send-keys, so the
     operator's words reach the live cai session (true continuity).
  3. Else -> graceful HEADLESS fallback: ack on Telegram that the cai session
     is offline, and answer a deterministic `status` query straight from the
     substrate (no LLM needed).

Security: ONLY the operator's chat_id is honoured (everyone else ignored). No
partner/Desmond logic — cai is the governance brain, operator-only. The bot
token is read from .env and never echoed.

This is a SEPARATE bridge from tg_bridge.py: different bot token, different
offset file, different tmux target. It does NOT touch the live orch bridge.
"""
from __future__ import annotations
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from nervous_system import operator_log

ORCH = pathlib.Path(__file__).resolve().parent.parent

# Distinguishing tag stamped on every cai-channel message in operator_messages.
CHANNEL_TAG = "cai-channel"


def _env(key: str) -> str | None:
    for ln in open(ORCH / ".env"):
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    return None


TOKEN = _env("CAI_TELEGRAM_BOT_TOKEN")
OPERATOR = str(_env("MUSA_TELEGRAM_ID"))
TMUX_TARGET = os.environ.get("CAI_BRIDGE_TMUX_TARGET", "cai")
# tmux does prefix/glob matching on -t, so "cai" could falsely match another
# session whose name starts with "cai". The "=" prefix forces an EXACT match.
TMUX_EXACT = "=" + TMUX_TARGET
OFFSET_FILE = ORCH / "logs" / ".cai_bridge_offset"
# Max chars to TYPE into the cai TUI. Longer messages inject a preview + pointer
# (full text stays in operator_messages). Kept SMALL on purpose: at this size the
# paste never wraps past ~2 rows, so verify is reliable AND a failed attempt
# clears cleanly (multi-line wrapped input is what resists clearing — 2026-06-19).
_INJECT_CAP = 150
API = f"https://api.telegram.org/bot{TOKEN}"

# @alias -> context label. The bridge resolves the tag; cai interprets the
# intent. (Kept in sync with the CLAUDE.md bridge protocol.)
ALIASES = {
    "adcda": "cosem-adcda", "tdu": "cosem-tdu", "cosem": "cosem",
    "ihsanos": "ihsanos", "irsyad": "ihsanos",
    "scholar": "ai-scholar", "mizan": "ai-scholar",
    "qr": "branditqr", "fleet": "fleet/substrate", "console": "fleet/substrate",
    "cai": "cai",
}


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


def tg_send(text: str, chat: str | None = None) -> None:
    env = os.environ.copy()
    if chat:
        env["TG_CHAT_OVERRIDE"] = chat
    subprocess.run([str(ORCH / "scripts" / "cai_send.sh"), text], check=False, env=env)


def live_session() -> str | None:
    r = subprocess.run(_tmux("has-session", "-t", TMUX_EXACT), capture_output=True)
    return TMUX_EXACT if r.returncode == 0 else None


def parse_tag(text: str) -> str | None:
    m = re.match(r"^\s*@(\w+)\b", text)
    if m and m.group(1).lower() in ALIASES:
        return ALIASES[m.group(1).lower()]
    return None


def _cai_pane() -> str | None:
    """Resolve session cai's active pane id. `send-keys -t =cai` fails with
    'can't find pane' (the = exact-match prefix doesn't resolve to a pane), so
    we target the unambiguous pane id (e.g. %3) instead."""
    r = subprocess.run(
        _tmux("list-panes", "-t", TMUX_EXACT, "-F", "#{pane_active} #{pane_id}"),
        capture_output=True, text=True)
    panes = [ln.split() for ln in r.stdout.splitlines() if ln.strip()]
    for active, pid in panes:
        if active == "1":
            return pid
    return panes[0][1] if panes else None


def _capture(pane: str, n: int = 30) -> list[str]:
    r = subprocess.run(_tmux("capture-pane", "-p", "-t", pane),
                       capture_output=True, text=True)
    return r.stdout.splitlines()[-n:]


def _pane_busy(pane: str) -> bool:
    """True iff the cai session is mid-task (WORKING), using the same observable
    heuristic as scripts/lane_nudge.sh: the live footer (last 3 non-blank rows)
    shows 'esc to interrupt' AND NOT 'for agents'. Pure read-only capture-pane —
    never types. Callers MUST wrap in try/except (any failure => treat as not
    busy, so the normal inject path is never blocked)."""
    cap = "\n".join(l for l in _capture(pane, 30) if l.strip())[-2000:]
    tail = "\n".join(cap.splitlines()[-3:])
    return ("esc to interrupt" in tail) and ("for agents" not in tail)


def _norm(s: str) -> str:
    """ASCII-printable only, whitespace collapsed. Makes comparison robust to (a)
    emoji that render in the pane but get stripped from our needle, and (b) the
    input box wrapping a long line across rows."""
    return re.sub(r"\s+", " ", "".join(c for c in s if 32 <= ord(c) < 127)).strip()


def _is_rule(line: str) -> bool:
    s = line.strip()
    return len(s) >= 10 and set(s) <= set("─-—")


def _input_text(pane: str) -> str:
    """The normalized text currently sitting IN the input box — NOT the
    conversation scrollback. The input box is anchored by the `❯` prompt (we take
    the LAST one, which is the live input at the bottom of the pane) and spans
    from there down to the box's bottom horizontal rule. This lets us distinguish
    'typed but unsent' (text after ❯) from 'submitted and now shown above the
    prompt' (text that has left the box)."""
    lines = _capture(pane, 30)
    pi = None
    for i, l in enumerate(lines):
        if "❯" in l:
            pi = i  # keep the last (lowest) prompt
    if pi is None:
        return ""  # prompt not visible (e.g. mid-render) — treat as unknown
    seg = []
    for l in lines[pi:]:
        if _is_rule(l):
            break  # bottom border of the input box
        seg.append(l)
    seg = [l.replace("❯", " ", 1) for l in seg]
    return _norm(" ".join(seg))


def _clear_input(pane: str) -> None:
    """Belt-and-suspenders clear of a possibly multi-line / wrapped input box.
    C-a then C-k handles wrapped content that a lone C-u leaves behind."""
    for _ in range(3):
        subprocess.run(_tmux("send-keys", "-t", pane, "C-a"), check=False)
        subprocess.run(_tmux("send-keys", "-t", pane, "C-k"), check=False)
        subprocess.run(_tmux("send-keys", "-t", pane, "C-u"), check=False)
        subprocess.run(_tmux("send-keys", "-t", pane, "C-e"), check=False)
        subprocess.run(_tmux("send-keys", "-t", pane, "C-k"), check=False)


def inject(text: str, tag: str | None, prefix: str | None = None) -> bool:
    """Type the operator's message into the live cai pane and submit it,
    verifying each step against the actual input box.

    CRITICAL (2026-06-19): a bare send-keys can be SILENTLY DROPPED when it
    collides with the pane's own streaming output (a tool mid-run). This
    version normalizes both sides, inspects the input box specifically, clears
    robustly before each attempt, and confirms the text actually LEFT the box
    (i.e. submitted) before returning True."""
    pane = _cai_pane()
    if not pane:
        return False
    if prefix is None:
        prefix = f"📱 Operator (Telegram→cai{', @' + tag if tag else ''}): "
    line = prefix + " ".join(text.splitlines())
    needle = _norm(line)[:40]
    for _ in range(3):
        _clear_input(pane)
        time.sleep(0.15)
        subprocess.run(_tmux("send-keys", "-t", pane, "-l", line), check=False)
        time.sleep(0.3)
        if needle and needle not in _input_text(pane):
            continue  # didn't land — clear-first on next loop and retry
        subprocess.run(_tmux("send-keys", "-t", pane, "Enter"), check=False)
        time.sleep(0.4)
        # submitted iff the text has LEFT the input box (it now shows above it)
        if needle not in _input_text(pane):
            return True
        subprocess.run(_tmux("send-keys", "-t", pane, "Enter"), check=False)  # nudge
        time.sleep(0.4)
        if needle not in _input_text(pane):
            return True
    _clear_input(pane)  # leave no half-typed mess behind
    return False


def status_snapshot() -> str:
    """Deterministic fleet snapshot from the substrate — no LLM."""
    import psycopg
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    blk, rev, live = [], [], []
    try:
        with psycopg.connect(dsn, connect_timeout=10) as c, c.cursor() as cur:
            cur.execute(
                "SELECT workstream, stage FROM deploy_status "
                "WHERE stage IN ('live','blocked','in_review') "
                "ORDER BY (stage='blocked') DESC, updated_at DESC LIMIT 10")
            for w, s in cur.fetchall():
                (blk if s == "blocked" else rev if s == "in_review" else live).append(w)
    except Exception as e:
        return f"(couldn't read substrate: {e})"
    parts = []
    if blk:  parts.append("blocked: " + "; ".join(blk))
    if rev:  parts.append("awaiting you/review: " + "; ".join(rev))
    if live: parts.append("live: " + "; ".join(live))
    return "Fleet snapshot —\n" + ("\n".join(parts) if parts else "all quiet")


def _fetch_telegram_file(fp: str, dest) -> str:
    """Download api.telegram.org/file/<fp> -> dest with retry + backoff.
    Telegram's file CDN intermittently drops the connection mid-download (Errno 54
    'Connection reset by peer'); a bare urlretrieve has no retry, which silently
    lost the operator's signed DPA + ADCDA photos twice (2026-06-28, CAI-RESP).
    Stream in chunks, retry transient failures, verify the file is non-empty."""
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
    the local path (so cai can Read the image). Token stays in the download URL
    only — never logged."""
    file_id = photos[-1]["file_id"]
    with urllib.request.urlopen(f"{API}/getFile?file_id={file_id}", timeout=30) as r:
        fp = json.load(r)["result"]["file_path"]
    media = ORCH / "logs" / "tg_media"
    media.mkdir(parents=True, exist_ok=True)
    dest = media / fp.replace("/", "_")
    return _fetch_telegram_file(fp, dest)


def _download_document(doc: dict) -> str:
    """Download a Telegram document (PDF/docx/csv/xlsx/etc.) to logs/tg_media and
    return the local path. Preserves the operator's original file name (sanitised)
    so cai can recognise + Read it. Token stays in the download URL only — never
    logged. (getFile supports files up to ~20MB.)"""
    file_id = doc["file_id"]
    with urllib.request.urlopen(f"{API}/getFile?file_id={file_id}", timeout=30) as r:
        fp = json.load(r)["result"]["file_path"]
    media = ORCH / "logs" / "tg_media"
    media.mkdir(parents=True, exist_ok=True)
    # prefer the human file name; fall back to the telegram path basename
    name = doc.get("file_name") or fp.rsplit("/", 1)[-1]
    safe = "".join(ch if (ch.isalnum() or ch in "._- ") else "_" for ch in name).strip() or "file"
    dest = media / safe
    return _fetch_telegram_file(fp, dest)


def handle(m: dict, chat: str) -> None:
    text = m.get("text") or m.get("caption") or ""
    tag = parse_tag(text)
    content = text

    # screenshot / photo -> download + hand cai the path to Read
    if m.get("photo"):
        try:
            path = _download_photo(m["photo"])
            content = f"sent a SCREENSHOT → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            content = f"sent a photo (download failed: {e})" + (f" | {text}" if text else "")

    # document (PDF/docx/csv/xlsx/etc.) -> download + hand cai the path
    elif m.get("document"):
        try:
            path = _download_document(m["document"])
            content = f"sent a FILE → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            content = f"sent a file (download failed: {e})" + (f" | {text}" if text else "")

    # reply-threading -> prepend the quoted message so cai knows what it refers to
    rep = m.get("reply_to_message")
    if rep:
        snip = (rep.get("text") or rep.get("caption") or "[media]").strip().replace("\n", " ")[:70]
        content = f'↩️ re "{snip}": {content}'

    if not content:
        return
    operator_log.log("inbound", content, chat_id=chat, tag=CHANNEL_TAG)
    if live_session():
        # Cap what we TYPE into the TUI. A long paste wraps to many rows — verify
        # gets racy and a failed attempt can't be fully cleared, so the residue
        # bleeds into the next message. The full text is always in
        # operator_messages, so inject a short preview + a pointer and let cai
        # read the rest there.
        inject_text = content
        if len(content) > _INJECT_CAP:
            inject_text = (content[:_INJECT_CAP].rstrip() +
                           f" …[+{len(content) - _INJECT_CAP} chars truncated — "
                           f"full text: operator_log.recent()]")
        # INSTANT BUSY-ACK: if the session is mid-task it can't surface the reply
        # until it frees, so fire an immediate honest ack so the operator is never
        # left in silence (the real reply still follows via the unchanged inject
        # path below). Fully guarded: ANY failure falls through to normal flow.
        busy_acked = False
        try:
            _pane = _cai_pane()
            if _pane and _pane_busy(_pane):
                tg_send("got it 👍 — cai is mid-task, real reply coming shortly.", chat)
                busy_acked = True
        except Exception as e:
            print(f"[cai_bridge] busy-ack skipped: {type(e).__name__}: {e}", flush=True)
        if inject(inject_text, tag):
            return
        # cai IS live but the injection couldn't land (busy pane). Don't claim
        # offline — tell the operator it's saved + queued so nothing feels lost.
        # (Skip if we already fired the instant busy-ack — no need to double up.)
        if not busy_acked:
            tg_send("📥 Got it — saved. cai was mid-task so it didn't surface "
                    "instantly; I'll pick it up on my next breath. (Nothing lost.)", chat)
        return
    if text.strip().lower().lstrip("/") == "status":
        tg_send(status_snapshot(), chat)
    else:
        tg_send("📥 Logged — but the cai (tmux) session isn't live right now, so "
                "I'll pick this up the moment it is. Send 'status' for an instant "
                "fleet snapshot in the meantime.", chat)


def main() -> None:
    offset = 0
    if OFFSET_FILE.exists():
        try:
            offset = int(OFFSET_FILE.read_text().strip())
        except ValueError:
            offset = 0
    # Offset advances ONLY after a message is fully handled (durable log first),
    # so a timeout/outage never loses a message — Telegram redelivers from the
    # un-acked offset. The 2026-07-03 "frozen offset / kickstart didn't fix"
    # symptom was NOT this loop: the launchd plist pointed at a decommissioned
    # host path so the service could never actually run. Backoff below just keeps
    # a down network / a dual-poller 409 from tight-looping.
    while True:
        try:
            for u in get_updates(offset):
                m = u.get("message") or u.get("edited_message") or {}
                chat = str(m.get("chat", {}).get("id", ""))
                from_id = str((m.get("from") or {}).get("id", ""))
                # Operator-only: ignore everyone but Musa (no partner logic).
                if from_id and from_id == OPERATOR:
                    handle(m, chat)
                # advance only AFTER handle() logs durably — crash re-delivers.
                offset = u["update_id"] + 1
                OFFSET_FILE.write_text(str(offset))
        except urllib.error.HTTPError as e:
            if e.code == 409:
                # 409 Conflict = another getUpdates poller holds this bot token
                # (a second machine, or the unified ingest daemon). Two pollers
                # split/lose messages — back off hard and shout for the watchdog.
                print("[cai_bridge] FATAL 409 Conflict: another getUpdates poller "
                      "is active on the cai bot token — messages WILL be split/lost. "
                      "Ensure exactly one poller (this bridge XOR unified ingest).",
                      flush=True)
                time.sleep(30)
            else:
                print(f"[cai_bridge] loop error: HTTPError {e.code}: {e}", flush=True)
                time.sleep(5 + random.uniform(0, 3))
        except Exception as e:
            print(f"[cai_bridge] loop error: {type(e).__name__}: {e}", flush=True)
            time.sleep(5 + random.uniform(0, 3))


if __name__ == "__main__":
    main()
