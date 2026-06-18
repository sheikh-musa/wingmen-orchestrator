#!/usr/bin/env python3
"""fleet_health.py — standing fleet-health sweep (cheap, ~0 model tokens).

The orchestrator used to notice dead lanes only when the operator did. This
runs on a launchd timer so the substrate self-heals:

  1. STALE -> offline: any agent whose heartbeat is older than STALE_MIN is
     marked offline (the Fleet Console then shows reality, not a phantom).
  2. REAP: offline rows older than PRUNE_DAYS are deleted (no 50-day zombies).
  3. RECONCILE: cross-check agent_status against live tmux sessions and flag
     drift — a "working" row with no live session (crashed lane) or a live
     CC session with no fresh row (unregistered lane).

Read-only on the model side; only touches agent_status (status + delete).
Run on demand:  python3 scripts/fleet_health.py [--quiet]
"""
import os, re, subprocess, sys
import psycopg
from dotenv import load_dotenv

STALE_MIN = 30      # heartbeat older than this => mark offline
PRUNE_DAYS = 1      # offline rows older than this => delete

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def live_tmux_sessions():
    try:
        out = subprocess.run(["tmux", "ls"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return set()
    return {ln.split(":", 1)[0] for ln in out.splitlines() if ":" in ln}


def main():
    quiet = "--quiet" in sys.argv
    sessions = live_tmux_sessions()
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-orchestrator',true)")

        cur.execute(f"""UPDATE agent_status SET status='offline'
                        WHERE status <> 'offline'
                          AND last_heartbeat < now() - interval '{STALE_MIN} minutes'
                        RETURNING agent_id""")
        marked = [r[0] for r in cur.fetchall()]

        cur.execute(f"""DELETE FROM agent_status
                        WHERE status='offline'
                          AND last_heartbeat < now() - interval '{PRUNE_DAYS} days'
                        RETURNING agent_id""")
        pruned = [r[0] for r in cur.fetchall()]
        conn.commit()

        cur.execute("""SELECT agent_id, status, tmux_session,
                       round(extract(epoch from (now()-last_heartbeat))/60) AS m
                       FROM agent_status ORDER BY last_heartbeat DESC NULLS LAST""")
        rows = cur.fetchall()

    # reconcile against ground truth
    drift = []
    registered_sessions = set()
    for aid, status, tmux, m in rows:
        if tmux:
            registered_sessions.add(tmux)
        if status == "working" and tmux and tmux not in sessions:
            drift.append(f"  ! {aid}: 'working' but tmux '{tmux}' is GONE (crashed?)")
    # live CC sessions with no fresh registration (ignore the desktop 'claude' app + helpers)
    for s in sessions:
        if s not in registered_sessions and not re.match(r"^(claude|orchestrator)$", s):
            drift.append(f"  ? tmux '{s}' alive but no agent_status row points to it (unregistered/zombie)")

    if not quiet:
        if marked:
            print("marked offline (stale heartbeat):", ", ".join(marked))
        if pruned:
            print("reaped (old offline rows):", ", ".join(pruned))
        print(f"live lanes: {sum(1 for r in rows if r[1]=='working')} working / {len(rows)} rows")
        for aid, status, tmux, m in rows:
            print(f"  {aid:<20} {status:<9} hb={m}m  tmux={tmux}")
        if drift:
            print("DRIFT:")
            print("\n".join(drift))
        else:
            print("no drift — DB matches live tmux")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
