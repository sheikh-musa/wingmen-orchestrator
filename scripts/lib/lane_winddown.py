#!/usr/bin/env python3
"""lane_winddown — the missing half of lane elasticity: deciding when a lane may be ENDED.

`scripts/lanes.sh` could spin lanes UP and never down; nothing anywhere wound one down on
idleness. The doctrine sentence in CLAUDE.md said the manual pen was "interim until the
autoscaler subsumes it", and on 2026-08-15 the only file in the repo containing "autoscal"
turned out to be that sentence. A name doing an implementation's job.

WINDING DOWN IS NOT A RECYCLE, and the difference sets every gate below. A recycle clears a
body and boots it again from its own handoff; a wind-down ENDS the session, and anything that
lived only in that context is gone. There is also no wipe here whose result could be read to
tell a dim ghost from real staged text, so the composer question has to be answered by the
classifier alone. Every gate therefore FAILS CLOSED: an unknown answer refuses. A wrong "yes"
loses work; a wrong "no" leaves a lane up for another hour.

The predicate is separated from the shell entrypoint so it is testable without a live tmux
server or database — and so `lanes.sh down` and (later) the SRE's idle detector call the SAME
rules. A lane must never be windable by one path under rules the other would have refused.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

_REPO = Path(__file__).resolve().parent.parent.parent

# Not lanes. Ending one of these is not elasticity, it is an outage. Hard-coded here rather
# than left to each caller's memory — the caller who forgets is exactly the failure mode.
SINGLETONS = {"nazim", "cai", "orch", "orchestrator", "fleet-health", "fleet-console", "quality"}

# A handoff older than this is not a restore point for a session that is about to cease to
# exist. Deliberately tighter than self_recycle's 900s: a recycle can be re-run if the boot
# comes back thin; a wound-down lane cannot be asked what it meant.
MAX_HANDOFF_AGE_S = 900


def may_wind_down(
    session: str,
    session_exists: Callable[[str], bool],
    is_busy: Callable[[str], bool],
    unread_count: Callable[[str], Optional[int]],
    handoff_age_s: Callable[[str], Optional[int]],
    composer_state: Callable[[str], str],
) -> Tuple[bool, str]:
    """(may_wind_down, reason). Every external fact is injected — see the module docstring.

    composer_state returns one of: "empty" | "ghost" | "real" | "unknown".
    unread_count / handoff_age_s return None when they could not be measured, which is
    REFUSED rather than treated as zero — a measurement whose tooling failed reports "could
    not measure", never a finding.
    """
    if session in SINGLETONS:
        return False, f"'{session}' is a SINGLETON body, not a lane — ending it is an outage, not elasticity"

    if not session_exists(session):
        return False, f"no tmux session '{session}' — nothing to wind down (and a missing session is not a success)"

    if is_busy(session):
        return False, f"'{session}' is BUSY (mid-turn) — ending a working body discards the turn it is producing"

    n = unread_count(session)
    if n is None:
        return False, f"could not measure '{session}' unread bus rows — refusing (unknown is not zero)"
    if n > 0:
        return False, (f"'{session}' has {n} unread bus row(s) — winding it down strands that work AND hides it: "
                       f"the rows stay unread forever because their recipient stops existing")

    age = handoff_age_s(session)
    if age is None:
        return False, (f"'{session}' has NO handoff — its context is about to cease to exist and nothing would "
                       f"survive it. Ask it to write one first, or pass --handoff <path>. Looked in: "
                       + ", ".join(handoff_candidates(session)))
    if age > MAX_HANDOFF_AGE_S:
        return False, (f"'{session}' handoff is {age}s old (max {MAX_HANDOFF_AGE_S}s) — a stale restore point does "
                       f"not preserve the work, it launders the loss")

    state = composer_state(session)
    if state == "real":
        return False, (f"'{session}' composer holds REAL unsent text — that is a next step the lane typed for "
                       f"itself, and unlike a reset there is no wipe here whose result could prove otherwise")
    if state not in ("empty", "ghost"):
        return False, f"could not read '{session}' composer (state={state!r}) — refusing (cannot prove nothing is staged)"

    return True, f"'{session}' is idle, drained, has a {age}s-old handoff, and its composer is {state}"


# --------------------------------------------------------------------------------------
# Live probes — the real implementations of the injected callables.
# --------------------------------------------------------------------------------------

def _tmux() -> str:
    for c in (os.environ.get("TMUX_BIN"), "/usr/local/bin/tmux", "/opt/homebrew/bin/tmux"):
        if c and os.path.exists(c):
            return c
    return "tmux"


def live_session_exists(session: str) -> bool:
    return subprocess.run([_tmux(), "has-session", "-t", f"={session}"],
                          capture_output=True).returncode == 0


def live_is_busy(session: str) -> bool:
    """Delegates to scripts/lib/pane_busy.sh — the LIVE FOOTER, never the whole pane. Grepping
    the whole capture makes a stale busy marker in the scrollback read as busy forever, which
    is the bug that stranded cai's own recycle on 2026-08-16. Unreadable => busy (fail closed)."""
    script = f'. "{_REPO}/scripts/lib/pane_busy.sh"; TMUX_BIN="{_tmux()}" pane_busy "{session}"'
    try:
        return subprocess.run(["bash", "-c", script], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return True


def _agent_for_session(session: str) -> Optional[str]:
    """The bus id registered for this tmux session, or None if it cannot be resolved.
    None propagates as 'could not measure', which refuses."""
    try:
        import psycopg
        from dotenv import load_dotenv
        load_dotenv(_REPO / ".env")
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT agent_id FROM agent_status WHERE tmux_session=%s "
                        "ORDER BY updated_at DESC LIMIT 1", (session,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def live_unread_count(session: str) -> Optional[int]:
    agent = _agent_for_session(session)
    if not agent:
        return None
    try:
        import psycopg
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent=%s AND read_at IS NULL "
                        "AND coalesce(is_test,false)=false", (agent,))
            return int(cur.fetchone()[0])
    except Exception:
        return None


def handoff_candidates(session: str) -> list:
    """Every place a lane handoff is actually written today. There is no single convention —
    reports/<session>-handoff-NOW.md, reports/<agent-id>-handoff-NOW.md and
    reports/lane-handoffs/ are all in live use — and checking only one of them would make this
    gate refuse every lane. A gate that always refuses is a gate people route around, which is
    worse than a gate with three candidates."""
    agent = _agent_for_session(session)
    names = [session] + ([agent] if agent and agent != session else [])
    out = []
    for n in names:
        out.append(str(_REPO / "reports" / f"{n}-handoff-NOW.md"))
        out.append(str(_REPO / "reports" / "lane-handoffs" / f"{n}-handoff-NOW.md"))
    return out


def live_handoff_age_s(session: str, handoff: Optional[str] = None) -> Optional[int]:
    paths = [handoff] if handoff else handoff_candidates(session)
    for candidate in paths:
        try:
            return int(time.time() - Path(candidate).stat().st_mtime)
        except Exception:
            continue
    return None


def live_composer_state(session: str) -> str:
    """Delegates to the shared SGR-aware extractor — the same classifier the resets and
    lane_nudge use, so 'real' means the same thing everywhere."""
    script = (
        f'. "{_REPO}/scripts/lib/composer_capture.sh"; '
        f'composer_parse_pane "{_tmux()}" "={session}:0.0"; '
        f'echo "$CC_EMPTY|${{CC_GHOST:-0}}|$CC_PARTIAL"'
    )
    try:
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
        empty, ghost, partial = (out.stdout.strip().split("|") + ["", "", ""])[:3]
    except Exception:
        return "unknown"
    if partial not in ("ok", ""):
        return "unknown"
    if empty == "1":
        return "empty"
    if ghost == "1":
        return "ghost"
    return "real"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Decide (and optionally perform) a lane wind-down.")
    ap.add_argument("session")
    ap.add_argument("--handoff", default=None, help="explicit handoff path (default reports/<session>-handoff-NOW.md)")
    ap.add_argument("--kill", action="store_true", help="actually end the session when the gates pass")
    args = ap.parse_args()

    ok, why = may_wind_down(
        args.session,
        session_exists=live_session_exists,
        is_busy=live_is_busy,
        unread_count=live_unread_count,
        handoff_age_s=lambda s: live_handoff_age_s(s, args.handoff),
        composer_state=live_composer_state,
    )
    print(("WIND-DOWN OK: " if ok else "REFUSED: ") + why)
    if not ok:
        return 1
    if not args.kill:
        print("(dry — pass --kill to actually end the session)")
        return 0
    subprocess.run([_tmux(), "kill-session", "-t", f"={args.session}"], check=False)
    print(f"ended tmux session '{args.session}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
