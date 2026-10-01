"""Shared secret-VALUE shape patterns (Musa op#24408), used by BOTH:

  - secrets_transcript_guard.py's Rule E (PreToolUse, blocks a Bash command that
    types a secret literally inline instead of referencing it by $VAR)
  - secrets_output_scanner.py (PostToolUse, catches/redacts a secret-shaped value
    that still made it into a tool_use input or a tool_result output)

One shared source so the two layers can't silently drift apart (bus #48685/#48695:
cc-fleet-health's real-leak-shapes sweep found literal DSNs/tokens typed directly into
Bash commands -- shape 1 ~84x, mostly `psql postgres://user:pass@host/db`; shape 2 ~55x,
mostly `curl -H "Authorization: Bearer <token>"` / an inline bot token -- that the old
guard never blocked pre-execution and the old scanner, which only ever looked at
tool_response/stdout, never caught in the tool_use INPUT either).
"""
from __future__ import annotations

import re

SECRET_VALUE_PATTERNS = {
    "anthropic-api-key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "supabase-service-key": re.compile(r"sbp_[a-f0-9]{20,}"),
    # no leading \b: a Telegram Bot API URL embeds the token right after "bot" with no
    # word boundary (.../bot123456789:AA.../sendMessage) -- anchored on the literal "AA"
    # prefix real bot tokens use instead (bus #48642 real incident).
    "telegram-bot-token": re.compile(r"\d{8,10}:AA[A-Za-z0-9_-]{30,}\b"),
    "postgres-dsn": re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "vercel-token": re.compile(r"\bvcp_[A-Za-z0-9]{20,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "google-oauth-refresh-token": re.compile(r"\b1//0[A-Za-z0-9_-]{20,}\b"),
    "ssh-private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # bus #48685/#48695 shape 2: a bearer/bot token typed literally into a curl header
    # or an export, rather than referenced via $VAR. Not anchored to "Authorization:" --
    # orch-console's ask covers "Bearer <token>" generally (curl -H, a raw header dump,
    # an export of the header value).
    "bearer-token": re.compile(r"\bBearer\s+[A-Za-z0-9\-_.=]{15,}\b", re.IGNORECASE),
}
