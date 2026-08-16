#!/usr/bin/env python3
"""pane_busy — is this pane mid-turn RIGHT NOW? Read the live footer, never the scrollback.

WHY THIS EXISTS. A `capture-pane -p` returns the whole visible pane, and every caller that
asked `"esc to interrupt" in capture` was really asking "has this body been busy at any point
still in the buffer". A lane that finished a long turn keeps that footer in its scrollback,
so it reads BUSY forever.

Found first in self_recycle's wait-for-idle loop, where it stranded cai's own recycle at 838k
(2026-08-16). Then in four more places, and the worst of them is the WAKE path itself:
agent_wake._pane_busy debounced the wake of any lane whose scrollback held an old busy footer.
That is a silent delivery failure on the mechanism the whole fleet uses to reach a lane — and
it presents as "the lane is busy", which is exactly why nobody chased it.

The live footer is the only part of a pane that describes NOW; everything above it describes
the past.

THE UNREADABLE CASE IS THE CALLER'S TO DECIDE, so it is a required-by-convention argument
rather than a default that hides the choice:
  * about to CLEAR a body  -> on_unreadable=True  (a pane we cannot see is not one we have
    proven safe to touch)
  * about to DELIVER to one -> on_unreadable=False (a dropped nudge is recoverable and the
    payload is durable; an undelivered one is invisible)

The bash twin is scripts/lib/pane_busy.sh; both are pinned by tests/test_pane_busy.py.
"""
from __future__ import annotations

import os
import subprocess

_BUSY_HINT = "esc to interrupt"
_IDLE_HINT = "for agents"
_FOOTER_LINES = 3


def is_busy_text(pane_text: str, on_unreadable: bool = True) -> bool:
    """True when the LIVE footer shows a turn in progress."""
    lines = [ln for ln in (pane_text or "").splitlines() if ln.strip()]
    if not lines:
        return on_unreadable
    footer = "\n".join(lines[-_FOOTER_LINES:]).lower()
    return _BUSY_HINT in footer and _IDLE_HINT not in footer


def _tmux() -> str:
    for c in (os.environ.get("TMUX_BIN"), "/usr/local/bin/tmux", "/opt/homebrew/bin/tmux"):
        if c and os.path.exists(c):
            return c
    return "tmux"


def is_busy(session: str, on_unreadable: bool = True, tmux: str = None) -> bool:
    try:
        r = subprocess.run([tmux or _tmux(), "capture-pane", "-t", f"={session}:0.0", "-p"],
                           capture_output=True, text=True, timeout=10)
        return is_busy_text(r.stdout or "", on_unreadable=on_unreadable)
    except Exception:
        return on_unreadable
