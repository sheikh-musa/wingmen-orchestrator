#!/usr/bin/env python3
"""irsyad spin-actuator — boot ONE elastic pool worker, ONLY on Nazim's confirm.

This is the ACTUATION step the armed autoscaler (scripts/irsyad_autoscaler.py, SUPERVISED-
propose) deliberately does NOT do. Flow (Nazim #40427/#40443):

  autoscaler tick -> demand>=1 & pool<MAX_LANES -> posts a deduped P1 rr "[irsyad-autoscaler]
  SPIN proposal" bus row to orch-console -> Nazim CONFIRMS on the bus -> the hub runs THIS to
  boot a cc-irsyad-<N> worker that claims from public.coord_dispatch_queue.

FAIL-CLOSED: without a valid confirm row for the given proposal it refuses. It also refuses to
exceed MAX_LANES. The worker BOOT is /home/gazzai/irsyad_worker_supervisor.sh (musa2 via the
session name's irsyad family; identity cc-irsyad-<N> via CC_BASE_OVERRIDE=cc-irsyad).

CONFIRM CONTRACT (what counts as Nazim's go for proposal <PID>): a bus row
  from_agent='orch-console', to_agent='cc-orchestrator',
  message_type IN ('agreed','decision'), created_at > proposal.created_at,
  and body/subject contains the token 'CONFIRM SPIN <PID>' (case-insensitive).
Deterministic + unambiguous so it can never fire on a stray 'ok'. Documented for Nazim.

Usage:
  irsyad_spin_worker.py --proposal-id <bus id>   [--dry-run]
  irsyad_spin_worker.py --proposal-id <bus id> --wetprove-synthetic   (bypass confirm; SYNTHETIC only)
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.irsyad_autoscaler import MAX_LANES, PROPOSAL_SUBJECT_PREFIX  # noqa: E402

IHSANOS_REPO = Path.home() / "wingmen" / "projects" / "ihsanos"
WORKER_SUPERVISOR = Path.home() / "irsyad_worker_supervisor.sh"
POOL_LANE_PREFIX = "irsyad-worker-"     # fleet_lanes pool registry the actuator owns
CONFIRM_RE_TMPL = r"confirm\s+spin\s+{pid}\b"


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
                return line.split("=", 1)[1].strip()
    raise SystemExit("irsyad_spin_worker: no DATABASE_URL")


def _confirm_ok(conn, proposal_id: int) -> tuple[bool, str]:
    """Fail-closed check that the proposal exists AND Nazim confirmed it per the contract."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT subject, created_at FROM agent_messages "
            "WHERE id=%s AND from_agent='cc-orchestrator' AND to_agent='orch-console' "
            "AND subject LIKE %s",
            (proposal_id, PROPOSAL_SUBJECT_PREFIX + "%"))
        prop = cur.fetchone()
        if not prop:
            return False, f"no autoscaler SPIN proposal #{proposal_id} found (from cc-orchestrator to orch-console)"
        pat = CONFIRM_RE_TMPL.format(pid=proposal_id)
        cur.execute(
            "SELECT id, subject, body FROM agent_messages "
            "WHERE from_agent='orch-console' AND to_agent='cc-orchestrator' "
            "AND message_type = ANY(ARRAY['agreed','decision']) "
            "AND created_at > %s "
            "ORDER BY created_at DESC LIMIT 25",
            (prop[1],))
        for cid, subj, body in cur.fetchall():
            blob = f"{subj or ''}\n{body or ''}"
            if re.search(pat, blob, re.IGNORECASE):
                return True, f"confirmed by orch-console bus #{cid} (matched 'CONFIRM SPIN {proposal_id}')"
    return False, (f"proposal #{proposal_id} found but NO confirm — need an orch-console "
                   f"agreed/decision row containing 'CONFIRM SPIN {proposal_id}'")


def _live_pool_slots(conn) -> set[int]:
    """Pool slots currently up in the fleet_lanes registry the actuator owns."""
    slots: set[int] = set()
    with conn.cursor() as cur:
        cur.execute("SELECT lane FROM fleet_lanes WHERE lane LIKE %s AND desired_state='up'",
                    (POOL_LANE_PREFIX + "%",))
        for (lane,) in cur.fetchall():
            m = re.match(re.escape(POOL_LANE_PREFIX) + r"(\d+)$", lane)
            if m:
                slots.add(int(m.group(1)))
    return slots


def _allocate_slot(live: set[int]) -> Optional[int]:
    for n in range(1, MAX_LANES + 1):
        if n not in live:
            return n
    return None


