#!/usr/bin/env python3
"""irsyad worker-reaper — tear down WOUND-DOWN elastic workers so the pool actually shrinks.

The bug this fixes (Musa op#20774 / Nazim #40833): an elastic worker winds down when the queue
is empty, but its tmux session LINGERS at an idle prompt and keeps heartbeating — so the
autoscaler still counts it toward MAX_LANES. The pool never shrinks → the autoscaler stops
proposing → new demand sits until someone manually prompts. That is exactly the "only built when
prompted" complaint. The elastic contract is: wind down → session GOES AWAY → pool shrinks →
autoscaler re-proposes on the next demand. This reaper is the missing "session goes away" step.

Reaps ONLY a spun elastic worker (tmux session 'irsyad-worker-<N>') that is ALL of:
  (a) idle — its tmux pane is NOT busy (composer_capture: no active turn / not 'Waiting for
      background agents'); a re-engaged worker is never reaped; AND
  (b) wound down — its LATEST bus post (from its own agent_id) is a WIND-DOWN, at least
      REAP_GRACE_MIN old (so a just-wound-down worker gets a grace window to be re-tasked); AND
  (c) idle-proof — it has NO pending order: no unresponded `requires_response` bus row
      addressed to it (a HOLD/direct-dispatch from orch-console or cc-irsyad-coord included),
      and no `coord_dispatch_queue` row it has claimed that isn't done yet. "No bus activity
      FROM it" is not sufficient proof of idle — a worker can go quiet while an order TO it
      sits unacked (bus #54606: the reaper killed cc-irsyad-2 despite an unacked HOLD posted
      to it 2 min earlier). (c) closes that gap.
Never touches standing lanes (tabung), coord, or a working worker. Fail-safe: any ambiguity
(pane unreadable, no clear wind-down signal, pending-order check errors) => SKIP (leave it
running).

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


def should_reap(session_is_worker: bool, pane_busy, wound_down_past_grace: bool,
                has_pending_order: bool = False) -> bool:
    """PURE reap decision (unit-testable). Reap iff ALL hold:
      - it is a spun worker session (standing lanes/coord excluded),
      - the pane is IDLE — pane_busy is exactly False (True=working, None=unknown => NEVER reap),
      - it wound down at least the grace ago (a just-wound-down worker is left for re-tasking),
      - it has NO pending order (bus #54606 idle-proof gap — see module docstring (c)).
    Fail-safe by construction: any ambiguity leaves the worker running."""
    return (bool(session_is_worker) and pane_busy is False and bool(wound_down_past_grace)
            and not bool(has_pending_order))


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


def _has_pending_order(conn, agent_id: str) -> bool:
    """True iff this worker has a PENDING ORDER — idle-proof (c), bus #54606. Checks:
      (a)/(b) an unresponded requires_response bus row addressed TO it (a HOLD/direct-dispatch
          from orch-console or cc-irsyad-coord, or any other sender — "no bus activity FROM it"
          does not prove idle when an order TO it is still unacked); and
      (c) a `coord_dispatch_queue` row it has claimed (claimed_by=agent_id) that is not done.
    Fail-safe: a query error => treat as pending (never reap on ambiguity)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agent_messages WHERE to_agent=%s AND requires_response=true "
                "AND responded_at IS NULL LIMIT 1", (agent_id,))
            if cur.fetchone():
                return True
            cur.execute(
                "SELECT 1 FROM coord_dispatch_queue WHERE claimed_by=%s AND done_at IS NULL "
                "LIMIT 1", (agent_id,))
            if cur.fetchone():
                return True
        return False
    except Exception as exc:
        print(f"  [worker-reaper] WARN: pending-order check failed for {agent_id} ({exc}) "
              "-> fail-safe, treating as pending")
        return True


def _stale_rr_only_skip(conn, agent_id: str, stale_hours: int = 24) -> list[int]:
    """Ids of unresponded requires_response rows addressed to agent_id older than
    stale_hours (bus #58316). Deliberately separate from _has_pending_order: this never
    feeds the reap decision (age must NOT shrink the idle-proof gap, bus fleet-health
    #58271 -- an instance-addressed row stays pending no matter how old). It only tells
    the caller whether a SKIP is attributable to a stale order, so coord can be nudged."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM agent_messages WHERE to_agent=%s AND requires_response=true "
                "AND responded_at IS NULL AND created_at < now() - (%s * interval '1 hour')",
                (agent_id, stale_hours))
            return [r[0] for r in cur.fetchall()]
    except Exception as exc:
        print(f"  [worker-reaper] WARN: stale-rr check failed for {agent_id} ({exc})")
        return []


def _already_surfaced_recently(conn, agent_id: str, within_hours: int = 24) -> bool:
    """Dedup: one surfaced row per worker per within_hours, not one per reaper tick.
    LIKE prefix is anchored on the ' (' that always follows agent_id in the subject
    (cc-quality #58499 finding 1) -- an unanchored trailing '%' let a shorter id
    (cc-irsyad-3) falsely match a longer sibling's row (cc-irsyad-31), suppressing
    its own nudge for up to 24h. Fail-safe: a query error => treat as already
    surfaced (suppress this tick's nudge rather than risk crashing the reaper loop
    over a nudge-only feature -- cc-quality #58499 finding 2)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agent_messages WHERE from_agent='cc-orchestrator' "
                "AND to_agent='cc-irsyad-coord' AND subject LIKE %s "
                "AND created_at > now() - (%s * interval '1 hour') LIMIT 1",
                (f"stale requires_response blocking reap: {agent_id} (%", within_hours))
            return cur.fetchone() is not None
    except Exception as exc:
        print(f"  [worker-reaper] WARN: surfaced-recently check failed for {agent_id} ({exc}) "
              "-> fail-safe, suppressing this tick's nudge")
        return True


def _surface_stale_rr_to_coord(conn, agent_id: str, session: str, row_ids: list[int]) -> None:
    """Fail-safe: a failed insert must never crash the reaper loop over a nudge-only
    feature (cc-quality #58499 finding 2) -- the reap decision above this call has
    already run and is unaffected."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
                "requires_response,priority) VALUES ('cc-orchestrator','cc-irsyad-coord','update',%s,%s,false,'P2')",
                (f"stale requires_response blocking reap: {agent_id} ({session})",
                 f"{agent_id} ({session}) is idle + wound-down past grace, but reap is held back "
                 f"solely by unresponded requires_response row(s) older than 24h: "
                 f"{', '.join(str(i) for i in row_ids)}. The reap decision does not change with age "
                 "-- an unacked order still blocks reap -- this is a surface-only nudge so coord can "
                 "clear or re-ack it. Deduped: at most one of these per worker per 24h."))
        conn.commit()
    except Exception as exc:
        print(f"  [worker-reaper] WARN: stale-rr surface failed for {agent_id} ({exc})")


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
            pending = _has_pending_order(conn, agent_id)
            if not should_reap(is_worker_session(session), busy, wd, pending):
                print(f"[worker-reaper] {agent_id} ({session}) SKIP (worker={is_worker_session(session)} "
                      f"busy={busy} wound_down_past_grace={wd} has_pending_order={pending})")
                if is_worker_session(session) and busy is False and wd and pending:
                    stale_ids = _stale_rr_only_skip(conn, agent_id)
                    if stale_ids and not _already_surfaced_recently(conn, agent_id):
                        _surface_stale_rr_to_coord(conn, agent_id, session, stale_ids)
                        print(f"  [worker-reaper] surfaced {len(stale_ids)} stale requires_response "
                              f"row(s) for {agent_id} to cc-irsyad-coord")
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
