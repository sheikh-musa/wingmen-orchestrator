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
  4. ARCHIVE dead-letters: mark read the unread agent_messages addressed to
     agents that are gone (no fresh heartbeat AND no sent-message in 24h) and
     older than ARCHIVE_MIN_AGE_H. Keeps the console's bus/queue counts honest
     (dead lanes accumulate tombstones nobody drains — operator-flagged 2026-07-16)
     WITHOUT hiding anything live: recipients that heartbeat OR have sent recently
     (incl. unregistered coordinators like cc-orchestrator/orch-console) are
     spared, human-addressed (musa/operator) + substrate rows are never touched,
     and only messages older than the grace window are swept.

Only touches agent_status (status + delete) and agent_messages.read_at (archive).
Run on demand:  python3 scripts/fleet_health.py [--quiet] [--no-archive]
"""
import os, re, subprocess, sys
import psycopg
from dotenv import load_dotenv

STALE_MIN = 30      # heartbeat older than this => mark offline
PRUNE_DAYS = 1      # offline rows older than this => delete
ARCHIVE_MIN_AGE_H = 72   # dead-letter grace: only archive unread older than this
SENDER_LIVE_WINDOW_H = 24  # a recipient that SENT within this window is "live" -> spared

# Singletons are NEVER auto-reaped by heartbeat-staleness. Their liveness is
# tracked by lease/reclaim machinery, not agent_status heartbeats — the hub in
# particular self-registers but does NOT continuously heartbeat (boots via
# `claude --continue`, not the launch_dangerous_cc.sh heartbeat loop), so it
# looks "stale" while fully alive, and its status<>'offline' is load-bearing for
# wake resolution (resolve_tmux_session; CAI-RESP-451 / hub #16415). Marking a
# live singleton offline breaks its wake path. The protected set is read ONCE per
# run from the protected_agents table (mig-038 / CAI-762/765) — the SINGLE source
# of truth also consulted by admin_mark_offline + the launcher/watchdog reapers —
# and spares them from offline-marking, pruning, AND the drift check.

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
        # This sweep runs as cc-fleet-health (the fleet_health lease holder); the
        # reaper below asserts that lease, so declare our own identity here.
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")

        # Protected singletons — ONE source of truth (protected_agents, mig-038):
        # consulted below by offline-marking, prune, AND the drift check.
        cur.execute("SELECT agent_id FROM protected_agents")
        protected = {r[0] for r in cur.fetchall()}

        # STALE -> offline via the sanctioned reaper admin_mark_offline() (mig-037 /
        # CAI-RESP-761): a lease-gated, offline-only, audited SECDEF primitive called
        # once per agent. Replaces the old batch UPDATE, which the hardened
        # enforce_agent_status_identity trigger rejects (one GUC can't match every
        # row). Each call writes a truthful admin_offline_audit row. If our lease has
        # lapsed the call fail-closes and the txn aborts LOUD — the dead-man's switch.
        cur.execute(f"""SELECT agent_id FROM agent_status
                        WHERE status <> 'offline'
                          AND last_heartbeat < now() - interval '{STALE_MIN} minutes'""")
        marked = []
        for _aid in [r[0] for r in cur.fetchall()]:
            if _aid in protected:
                continue  # singleton — liveness is lease-tracked, never heartbeat-reaped
            cur.execute("SELECT admin_mark_offline(%s, %s)",
                        (_aid, f"fleet_health sweep: stale heartbeat > {STALE_MIN}m"))
            if cur.fetchone()[0]:
                marked.append(_aid)

        cur.execute(f"""DELETE FROM agent_status
                        WHERE status='offline'
                          AND last_heartbeat < now() - interval '{PRUNE_DAYS} days'
                          AND agent_id <> ALL(%s)
                        RETURNING agent_id""", (list(protected),))
        pruned = [r[0] for r in cur.fetchall()]
        conn.commit()

        # 4. ARCHIVE dead-letters — mark read unread messages to gone agents.
        # "Gone" = no fresh heartbeat AND hasn't sent in SENDER_LIVE_WINDOW_H
        # (the sent-recently clause spares unregistered coordinators that never
        # write agent_status, e.g. cc-orchestrator/orch-console). Human + substrate
        # rows excluded; only messages past the ARCHIVE_MIN_AGE_H grace are swept.
        archived = []
        if "--no-archive" not in sys.argv:
            cur.execute(f"""
                UPDATE agent_messages SET read_at = now()
                WHERE read_at IS NULL
                  AND to_agent IS NOT NULL
                  AND lower(to_agent) NOT IN ('musa','operator','substrate')
                  AND created_at < now() - interval '{ARCHIVE_MIN_AGE_H} hours'
                  AND to_agent NOT IN (
                      SELECT DISTINCT from_agent FROM agent_messages
                      WHERE from_agent IS NOT NULL
                        AND created_at > now() - interval '{SENDER_LIVE_WINDOW_H} hours')
                  AND to_agent NOT IN (
                      SELECT agent_id FROM agent_status
                      WHERE last_heartbeat > now() - interval '{STALE_MIN} minutes')
                RETURNING to_agent""")
            archived = [r[0] for r in cur.fetchall()]
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
        # Skip singletons: their tmux may live on another host (the hub runs on the
        # VPS, not this Mini) and their liveness is lease/reclaim-tracked, so a local
        # `tmux ls` miss is NOT a crash — flagging it manufactures a false alarm.
        if status == "working" and tmux and tmux not in sessions and aid not in protected:
            drift.append(f"  ! {aid}: 'working' but tmux '{tmux}' is GONE (crashed?)")
    # live CC sessions with no fresh registration (ignore the desktop 'claude' app + helpers)
    for s in sessions:
        if s not in registered_sessions and not re.match(r"^(claude|orchestrator)$", s):
            drift.append(f"  ? tmux '{s}' alive but no agent_status row points to it (unregistered/zombie)")

    # archive is a mutation — always log it (even in --quiet), it's rare + auditable
    if archived:
        from collections import Counter
        by = Counter(archived)
        print(f"archived {len(archived)} dead-letters: " +
              ", ".join(f"{a}×{n}" for a, n in by.most_common()))

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
