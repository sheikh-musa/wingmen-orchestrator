#!/usr/bin/env python3
"""irsyad worker-reaper — tear down WOUND-DOWN elastic workers so the pool actually shrinks.

The bug this fixes (Musa op#20774 / Nazim #40833): an elastic worker winds down when the queue
is empty, but its tmux session LINGERS at an idle prompt and keeps heartbeating — so the
autoscaler still counts it toward MAX_LANES. The pool never shrinks → the autoscaler stops
proposing → new demand sits until someone manually prompts. That is exactly the "only built when
prompted" complaint. The elastic contract is: wind down → session GOES AWAY → pool shrinks →
autoscaler re-proposes on the next demand. This reaper is the missing "session goes away" step.

Reaps ONLY a spun elastic worker (tmux session 'irsyad-worker-<N>') that is BOTH:
  (a) idle — its tmux pane is NOT busy (composer_capture: no active turn / not 'Waiting for
      background agents'); a re-engaged worker is never reaped; AND
  (b) wound down — its LATEST bus post (from its own agent_id) is a WIND-DOWN, at least
      REAP_GRACE_MIN old (so a just-wound-down worker gets a grace window to be re-tasked).
Never touches standing lanes (tabung), coord, or a working worker. Fail-safe: any ambiguity
(pane unreadable, no clear wind-down signal) => SKIP (leave it running).

Reap = tmux kill-session + fleet_lanes[session].desired_state='down' + delete its agent_status
row + a P3 log to orch-console.

Usage: irsyad_worker_reaper.py [--once] [--grace-min 5] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

WORKER_SESSION_RE = re.compile(r"^irsyad-worker-\d+$")
PANE_BUSY_LIB = _ROOT / "scripts" / "lib" / "pane_busy.sh"


def is_worker_session(session: str) -> bool:
    """Only spun elastic workers (irsyad-worker-<N>) are reapable. Standing lanes (tabung=
    irsyad-tabung-jumaat, coord=irsyad-coord) never match -> never reaped, even if idle."""
    return bool(WORKER_SESSION_RE.match(session or ""))


def should_reap(session_is_worker: bool, pane_busy, wound_down_past_grace: bool) -> bool:
    """PURE reap decision (unit-testable). Reap iff ALL hold:
      - it is a spun worker session (standing lanes/coord excluded),
      - the pane is IDLE — pane_busy is exactly False (True=working, None=unknown => NEVER reap),
      - it wound down at least the grace ago (a just-wound-down worker is left for re-tasking).
    Fail-safe by construction: any ambiguity leaves the worker running."""
    return bool(session_is_worker) and pane_busy is False and bool(wound_down_past_grace)


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
                return line.split("=", 1)[1].strip()
    raise SystemExit("irsyad_worker_reaper: no DATABASE_URL")


def _pane_busy(session: str) -> bool | None:
    """True=busy(working), False=idle, None=unknown (fail-safe -> treat as busy, never reap).
    Uses the canonical footer-only busy check (scripts/lib/pane_busy.sh, sourced): the function
    `pane_busy <session>` exits 0=BUSY, 1=idle. Anything else (no such session / error) => None."""
    try:
        r = subprocess.run(
            ["bash", "-c", f'. "{PANE_BUSY_LIB}"; pane_busy "$1"', "_", session],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True    # BUSY (working)
        if r.returncode == 1:
            return False   # idle
        return None        # unknown session / error -> fail-safe
    except Exception:
        return None


def _live_workers(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT agent_id, tmux_session FROM agent_status "
            "WHERE base_agent_id='cc-irsyad' AND last_heartbeat > now() - interval '30 minutes'")
        return [(a, s) for a, s in cur.fetchall() if s and WORKER_SESSION_RE.match(s)]


def _wound_down_since(conn, agent_id: str, grace_min: int) -> bool:
    """True iff this worker's LATEST bus post is a WIND-DOWN at least grace_min old (so a just-
    wound-down worker keeps a grace window). Any later claim/done => not wound down."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT subject, body, created_at FROM agent_messages "
            "WHERE from_agent=%s ORDER BY created_at DESC LIMIT 1", (agent_id,))
        row = cur.fetchone()
        if not row:
            return False
        subj, body, created = row
        blob = f"{subj or ''} {body or ''}".lower()
        if "wind-down" not in blob and "winding down" not in blob and "wound down" not in blob:
            return False
        cur.execute("SELECT now() - %s > (%s * interval '1 minute')", (created, grace_min))
        return bool(cur.fetchone()[0])


def _reap(conn, agent_id: str, session: str, dry: bool) -> None:
    if dry:
        print(f"  [dry-run] would reap {agent_id} ({session}): kill-session + fleet_lanes down + clear status")
        return
    subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
    with conn.cursor() as cur:
        cur.execute("UPDATE fleet_lanes SET desired_state='down', updated_at=now() WHERE lane=%s", (session,))
        cur.execute("DELETE FROM agent_status WHERE tmux_session=%s", (session,))
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "requires_response,priority) VALUES ('cc-orchestrator','orch-console','update',%s,%s,false,'P3')",
            (f"REAPED wound-down worker {agent_id} ({session}) — pool shrinks",
             f"{agent_id} had wound down (idle + latest post = wind-down); reaped its lingering session "
             "so the autoscaler pool count shrinks and re-proposes on the next demand (op#20774 fix)."))
    conn.commit()
    print(f"  REAPED {agent_id} ({session}) — pool shrinks")


def run_once(grace_min: int, dry: bool) -> int:
    import psycopg
    reaped = 0
    with psycopg.connect(_dsn(), connect_timeout=15) as conn:
        workers = _live_workers(conn)
        if not workers:
            print("[worker-reaper] no live irsyad-worker-<N> sessions — nothing to do")
            return 0
        for agent_id, session in workers:
            busy = _pane_busy(session)
            wd = _wound_down_since(conn, agent_id, grace_min)
            if not should_reap(is_worker_session(session), busy, wd):
                print(f"[worker-reaper] {agent_id} ({session}) SKIP (worker={is_worker_session(session)} "
                      f"busy={busy} wound_down_past_grace={wd})")
                continue
            print(f"[worker-reaper] {agent_id} ({session}) idle + wound-down > {grace_min}m -> reaping")
            _reap(conn, agent_id, session, dry)
            reaped += 1
    print(f"[worker-reaper] done — reaped {reaped}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reap wound-down irsyad elastic workers.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--grace-min", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return run_once(args.grace_min, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
