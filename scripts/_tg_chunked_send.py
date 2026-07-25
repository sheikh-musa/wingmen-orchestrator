#!/usr/bin/env python3
"""_tg_chunked_send.py — send a message to Telegram, splitting on the 4096-char
limit so long replies/pings are never truncated (the ihsanosbot bug).

Reads TG_TOK / TG_CHAT / TG_TEXT from the environment (not argv — keeps the
token out of `ps`). Chunks on line boundaries where possible, hard-splitting any
single oversized line. Exits 0 only if every chunk sent.
"""
import difflib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import socket as _socket_ipv4patch
_ORIG_GAI = _socket_ipv4patch.getaddrinfo
def _gai_ipv4_tg(host, *a, **k):
    res = _ORIG_GAI(host, *a, **k)
    if isinstance(host, str) and "telegram.org" in host:
        v4 = [r for r in res if r[0] == _socket_ipv4patch.AF_INET]
        return v4 or res
    return res
_socket_ipv4patch.getaddrinfo = _gai_ipv4_tg


LIMIT = 4000  # under Telegram's 4096 hard cap, leaving margin

# ── Cross-body duplicate guard (op#7101, 2026-07-25) ─────────────────────────────────
# The operator got the SAME captcha warning twice within minutes — once from cai on its own
# bot, once from the freshly-reset hub relaying cai's ruling on the hub bot. Neither body
# broke a rule: each owns its own operator surface. The gap is that nobody checks whether
# the other already said it.
#
# Every operator-facing sender in the fleet funnels through THIS file (tg_send, cai_send,
# nazim_send, irsyad_support_send, lane_reply's direct phase), so this is the one chokepoint
# where "somebody else already told him this" can be enforced rather than promised.
#
# Suppresses only when ALL of these hold:
#   • same chat_id, within DUP_WINDOW_SEC
#   • text is near-identical (normalised similarity >= DUP_RATIO)
#   • the message is substantial (>= DUP_MIN_CHARS) — short acks like "done" or "got it"
#     legitimately repeat and are never suppressed
# Escape hatch: TG_ALLOW_DUPLICATE=1 sends regardless (a deliberate resend).
# Deliberately sender-agnostic: a body echoing ANOTHER body is the bug we hit, and a body
# double-sending ITSELF is also a bug. Neither needs a new column to detect.
DUP_WINDOW_SEC = int(os.environ.get("TG_DUP_WINDOW_SEC", "900"))
DUP_RATIO = float(os.environ.get("TG_DUP_RATIO", "0.85"))
DUP_COMPARE_CHARS = 600
DUP_MIN_CHARS = 80


def _norm(s: str) -> str:
    return " ".join((s or "").split()).lower()[:DUP_COMPARE_CHARS]


def duplicate_of(chat: str, text: str):
    """Return (id, sender) of a near-identical recent message to this chat, else None.

    Fail-open by design: if the log is unreachable we send. A missed suppression is one
    duplicate message; a false block is a lost operator message, which is far worse.
    """
    if os.environ.get("TG_ALLOW_DUPLICATE") == "1" or len(_norm(text)) < DUP_MIN_CHARS:
        return None
    try:
        import psycopg
    except Exception:
        return None
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        orch = os.path.expanduser("~/wingmen/orchestrator/.env")
        try:
            for line in open(orch):
                if line.startswith("DATABASE_URL="):
                    dsn = line.split("=", 1)[1].strip()
                    break
        except OSError:
            return None
    if not dsn:
        return None
    want = _norm(text)
    try:
        with psycopg.connect(dsn, connect_timeout=8) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, coalesce(from_name, 'unknown'), text, coalesce(tag,'-') "
                "FROM operator_messages WHERE direction='outbound' AND chat_id=%s "
                "AND delivered AND created_at > now() - make_interval(secs => %s) "
                "ORDER BY id DESC LIMIT 25", (str(chat), DUP_WINDOW_SEC))
            for rid, sender, prev, tag in cur.fetchall():
                if difflib.SequenceMatcher(None, want, _norm(prev)).ratio() >= DUP_RATIO:
                    return rid, f"{sender}/{tag}"
    except Exception:
        return None
    return None


def chunks(text: str):
    out, cur = [], ""
    for line in text.split("\n"):
        # a single line longer than the limit must be hard-split
        while len(line) > LIMIT:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:LIMIT])
            line = line[LIMIT:]
        if cur and len(cur) + 1 + len(line) > LIMIT:
            out.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def main() -> int:
    tok = os.environ.get("TG_TOK", "")
    chat = os.environ.get("TG_CHAT", "")
    text = os.environ.get("TG_TEXT", "")
    if not (tok and chat and text):
        print("missing TG_TOK/TG_CHAT/TG_TEXT", file=sys.stderr)
        return 1
    dup = duplicate_of(chat, text)
    if dup:
        rid, sender = dup
        # Exit 0: from the caller's point of view the operator HAS this message — it was
        # delivered minutes ago by {sender}. Failing here would make callers retry the
        # very duplicate we are preventing.
        print(f"SUPPRESSED duplicate — the operator already got this from {sender} "
              f"(operator_messages #{rid}) within {DUP_WINDOW_SEC}s. "
              f"Set TG_ALLOW_DUPLICATE=1 to send anyway.", file=sys.stderr)
        return 0
    parts = chunks(text)
    total = len(parts)
    for i, part in enumerate(parts):
        # suffix a page marker only when actually split, so single messages stay clean
        body = part if total == 1 else f"{part}\n\n({i + 1}/{total})"
        data = urllib.parse.urlencode({"chat_id": chat, "text": body}).encode()
        try:
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=30
            ) as r:
                d = json.load(r)
                if not d.get("ok"):
                    print(f"tg send error: {d.get('description')}", file=sys.stderr)
                    return 1
        except Exception as e:
            print(f"tg send error: {e}", file=sys.stderr)
            return 1
        if total > 1:
            time.sleep(0.4)  # stay under Telegram's per-chat rate limit
    return 0


if __name__ == "__main__":
    sys.exit(main())
