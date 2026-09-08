#!/usr/bin/env python3
"""gzb_hub_heartbeat — the gzb hub's OWN agent_status liveness writer (dead-man's-switch).

Why this exists (2026-09-05 outage): after the hub flip to gzb, gzb's orch_supervisor.sh
only spun the tmux session and waited — it NEVER beat agent_status. So the only writer of
cc-orchestrator's row was the Mac Mini's launchd `dev.wingmen.cc-orch` (host=Sheikhs-Mini),
which has ZERO knowledge of whether the gzb session is alive. When the gzb session sat stuck
at the OAuth login screen for ~1.5h, that stale Mini heartbeat kept the row reading
status='working' — MASKING the dead session so no watchdog escalated and the operator went
unheard. host-alive (orch_lease, renewed by a root timer independent of the session) also
could not catch it: a fresh lease means the HOST is up, not that the SESSION is up.

The fix: beat agent_status host=<this host> ONLY while the hub tmux session actually exists.
When the session dies the loop sees `tmux has-session` fail and STOPS (marking offline once),
so the heartbeat goes stale/offline and the fleet detects the death. This is the same
dead-man's-switch shape as boot_orch.sh's `while has-session` loop and boot_nazim.sh's
`_console_heartbeat_loop` (CAI-RESP-791) — the identity GUC is set in the SAME txn as the
UPDATE or the hardened agent_status identity trigger (BUG-024/ARCH-035) rejects the write.

SINGLE SOURCE for both callers (no drift): run interim/by-hand now, AND launched by
orch_supervisor.sh for reboot durability. Whoever calls it, the rules are identical.

CRITICAL: gate the beat on `tmux has-session` EVERY iteration. A loop that beats
unconditionally (or one orphaned by nohup that never rechecks) becomes a liveness-LIE —
a heartbeat outliving a dead body — which is the exact failure this file closes.

Env: DATABASE_URL / SUPABASE_DB_URL (substrate DSN, required); ORCH_TMUX_SESSION (default
'orch'); ORCH_AGENT_ID (default 'cc-orchestrator'); ORCH_HB_EVERY_SEC (default 60).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time

SESSION = os.environ.get("ORCH_TMUX_SESSION", "orch").strip() or "orch"
AGENT_ID = os.environ.get("ORCH_AGENT_ID", "cc-orchestrator").strip() or "cc-orchestrator"
INTERVAL = int(os.environ.get("ORCH_HB_EVERY_SEC", "60") or "60")
HOST = socket.gethostname()


def _dsn() -> str:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL") or ""


def _session_alive() -> bool:
    """Exact-match the session (=name) so it never resolves a same-prefix session."""
    tmux = shutil.which("tmux") or "/usr/bin/tmux"
    try:
        r = subprocess.run([tmux, "has-session", "-t", f"={SESSION}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return r.returncode == 0
    except Exception:  # noqa: BLE001 — a tmux hiccup must not be read as death; keep beating
        return True


def _write(status: str) -> None:
    import psycopg
    dsn = _dsn()
    if not dsn:
        print("[gzb_hub_heartbeat] FATAL: no DSN in env", file=sys.stderr)
        sys.exit(2)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # identity GUC in the SAME txn as the write (BUG-024/ARCH-035) or the trigger rejects.
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (AGENT_ID,))
        # Stamp tmux_session too (Nazim bus 37957): this writer's has-session check keys on
        # SESSION, so the row's tmux field must read that same session, not a stale value —
        # else a session-keyed liveness reader sees the wrong tmux for the hub.
        cur.execute(
            "UPDATE agent_status SET status=%s, host=%s, tmux_session=%s, "
            "last_heartbeat=now(), updated_at=now() WHERE agent_id=%s",
            (status, HOST, SESSION, AGENT_ID))
        conn.commit()


def main() -> int:
    if not _dsn():
        print("[gzb_hub_heartbeat] FATAL: no DSN in env", file=sys.stderr)
        return 2
    print(f"[gzb_hub_heartbeat] up — agent={AGENT_ID} host={HOST} session='{SESSION}' "
          f"every {INTERVAL}s (dead-man on tmux =\"{SESSION}\")", flush=True)
    # Immediate beat so host flips to this host NOW rather than after one interval.
    if _session_alive():
        try:
            _write("working")
        except Exception as e:  # noqa: BLE001 — transient DB blip: log, keep looping
            print(f"[gzb_hub_heartbeat] initial beat failed (transient?): {e}", file=sys.stderr, flush=True)
    while True:
        time.sleep(INTERVAL)
        if not _session_alive():
            # Session gone -> stop lying. Mark offline once, then exit; systemd/the supervisor
            # owns re-launch. Real death is now visible as stale/offline to the watchdogs.
            try:
                _write("offline")
                print("[gzb_hub_heartbeat] session gone — marked offline, exiting (dead-man)", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[gzb_hub_heartbeat] offline mark failed on exit: {e}", file=sys.stderr, flush=True)
            return 0
        try:
            _write("working")
        except Exception as e:  # noqa: BLE001 — never crash the loop on a transient DB error
            print(f"[gzb_hub_heartbeat] beat failed (transient?): {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
