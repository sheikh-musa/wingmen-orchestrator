#!/usr/bin/env python3
"""tg_bridge.py — inbound half of the operator<->orchestrator Telegram bridge.

Long-polls the Wingmen Orchestrator bot (@wingmennorchbot) for operator messages
and routes each one:
  1. LOG every inbound message to operator_messages (durable — nothing lost,
     even if the rest fails).
  2. If a live cc-orchestrator tmux session exists -> INJECT via send-keys, so
     the operator's words reach the live "tmux you" (true continuity).
  3. Else -> graceful HEADLESS fallback: ack on Telegram that the desk session
     is offline, and answer a deterministic `status` query straight from the
     substrate (no LLM needed).

Security: only the operator's chat_id is honoured (others ignored). The bot
token is read from .env and never echoed.
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.request

from nervous_system import operator_log

ORCH = pathlib.Path(__file__).resolve().parent.parent


def _env(key: str) -> str | None:
    for ln in open(ORCH / ".env"):
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    return None


TOKEN = _env("WINGMEN_BOT_TOKEN")
OPERATOR = str(_env("MUSA_TELEGRAM_ID"))
TMUX_TARGET = os.environ.get("TG_BRIDGE_TMUX_TARGET", "orch")
# tmux does prefix/glob matching on -t, so "orch" would falsely match an idle
# "orchestrator" session. The "=" prefix forces an EXACT session-name match.
TMUX_EXACT = "=" + TMUX_TARGET
OFFSET_FILE = ORCH / "logs" / ".tg_bridge_offset"
API = f"https://api.telegram.org/bot{TOKEN}"

# @alias -> context label. The bridge resolves the tag; cc-orchestrator
# interprets the intent. (Kept in sync with the CLAUDE.md bridge protocol.)
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
    url = f"{API}/getUpdates?timeout=30" + (f"&offset={offset}" if offset else "")
    with urllib.request.urlopen(url, timeout=45) as r:
        return json.load(r).get("result", [])


def tg_send(text: str) -> None:
    subprocess.run([str(ORCH / "scripts" / "tg_send.sh"), text], check=False)


def live_session() -> str | None:
    r = subprocess.run(_tmux("has-session", "-t", TMUX_EXACT), capture_output=True)
    return TMUX_EXACT if r.returncode == 0 else None


def parse_tag(text: str) -> str | None:
    m = re.match(r"^\s*@(\w+)\b", text)
    if m and m.group(1).lower() in ALIASES:
        return ALIASES[m.group(1).lower()]
    return None


def _orch_pane() -> str | None:
    """Resolve session orch's active pane id. `send-keys -t =orch` fails with
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
    for _ in range(2):
        subprocess.run(_tmux("send-keys", "-t", pane, "C-a"), check=False)
        subprocess.run(_tmux("send-keys", "-t", pane, "C-k"), check=False)
        subprocess.run(_tmux("send-keys", "-t", pane, "C-u"), check=False)


def inject(text: str, tag: str | None) -> bool:
    """Type the operator's message into the live orch pane and submit it,
    verifying each step against the actual input box.

    CRITICAL (2026-06-19): a bare send-keys can be SILENTLY DROPPED when it
    collides with the pane's own streaming output (a tool mid-run). The first
    fix verified the text landed but (a) built the needle by stripping emoji, so
    it never matched the rendered pane for reply-quotes (↩️/🚨) and (b) retried
    with a lone C-u that didn't clear wrapped text — typing the title 3× and
    never submitting. This version normalizes both sides, inspects the input box
    specifically, clears robustly before each attempt, and confirms the text
    actually LEFT the box (i.e. submitted) before returning True."""
    pane = _orch_pane()
    if not pane:
        return False
    prefix = f"📱 Operator (Telegram{', @' + tag if tag else ''}): "
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


def _download_photo(photos: list) -> str:
    """Download the largest size of a Telegram photo to logs/tg_media and return
    the local path (so cc-orchestrator can Read the image). Token stays in the
    download URL only — never logged."""
    file_id = photos[-1]["file_id"]
    with urllib.request.urlopen(f"{API}/getFile?file_id={file_id}", timeout=30) as r:
        fp = json.load(r)["result"]["file_path"]
    media = ORCH / "logs" / "tg_media"
    media.mkdir(parents=True, exist_ok=True)
    dest = media / fp.replace("/", "_")
    urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{TOKEN}/{fp}", dest)
    return str(dest)


def handle(m: dict, chat: str) -> None:
    text = m.get("text") or m.get("caption") or ""
    tag = parse_tag(text)
    content = text

    # screenshot / photo -> download + hand cc-orchestrator the path to Read
    if m.get("photo"):
        try:
            path = _download_photo(m["photo"])
            content = f"sent a SCREENSHOT → {path}" + (f"  | caption: {text}" if text else "")
        except Exception as e:
            content = f"sent a photo (download failed: {e})" + (f" | {text}" if text else "")

    # reply-threading -> prepend the quoted message so I know what it refers to
    rep = m.get("reply_to_message")
    if rep:
        snip = (rep.get("text") or rep.get("caption") or "[media]").strip().replace("\n", " ")[:70]
        content = f'↩️ re "{snip}": {content}'

    if not content:
        return
    operator_log.log("inbound", content, chat_id=chat, tag=tag)
    if live_session():
        if inject(content, tag):
            return
        # Desk IS live but the injection couldn't land (busy pane). Don't claim
        # offline — tell the operator it's saved + queued so nothing feels lost.
        tg_send("📥 Got it — saved. The desk was mid-task so it didn't surface "
                "instantly; I'll pick it up on my next breath. (Nothing lost.)")
        return
    if text.strip().lower().lstrip("/") == "status":
        tg_send(status_snapshot())
    else:
        tg_send("📥 Logged — but the desk (tmux) orchestrator isn't live right "
                "now, so I'll pick this up the moment it is. Send 'status' for an "
                "instant fleet snapshot in the meantime.")


def main() -> None:
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
                if chat == OPERATOR:
                    handle(m, chat)
        except Exception as e:
            print(f"[tg_bridge] loop error: {type(e).__name__}: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
