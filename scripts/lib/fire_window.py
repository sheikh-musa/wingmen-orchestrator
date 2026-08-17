#!/usr/bin/env python3
"""fire_window — a short, self-expiring hold that quiesces keystrokes into ONE tmux pane.

WHY. A recycle wipes a body's composer, types `/clear`, submits it, then types the boot
instruction. Anything that `tmux send-keys` into that pane during those few seconds jams
the sequence, and the body returns half-initialised — holding neither its old context nor
a clean one. `reset_nazim.sh` guarded that window by `launchctl bootout`-ing ONE daemon
(`dev.wingmen.nazim-bus-notify`), but at least eight things on this host can type into a
body's pane: the operator-ingest nudger, the fleet wake subscriber, the wedge/SLA/context
watchdogs, backlog_swipe, lane_nudge.sh, the shadow watcher. A pause LIST names the ones
someone remembered; the next sender written is not on it.

So the quiesce is a lock the SENDERS consult, not a list the resetter maintains. A sender
added tomorrow is blocked by default, and `tests/test_fire_window.py` fails if it isn't.

⚠ WORKTREE TEST-FIDELITY CAVEAT (cc-fleet-health, 2026-08-17). Several senders invoke this
via "$ORCH_DIR/.venv/bin/python3 fire_window.py check ..." where $ORCH_DIR is the SCRIPT'S
OWN dir (e.g. lane_nudge.sh:45). A git WORKTREE has no .venv, so that python is absent, the
check command fails, and the caller's guard FAILS OPEN — a held window is NOT caught. In a
full checkout (.venv present) the same guard HOLDS. Net: a worktree-based test of any
fire_window guard exercises a MORE PERMISSIVE path than production (the dangerous direction).
Do NOT trust a green worktree run for fire-window behaviour — verify on the live tree
(#23421 gate-test != shipped-path). This bit the step-4 f/u locked-label test.

FAIL-OPEN, DELIBERATELY. A missing, expired, or unreadable lock reads as NOT held, and the
TTL is clamped hard. A skipped nudge is cheap — the payload is always a durable row that
survives being missed (Option B: the log is the delivery guarantee, the keystroke is only
a signal). A body that can never be nudged again is silently unreachable, which is the
exact ghost-STAGED failure that left a lane sitting for five hours on 2026-08-15. Prefer a
collided keystroke to an unreachable body.

Usage (bash):
    scripts/lib/fire_window.py hold nazim --ttl 120 --reason "reset_nazim fire window"
    scripts/lib/fire_window.py check nazim   # exit 0 = HELD (do not type), 1 = free
    scripts/lib/fire_window.py release nazim

Usage (python):
    from scripts.lib import fire_window
    if fire_window.is_held(session):
        return  # a recycle owns this pane right now; skip the nudge
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parent.parent.parent

# Overridable by env so a test (or a caller on another checkout) never touches the real
# locks. Read at import time; tests monkeypatch the module attribute directly.
STATE_DIR = Path(os.environ.get("FIRE_WINDOW_DIR") or (_REPO / "state" / "fire_window"))

# A fire window is seconds long. Anything approaching ten minutes is a bug or a crashed
# resetter, and the cost of the clamp being wrong is one collided keystroke.
MAX_TTL_SECONDS = 600


def _path(session: str) -> Path:
    return STATE_DIR / f"{session}.json"


def hold(session: str, ttl_seconds: int, reason: str, holder: Optional[str] = None) -> Path:
    """Claim the pane for `ttl_seconds`. Overwrites any existing hold on that session."""
    ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "session": session,
        "reason": reason,
        "holder": holder or f"pid:{os.getpid()}",
        "created_at": time.time(),
        "expires_at": time.time() + ttl,
    }
    path = _path(session)
    path.write_text(json.dumps(payload, indent=2))
    return path


def release(session: str) -> bool:
    """Drop the hold. Idempotent — releasing a session never held is not an error."""
    try:
        _path(session).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _read(session: str) -> Optional[dict]:
    try:
        return json.loads(_path(session).read_text())
    except Exception:
        # Missing, corrupt, unreadable — all mean "no usable hold", and fail-open is the
        # safe direction here (see module docstring).
        return None


def is_held(session: str) -> bool:
    payload = _read(session)
    if not payload:
        return False
    try:
        return float(payload["expires_at"]) > time.time()
    except Exception:
        return False


def held_reason(session: str) -> Optional[str]:
    payload = _read(session)
    if not payload or not is_held(session):
        return None
    return payload.get("reason")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_hold = sub.add_parser("hold", help="claim a pane for the fire window")
    p_hold.add_argument("session")
    p_hold.add_argument("--ttl", type=int, default=120)
    p_hold.add_argument("--reason", default="fire window")

    p_rel = sub.add_parser("release", help="drop the hold")
    p_rel.add_argument("session")

    p_chk = sub.add_parser("check", help="exit 0 if HELD (do not type), 1 if free")
    p_chk.add_argument("session")

    args = ap.parse_args()
    if args.cmd == "hold":
        print(hold(args.session, args.ttl, args.reason))
        return 0
    if args.cmd == "release":
        release(args.session)
        return 0
    return 0 if is_held(args.session) else 1


if __name__ == "__main__":
    sys.exit(main())
