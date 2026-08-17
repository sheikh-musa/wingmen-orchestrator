#!/usr/bin/env python3
"""fleet_health_bus_notify — realtime poke for the cc-fleet-health SRE lane on NEW bus rows.

Peer of nazim_bus_notify (Nazim), cai_bus_notify (cai) and finance_bus_notify
(cc-finance): cc-fleet-health is the fleet's SRE singleton lane, and it was the
ONLY coordinator WITHOUT a realtime bus-notifier — it noticed new agent_messages
only on its slower detect->act heartbeat, not the instant one landed
(operator-flagged gap). This closes it so the SRE is a first-class realtime peer.

Design (mirrors finance_bus_notify — cc-fleet-health is a claude worker/SRE LANE,
structurally like cc-finance, NOT the nazim console; the ghost-aware lane nudge is
the correct injection for a lane, not raw send-keys):
  - Injection is DELEGATED to scripts/lane_nudge.sh — the fleet's sanctioned
    VERIFIED-SUBMIT lane nudge (ghost-aware so it never clobbers cc-fleet-health's
    own staged next-step; confirms the pane actually entered a working state). We
    never send-keys the fleet-health pane directly.
  - Dedup on the newest row id (never re-nudge the same rows) + a debounce so a
    burst of rows is ONE poke, not a storm.
  - Substrate stays source of truth; a dropped keystroke loses nothing (the
    SRE's own heartbeat remains the reactive backstop). Signal, not delivery.

Requires: DATABASE_URL in the orch .env; scripts/lane_nudge.sh reachable;
tmux session `fleet-health` live on this host.
"""
import os
import subprocess
import sys
import time

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLL_SEC = 20
MIN_NUDGE_INTERVAL_SEC = 45   # debounce: coalesce bursts into one poke
LANE_NUDGE = os.path.join(ORCH_DIR, "scripts", "lane_nudge.sh")
TARGET = "cc-fleet-health"
SESSION = os.environ.get("FLEET_HEALTH_TMUX_SESSION", "fleet-health")


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    for line in open(os.path.join(ORCH_DIR, ".env")):
        if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
            return line.split("=", 1)[1].strip()
    raise SystemExit("fleet_health_bus_notify: no DATABASE_URL")


def _log(msg: str) -> None:
    print(f"[fleet-health-bus-notify] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def _max_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(id),0) FROM agent_messages WHERE to_agent=%s", (TARGET,))
        return cur.fetchone()[0]


def _unread_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent=%s AND read_at IS NULL", (TARGET,))
        return cur.fetchone()[0]


def _nudge(unread: int) -> None:
    """Delegate to the sanctioned verified-submit lane nudge. Ghost-aware; a
    missing/busy session it handles itself. Count-only reconcile line — no content."""
    line = (f"\U0001F4E5 {unread} new bus msg(s) — reconcile your inbox "
            f"(agent_messages to_agent='{TARGET}') now.")
    try:
        r = subprocess.run(["bash", LANE_NUDGE, SESSION, line],
                           capture_output=True, text=True, timeout=60)
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] if (r.stdout or r.stderr) else []
        _log(f"lane_nudge.sh({SESSION}) rc={r.returncode} :: {tail}")
    except Exception as e:
        _log(f"lane_nudge.sh error ({type(e).__name__}): {e}")


def main() -> int:
    import psycopg
    dsn = _dsn()
    with psycopg.connect(dsn) as conn:
        last_max = _max_id(conn)
    _log(f"up — watching agent_messages->{TARGET} (from id>{last_max}, poll {POLL_SEC}s, via lane_nudge.sh)")
    # None (not 0.0): time.monotonic() is process-relative here, so a 0.0 seed
    # would debounce-suppress the FIRST nudge — the one that matters most.
    last_nudge = None
    while True:
        try:
            with psycopg.connect(dsn) as conn:
                mx = _max_id(conn)
                if mx > last_max:
                    now = time.monotonic()
                    if last_nudge is None or now - last_nudge >= MIN_NUDGE_INTERVAL_SEC:
                        _nudge(_unread_count(conn))
                        last_nudge = now
                    else:
                        _log(f"new rows (id>{last_max}) but within debounce — coalescing")
                    last_max = mx
        except Exception as e:
            _log(f"poll error ({type(e).__name__}): {e}")
        time.sleep(POLL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
