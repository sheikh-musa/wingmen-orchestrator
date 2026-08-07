"""secret_redact.py — outbound secret-pattern scrubbing (extracted from
ralph_runner._redact and hardened).

A single, importable redactor so EVERY outbound surface (Telegram sends, bus
rows, logs) scrubs the same secret shapes. Extracted after a real Postgres DSN
leaked into a bus row: the redactor existed only inside ralph_runner and never
guarded the Telegram send paths.

Usage (import):
    from nervous_system.secret_redact import redact
    safe = redact(text)

Usage (CLI — for shell send scripts):
    printf '%s' "$TEXT" | python3 -m nervous_system.secret_redact
reads stdin, writes redacted text to stdout with no added trailing newline, so
clean (secret-free) input round-trips byte-identical.

Each pattern maps to a legible marker (e.g. `[REDACTED:pg-dsn]`) so a scrubbed
message stays readable while never leaking the secret itself. Patterns are
applied in order; the DSN pattern runs first so its embedded password can never
be partially matched by a later, narrower pattern.
"""
from __future__ import annotations

import re
import sys

# Ordered (compiled_pattern, replacement). Order matters: broad/structured
# secrets (DSN) are scrubbed before narrower token shapes so an inner substring
# of a DSN can't be re-matched after the DSN is already gone.
PATTERNS = [
    # Postgres connection string incl. embedded credentials:
    #   postgres://user:pass@host:5432/db  /  postgresql://...
    (re.compile(r'postgres(?:ql)?://[^\s:@]+:[^\s@]+@\S+'), '[REDACTED:pg-dsn]'),
    # Telegram bot token:  123456789:AA...(35+ url-safe chars)
    (re.compile(r'\d{6,}:[A-Za-z0-9_-]{30,}'), '[REDACTED:tg-token]'),
    # Anthropic API key
    (re.compile(r'sk-ant-[a-zA-Z0-9_-]+'), '[REDACTED:anthropic-key]'),
    # JWT (three base64url segments; Supabase service/anon keys take this shape)
    (re.compile(r'eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+'), '[REDACTED:jwt]'),
    # GitHub personal / oauth token
    (re.compile(r'gh[po]_[a-zA-Z0-9]+'), '[REDACTED:gh-token]'),
    # Vercel token
    (re.compile(r'vcp_[a-zA-Z0-9]+'), '[REDACTED:vercel-token]'),
    # Supabase publishable key
    (re.compile(r'sb_publishable_[a-zA-Z0-9_-]+'), '[REDACTED:supabase-key]'),
]


def redact(text: str) -> str:
    """Replace every known secret shape in *text* with its marker. Secret-free
    text is returned unchanged (byte-identical)."""
    if not text:
        return text
    for pattern, marker in PATTERNS:
        text = pattern.sub(marker, text)
    return text


def _main() -> int:
    data = sys.stdin.read()
    sys.stdout.write(redact(data))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
