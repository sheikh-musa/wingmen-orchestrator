#!/usr/bin/env python3
"""nazim_bus_notify — realtime poke for the Nazim console on NEW fleet bus rows.

The substrate (agent_messages) is the durable, attributable coordination layer,
but it is async — Nazim polls it on his turn. That latency cost us twice
(2026-07-09: orch posted a requires_response question that sat until Nazim's next
reconcile). This daemon closes the gap: it watches for NEW agent_messages
addressed to orch-console and send-keys a COUNT-ONLY nudge into the `nazim` tmux
session the moment one lands — so Nazim reconciles his fleet inbox in realtime,
not on the next loop tick.

Design (mirrors the sanctioned count-only nudge doctrine, R1/CAI-RESP-357):
  - NEVER carries content — just "N unread on the bus — reconcile agent_messages".
  - Dedup on the newest row id (never re-nudge the same rows — the 2026-07-08
    forced-nudge-storm lesson): nudge only when max(id) advances.
  - The substrate stays the source of truth; a dropped keystroke loses nothing
    (Nazim still reconciles his bus inbox each turn). This is signal, not delivery.

Requires: DATABASE_URL in the orch .env; tmux session `nazim` live on this host.
"""
import os, sys, time, subprocess

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLL_SEC = 20
SESSION = os.environ.get("ORCH_TMUX_SESSION", "nazim")
TMUX = None
for _t in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux",
           os.path.expanduser("~/.local/bin/tmux")):
    if os.path.exists(_t):
        TMUX = _t
        break
TMUX = TMUX or "tmux"


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    for line in open(os.path.join(ORCH_DIR, ".env")):
        if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
            return line.split("=", 1)[1].strip()
    raise SystemExit("nazim_bus_notify: no DATABASE_URL")


def _log(msg: str) -> None:
    print(f"[nazim-bus-notify] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def _state(conn):
    """(max_id over all rows to orch-console, count of unresponded requires_response)."""
    with conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(id),0) FROM agent_messages WHERE to_agent='orch-console'")
        mx = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent='orch-console' "
                    "AND requires_response=true AND responded_at IS NULL")
        need = cur.fetchone()[0]
    return mx, need


def _nudge(n_new: int, n_need: int) -> None:
    # Session must be live; a missing session loses nothing (durable bus).
    if subprocess.run([TMUX, "has-session", "-t", f"={SESSION}"],
                      capture_output=True).returncode != 0:
        _log("no live nazim session — skip (bus is durable)")
        return
    tail = f" ({n_need} need a reply)" if n_need else ""
    line = f"\U0001F4E5 {n_new} new fleet bus msg(s) for you{tail} — reconcile agent_messages (to_agent='orch-console')"
    pane = f"={SESSION}:0.0"
    subprocess.run([TMUX, "send-keys", "-t", pane, "C-u"], capture_output=True)
    time.sleep(0.2)
    subprocess.run([TMUX, "send-keys", "-t", pane, "-l", line], capture_output=True)
    time.sleep(0.2)
    subprocess.run([TMUX, "send-keys", "-t", pane, "Enter"], capture_output=True)
    _log(f"nudged: {line}")


def main() -> int:
    import psycopg
    dsn = _dsn()
    # Initialise the high-water mark to NOW so we never nudge about pre-existing
    # rows on boot — only genuinely new arrivals.
    with psycopg.connect(dsn) as conn:
        last_max, _ = _state(conn)
    _log(f"up — watching agent_messages->orch-console (from id>{last_max}, poll {POLL_SEC}s, session '{SESSION}')")
    while True:
        try:
            with psycopg.connect(dsn) as conn:
                mx, need = _state(conn)
            if mx > last_max:
                _nudge(mx - last_max, need)
                last_max = mx
        except Exception as e:  # never die on a transient DB blip
            _log(f"poll error ({type(e).__name__}): {e}")
        time.sleep(POLL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
