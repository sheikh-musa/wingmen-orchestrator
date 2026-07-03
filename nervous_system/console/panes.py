"""Live tmux pane peek (read-only) — operator-requested "the lane cards look
stale" fix (agent_messages thread f869956c, msg 6156).

The DB-derived lane view (agent_status.current_task, last_heartbeat) is a
dumb 5-min timer + an almost-always-empty current_task — it shows a lane is
"up", never what it's actually doing. This module lets the console show the
lane's REAL tmux pane instead.

READ-ONLY BY CONSTRUCTION: the only tmux subcommands ever invoked are
`list-sessions` (to know what's real) and `capture-pane` (to read it) — never
send-keys or anything else. A session name is only ever passed to
`capture-pane` after it's been confirmed to be in the CURRENT, real
`list-sessions` output; an unrecognized/attacker-supplied name never reaches
the subprocess call. subprocess.run is always given an argv LIST (never
shell=True, never string-interpolated), so even an adversarial session name
can't break out of its single argv slot.
"""
from __future__ import annotations

import subprocess
from typing import List, Optional

_TIMEOUT_S = 5
_CAPTURE_LINES = 40


def live_sessions() -> List[str]:
    """Real, currently-live tmux session names. Never trust client input for
    this — it's the sole source of truth for what capture_pane is allowed to
    read."""
    try:
        r = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if r.returncode != 0:
            return []
        return [s for s in r.stdout.splitlines() if s]
    except Exception:
        return []


def capture_pane(session: str) -> Optional[str]:
    """Return the last ~40 lines of *session*'s pane 0, or None if the
    session isn't live right now (checked against live_sessions(), not
    trusted from the caller) or the capture otherwise fails.

    `-S -40` asks tmux for the last 40 lines directly (not just whatever the
    current viewport happens to be); the result is also sliced client-side as
    a belt-and-suspenders cap. The `=session:0.0` target is tmux's EXACT-match
    session selector (never substring/prefix) — defense in depth on top of
    the live_sessions() membership check, since by the time this runs the
    name has already been confirmed to be a real, live session.
    """
    if not session or session not in live_sessions():
        return None
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", f"={session}:0.0", "-p", "-S", "-40"],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        if r.returncode != 0:
            return None
        lines = r.stdout.splitlines()
        return "\n".join(lines[-_CAPTURE_LINES:])
    except Exception:
        return None