def _ensure_worktree(n: int, dry: bool) -> Path:
    wt = Path.home() / "wingmen" / "projects" / f"ihsanos-irsyad.wt-worker{n}"
    if wt.exists():
        return wt
    branch = f"lane/irsyad-worker{n}"
    cmd = ["git", "-C", str(IHSANOS_REPO), "worktree", "add", "-B", branch, str(wt), "origin/main"]
    if dry:
        print(f"  [dry-run] would create worktree: {' '.join(cmd)}")
        return wt
    print(f"  creating worktree: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    return wt


def _register_pool_row(conn, n: int, wt: Path, dry: bool) -> None:
    lane = f"{POOL_LANE_PREFIX}{n}"
    if dry:
        print(f"  [dry-run] would upsert fleet_lanes[{lane}] base_agent_id=cc-irsyad desired_state=up wt={wt}")
        return
    with conn.cursor() as cur:
        # launcher must satisfy fleet_lanes_launcher_check (allow-list). The worker's underlying
        # launcher IS launch_dangerous_cc.sh; irsyad_worker_supervisor.sh wraps it (noted below).
        cur.execute(
            "INSERT INTO fleet_lanes (lane, base_agent_id, worktree_path, branch, launcher, desired_state, notes) "
            "VALUES (%s,'cc-irsyad',%s,%s,'launch_dangerous_cc.sh','up',%s) "
            "ON CONFLICT (lane) DO UPDATE SET base_agent_id='cc-irsyad', worktree_path=EXCLUDED.worktree_path, "
            "branch=EXCLUDED.branch, launcher=EXCLUDED.launcher, desired_state='up', "
            "notes=EXCLUDED.notes, updated_at=now()",
            (lane, str(wt), f"lane/irsyad-worker{n}",
             "Elastic irsyad autoscaler pool worker (boot-on-demand via irsyad_worker_supervisor.sh, "
             "winds down when queue empty). musa2 via session family; identity cc-irsyad-<N>."))
    conn.commit()
    print(f"  registered fleet_lanes[{lane}] desired_state=up")


def _boot(n: int, dry: bool) -> int:
    if dry:
        print(f"  [dry-run] would boot: bash {WORKER_SUPERVISOR} {n}")
        return 0
    print(f"  booting worker: bash {WORKER_SUPERVISOR} {n}")
    return subprocess.run(["bash", str(WORKER_SUPERVISOR), str(n)]).returncode


def _log_bus(conn, n: int, detail: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,requires_response,priority) "
            "VALUES ('cc-orchestrator','orch-console','update',%s,%s,false,'P1')",
            (f"SPUN cc-irsyad worker (pool slot {n}) — claiming from coord_dispatch_queue",
             detail))
    conn.commit()


def _already_booted_for(conn, proposal_id: int) -> bool:
    """One-proposal-one-boot idempotency (Nazim #40854 (1)): a boot already stamped 'for proposal
    #<id>' means this proposal was actuated — refuse a second boot for the same proposal id."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM agent_messages WHERE from_agent='cc-orchestrator' "
            "AND body LIKE %s LIMIT 1", (f"%for proposal #{proposal_id}%",))
        return cur.fetchone() is not None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="irsyad spin-actuator — boot one elastic pool worker.")
    ap.add_argument("--proposal-id", type=int, default=None,
                    help="bus id of the autoscaler SPIN proposal Nazim confirmed (confirm-gated path)")
    ap.add_argument("--auto", action="store_true",
                    help="FULLY-AUTO: skip the confirm gate (autoscaler already decided would_spin); "
                         "the demand-gate + MAX_LANES + cap still apply")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no worktree/boot/register")
    ap.add_argument("--wetprove-synthetic", action="store_true",
                    help="SYNTHETIC wet-prove: bypass confirm AND demand gate (synthetic queue row only)")
    args = ap.parse_args(argv)
    if not (args.auto or args.wetprove_synthetic or args.proposal_id is not None):
        print("[spin] REFUSING — need --proposal-id (confirm path), --auto, or --wetprove-synthetic.")
        return 2

    import psycopg
    with psycopg.connect(_dsn(), connect_timeout=15) as conn:
        if args.wetprove_synthetic:
            print("[spin] WET-PROVE mode — confirm + demand gates BYPASSED (synthetic-only).")
        elif args.auto:
            print("[spin] AUTO mode — confirm gate bypassed (autoscaler would_spin authority); demand-gate + cap still enforced.")
        else:
            ok, why = _confirm_ok(conn, args.proposal_id)
            print(f"[spin] confirm gate: {why}")
            if not ok:
                print("[spin] REFUSING — fail-closed (no valid Nazim confirm).")
                return 2
            if _already_booted_for(conn, args.proposal_id):
                print(f"[spin] REFUSING — proposal #{args.proposal_id} was ALREADY booted (idempotency). No double-spin.")
                return 5

        # DEMAND GATE (single source of truth): re-check the autoscaler's OWN would_spin right
        # before actuating. This aligns the actuator with the autoscaler + confirm-executor so a
        # manual run and the cron can't DOUBLE-SPIN one confirm (the 2nd sees pool grown / demand
        # met => would_spin=False => refuse). Skipped only for a synthetic wet-prove.
        if not args.wetprove_synthetic:
            import socket as _socket
            from scripts.irsyad_autoscaler import gather_and_decide as _decide
            d = _decide(conn, _socket.gethostname())
            print(f"[spin] demand gate: {d.reason}")
            if not d.would_spin:
                print("[spin] REFUSING — autoscaler would_spin=False now (demand already covered / "
                      "pool at cap incl. standing lanes). No double-spin.")
                return 4

        live = _live_pool_slots(conn)
        n = _allocate_slot(live)
        print(f"[spin] pool slots up={sorted(live)} MAX_LANES={MAX_LANES} -> allocate slot={n}")
        if n is None:
            print(f"[spin] REFUSING — pool already at MAX_LANES={MAX_LANES}.")
            return 3

        wt = _ensure_worktree(n, args.dry_run)
        _register_pool_row(conn, n, wt, args.dry_run)
        rc = _boot(n, args.dry_run)
        if rc != 0 and not args.dry_run:
            print(f"[spin] WARN worker supervisor rc={rc} — prompt delivery may need a P1 escalation.")
        if not args.dry_run:
            ref = (f"for proposal #{args.proposal_id}" if args.proposal_id is not None
                   else ("AUTO-spin on demand" if args.auto else "wet-prove"))
            _log_bus(conn, n, f"Booted irsyad-worker-{n} (identity cc-irsyad-<N>, musa2) off {wt} "
                              f"{ref}; worker runs the claim-build-loop "
                              f"vs coord_dispatch_queue and winds down when empty.")
        print(f"[spin] done (slot {n}, rc {rc}).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
