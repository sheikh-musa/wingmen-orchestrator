#!/usr/bin/env python3
"""cai_bus_notify — realtime poke for the cai governance node on NEW fleet bus rows.

The peer of nazim_bus_notify (which pokes Nazim) + the hub's own notifier: cai
was the ONE coordinator WITHOUT a realtime bus-notifier, so it depended entirely
on the slower REACTIVE path (lane_wedge_watchdog: notice idle-on-unread after
~5min, and a repeat-wedge circuit-breaker that gives up after 3 and just pages).
That asymmetry is exactly how a live money-path grant (mig134) sat unread for ~6h
overnight — no realtime poke, reactive path gave up, page went unactioned while
the operator slept (operator 2026-08-02: "why cant cai get the same realtime
nudge mechanism like you and orch?").

This closes it: watch for NEW agent_messages addressed to `cai` and fire cai's
SANCTIONED count-only nudge the moment one lands — so cai drains its bus in
realtime, a first-class peer, not on a 5-minute reactive delay.

Design (mirrors nazim_bus_notify + the count-only nudge doctrine R1/CAI-RESP-377):
  - Injection is DELEGATED to scripts/nudge_cai.sh — the ONLY sanctioned
    programmatic injection into the cai session (count-only, provenance header,
    ghost-aware so it never clobbers cai's own staged next-step, cross-host aware).
    This daemon NEVER send-keys cai directly (that would violate R1).
  - Dedup on the newest row id — nudge only when max(id) over rows->cai advances
    (never re-nudge the same rows; the 2026-07-08 forced-nudge-storm lesson).
  - The substrate stays the source of truth; a dropped keystroke loses nothing
    (the wedge-watchdog remains the reactive backstop). This is signal, not delivery.
  - Debounce: a minimum interval between nudges so a burst of rows is one poke,
    not a storm — nudge_cai.sh is idempotent-ish but we still coalesce.

Requires: DATABASE_URL in the orch .env; scripts/nudge_cai.sh reachable.
"""
import os
import subprocess
import sys
import time

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLL_SEC = 15                 # a touch tighter than Nazim's 20s — cai gates the money-path
MIN_NUDGE_INTERVAL_SEC = 45   # debounce: coalesce bursts into one poke
NUDGE_CAI = os.path.join(ORCH_DIR, "scripts", "nudge_cai.sh")
TARGET = "cai"


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    for line in open(os.path.join(ORCH_DIR, ".env")):
        if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
            return line.split("=", 1)[1].strip()
    raise SystemExit("cai_bus_notify: no DATABASE_URL")


def _log(msg: str) -> None:
    print(f"[cai-bus-notify] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def _max_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(id),0) FROM agent_messages WHERE to_agent=%s", (TARGET,))
        return cur.fetchone()[0]


def _unread_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent=%s AND read_at IS NULL", (TARGET,))
        return cur.fetchone()[0]


def _nudge(unread: int) -> None:
    """Delegate to the sanctioned cai nudge tool (count-only, ghost-aware). It
    applies its own safety checks + cross-host resolution; we never touch the cai
    pane directly (R1). A missing/idle session inside the tool loses nothing —
    the bus is durable and the wedge-watchdog is the reactive backstop."""
    try:
        r = subprocess.run(["bash", NUDGE_CAI, str(unread)],
                           capture_output=True, text=True, timeout=45)
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] if (r.stdout or r.stderr) else []
        _log(f"nudge_cai.sh({unread}) rc={r.returncode} :: {tail}")
    except Exception as e:  # never die on a nudge hiccup
        _log(f"nudge_cai.sh error ({type(e).__name__}): {e}")


def main() -> int:
    import psycopg
    dsn = _dsn()
    # High-water mark = NOW on boot, so we never nudge about pre-existing unread
    # (cai reconciles those on its own; we only signal genuinely NEW arrivals).
    with psycopg.connect(dsn) as conn:
        last_max = _max_id(conn)
    _log(f"up — watching agent_messages->{TARGET} (from id>{last_max}, poll {POLL_SEC}s, via nudge_cai.sh)")
    # None (not 0.0): time.monotonic() is process-relative here (starts near 0), so
    # a 0.0 seed would put the FIRST nudge inside the debounce window and suppress
    # it — and the first message after a quiet spell is exactly the one that matters
    # (a grant request). None => first detection always fires; debounce only
    # coalesces SUBSEQUENT bursts.
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
        except Exception as e:  # never die on a transient DB blip
            _log(f"poll error ({type(e).__name__}): {e}")
        time.sleep(POLL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
