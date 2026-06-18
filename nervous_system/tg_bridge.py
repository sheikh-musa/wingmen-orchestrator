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


def get_updates(offset: int):
    url = f"{API}/getUpdates?timeout=30" + (f"&offset={offset}" if offset else "")
    with urllib.request.urlopen(url, timeout=45) as r:
        return json.load(r).get("result", [])


def tg_send(text: str) -> None:
    subprocess.run([str(ORCH / "scripts" / "tg_send.sh"), text], check=False)


def live_session() -> str | None:
    r = subprocess.run(["tmux", "has-session", "-t", TMUX_EXACT], capture_output=True)
    return TMUX_EXACT if r.returncode == 0 else None


def parse_tag(text: str) -> str | None:
    m = re.match(r"^\s*@(\w+)\b", text)
    if m and m.group(1).lower() in ALIASES:
        return ALIASES[m.group(1).lower()]
    return None


def inject(text: str, tag: str | None) -> None:
    prefix = f"📱 Operator (Telegram{', @' + tag if tag else ''}): "
    line = prefix + " ".join(text.splitlines())
    subprocess.run(["tmux", "send-keys", "-t", TMUX_EXACT, "-l", line], check=False)
    subprocess.run(["tmux", "send-keys", "-t", TMUX_EXACT, "Enter"], check=False)


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


def handle(text: str, chat: str, tag: str | None) -> None:
    operator_log.log("inbound", text, chat_id=chat, tag=tag)
    if live_session():
        inject(text, tag)
    elif text.strip().lower().lstrip("/") == "status":
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
                text = m.get("text", "")
                if chat == OPERATOR and text:
                    handle(text, chat, parse_tag(text))
        except Exception:
            time.sleep(5)


if __name__ == "__main__":
    main()
