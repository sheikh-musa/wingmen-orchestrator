"""PII redaction — defense-in-depth (CAI-RESP-264 condition 4).

Invariant: agents never put donor/client PII (NRIC, full PII, card/account
numbers) into agent_messages bodies — the bus is coordination, not data. This
module enforces that invariant *in-view* as a second line of defense, so even a
misbehaving agent cannot surface PII through the console.

Stdlib-only (re). No external deps.
"""
from __future__ import annotations

import re
from typing import Optional

_REDACTED = "[redacted]"

# Singapore NRIC/FIN: letter + 7 digits + letter.
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)
# Email addresses.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Long digit runs (>=12) — card/account-shaped numbers (allow spaces/dashes).
_LONG_DIGITS = re.compile(r"\b(?:\d[ -]?){12,}\b")

# Fields on a message row that may carry free-text and therefore PII.
_TEXT_FIELDS = ("body", "subject")


def redact(text: Optional[str]) -> Optional[str]:
    """Return *text* with NRIC / email / long-digit-run PII replaced.

    None passes through as None; empty string passes through unchanged.
    """
    if not text:
        return text
    out = _NRIC.sub(_REDACTED, text)
    out = _EMAIL.sub(_REDACTED, out)
    out = _LONG_DIGITS.sub(_REDACTED, out)
    return out


def redact_message_row(row: dict) -> dict:
    """Return a shallow copy of *row* with free-text fields redacted."""
    out = dict(row)
    for field in _TEXT_FIELDS:
        if field in out:
            out[field] = redact(out[field])
    return out
