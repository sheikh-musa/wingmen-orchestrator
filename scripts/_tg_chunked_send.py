#!/usr/bin/env python3
"""_tg_chunked_send.py — send a message to Telegram, splitting on the 4096-char
limit so long replies/pings are never truncated (the ihsanosbot bug).

Reads TG_TOK / TG_CHAT / TG_TEXT from the environment (not argv — keeps the
token out of `ps`). Chunks on line boundaries where possible, hard-splitting any
single oversized line. Exits 0 only if every chunk sent.
"""
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
