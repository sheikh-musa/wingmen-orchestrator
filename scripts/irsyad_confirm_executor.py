#!/usr/bin/env python3
"""irsyad confirm-executor — make a standing spin CONFIRM always execute (or alert loudly).

Nazim #40639 bug: a 'CONFIRM SPIN <id>' posted as P1 rr=FALSE does NOT wake the hub (CAI-451
wake floor), so the confirm sat unexecuted until something else woke the hub — and meanwhile the
autoscaler re-proposed (duplicate). This closes both symptoms WITHOUT depending on the hub being
awake: a cron scans for standing confirms and, if the demand is still real, runs the actuator;
if the actuator refuses, it pages loudly.

Single source of truth for "should we spin?" is the autoscaler's OWN pure decide() (via
gather_and_decide) — so this never over-spins a demand that a live worker already covers (the
40636-vs-40638 duplicate class): if would_spin is no longer true, the confirm is treated as
already-satisfied and skipped, not executed.

"Executed" for proposal <id> = a cc-orchestrator bus row whose body contains 'for proposal #<id>'
(the actuator's _log_bus stamp) exists after the confirm. Idempotent: an already-executed confirm
is skipped.

Usage:
  irsyad_confirm_executor.py [--once] [--loop] [--interval 180] [--lookback-min 90] [--dry-run]
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

from scripts.irsyad_autoscaler import PROPOSAL_SUBJECT_PREFIX, gather_and_decide  # noqa: E402

SPIN_ACTUATOR = _ROOT / "scripts" / "irsyad_spin_worker.py"
CONFIRM_RE = re.compile(r"confirm\s+spin\s+(\d+)\b", re.IGNORECASE)


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    env = _ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
                return line.split("=", 1)[1].strip()
    raise SystemExit("irsyad_confirm_executor: no DATABASE_URL")


def _standing_confirms(conn, lookback_min: int):
    """Recent orch-console CONFIRM SPIN rows -> [(confirm_id, proposal_id, created_at)], newest first.
    Only confirms whose referenced proposal is a real autoscaler SPIN proposal are returned."""
    out = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, subject, body, created_at FROM agent_messages "
            "WHERE from_agent='orch-console' AND to_agent='cc-orchestrator' "
            "AND message_type = ANY(ARRAY['agreed','decision']) "
            "AND created_at > now() - (%s * interval '1 minute') "
            "ORDER BY created_at DESC",
            (lookback_min,))
        for cid, subj, body, created in cur.fetchall():
            m = CONFIRM_RE.search(f"{subj or ''}\n{body or ''}")
            if not m:
                continue
            pid = int(m.group(1))
            cur.execute(
                "SELECT 1 FROM agent_messages WHERE id=%s AND from_agent='cc-orchestrator' "
                "AND subject LIKE %s", (pid, PROPOSAL_SUBJECT_PREFIX + "%"))
            if cur.fetchone():
                out.append((cid, pid, created))
    return out


def _already_executed(conn, proposal_id: int, after) -> bool:
    """True if the actuator already ran for this proposal (its 'for proposal #<id>' stamp exists)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM agent_messages WHERE from_agent='cc-orchestrator' "
            "AND body LIKE %s AND created_at >= %s LIMIT 1",
            (f"%for proposal #{proposal_id}%", after))
        return cur.fetchone() is not None


def _alert(conn, subject: str, body: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "requires_response,priority) VALUES ('cc-orchestrator','orch-console','blocker',%s,%s,true,'P1')",
            (subject, body))
    conn.commit()


def run_once(dry: bool, lookback_min: int) -> int:
    import psycopg, socket
    with psycopg.connect(_dsn(), connect_timeout=15) as conn:
        confirms = _standing_confirms(conn, lookback_min)
        if not confirms:
            print("[confirm-executor] no standing CONFIRM SPIN rows in window — nothing to do")
            return 0
        # Only the NEWEST unexecuted confirm matters (older dups collapse to the same demand).
        for cid, pid, created in confirms:
            if _already_executed(conn, pid, created):
                print(f"[confirm-executor] confirm #{cid} (proposal {pid}) already executed — skip")
                continue
            # Re-check demand via the autoscaler's own decision — the single source of truth.
            d = gather_and_decide(conn, socket.gethostname())
            if not d.would_spin:
                print(f"[confirm-executor] confirm #{cid} (proposal {pid}) NOT executed but "
                      f"would_spin=False now ({d.reason}) — demand already covered, skip (no over-spin)")
                return 0
            print(f"[confirm-executor] confirm #{cid} (proposal {pid}) UNEXECUTED + would_spin=True "
                  f"-> running actuator")
            if dry:
                print(f"  [dry-run] would run: {SPIN_ACTUATOR} --proposal-id {pid}")
                return 0
            r = subprocess.run([sys.executable, str(SPIN_ACTUATOR), "--proposal-id", str(pid)])
            if r.returncode != 0:
                _alert(conn,
                       f"AUTOSCALER confirm-executor: actuator REFUSED proposal {pid} (rc={r.returncode})",
                       f"A standing CONFIRM SPIN {pid} (bus #{cid}) did not execute — the actuator "
                       f"returned rc={r.returncode}. Manual attention needed. (This alert is the "
                       "'post loudly why' half of #40639.)")
                print(f"[confirm-executor] actuator rc={r.returncode} — posted P1 alert")
                return r.returncode
            print(f"[confirm-executor] actuator ran OK for proposal {pid}")
            return 0
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Execute standing irsyad spin confirms (or alert).")
    ap.add_argument("--once", action="store_true", help="one pass (default)")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=180)
    ap.add_argument("--lookback-min", type=int, default=90)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.loop:
        import time
        print(f"[confirm-executor] loop up (every {args.interval}s)")
        while True:
            try:
                run_once(args.dry_run, args.lookback_min)
            except Exception as e:
                print(f"[confirm-executor] tick error ({type(e).__name__}): {e}")
            time.sleep(args.interval)
    return run_once(args.dry_run, args.lookback_min)


if __name__ == "__main__":
    sys.exit(main())
