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
  - NUDGE COALESCING (held_commitments #445, audit-20261006 item 5, bus#54648): a
    nudge DEFERS (never sends) while the target pane is pane_working — a busy
    "esc to interrupt" footer means the console is mid-turn, and `C-u` + send-keys
    into THAT pane would wipe a live, in-progress draft, not just an idle prompt.
    A deferred tick never advances last_max, so the NEXT poll recomputes the
    unread count + priority breakdown FRESH and retries — rows that keep arriving
    while the console stays busy coalesce into ONE eventual notifier, never one
    per arrival. The breakdown line ("P1:1 P3:3", scripts/lib/priority_breakdown)
    tells Nazim at a glance whether the backlog is urgent before he even looks.

Requires: DATABASE_URL in the orch .env; tmux session `nazim` live on this host.
"""
import os, sys, time, subprocess

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ORCH_DIR)
from scripts.lib import fire_window  # noqa: E402  (quiesce during a recycle's fire window)
from scripts.lib.priority_breakdown import breakdown_line  # noqa: E402
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
    # op#24342: file-first. A stale inherited DATABASE_URL (a pre-rotation password
    # held by this long-lived poller) must NOT win — that is what hammers the pooler
    # to ECIRCUITBREAKER after a rotation. The .env FILE is the rotation push-point.
    from scripts.lib.substrate_dsn import dsn_from_env_file
    return dsn_from_env_file(os.path.join(ORCH_DIR, ".env"))


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


def _new_priorities(conn, since_id: int) -> list:
    """Priority of each row to orch-console with id>since_id — feeds the breakdown
    line (#445). A plain count already existed; this is the SAME rows, one extra
    column, no new query shape."""
    with conn.cursor() as cur:
        cur.execute("SELECT priority FROM agent_messages "
                    "WHERE to_agent='orch-console' AND id>%s", (since_id,))
        return [r[0] for r in cur.fetchall()]


def _default_pane_busy(session: str) -> bool:
    from nervous_system.agent_wake import _pane_busy
    return _pane_busy(session)


def _nudge(n_new: int, n_need: int, priorities=(), *, pane_busy=_default_pane_busy) -> bool:
    """Send the coalesced count-only nudge. Returns True iff it actually sent (the
    caller only advances its high-water mark on True, so a deferred tick re-checks
    and re-coalesces next poll rather than losing track of what's unread)."""
    # Session must be live; a missing session loses nothing (durable bus).
    if subprocess.run([TMUX, "has-session", "-t", f"={SESSION}"],
                      capture_output=True).returncode != 0:
        _log("no live nazim session — skip (bus is durable)")
        return False
    # A recycle owns this pane for a few seconds; a keystroke landing inside that
    # window jams the /clear and the body returns half-initialised. Skipping costs
    # nothing — the bus row is durable and the fresh body reconciles it at boot.
    if fire_window.is_held(SESSION):
        _log(f"fire window held on '{SESSION}' ({fire_window.held_reason(SESSION)}) — skip")
        return False
    # NUDGE COALESCING (#445): mid-turn ('esc to interrupt') means `C-u` would wipe
    # a live draft, not an idle prompt. DEFER -- never send-keys into a busy pane.
    # The caller does NOT advance last_max on a deferred tick, so newly-arrived
    # rows accumulate into one eventual notifier instead of one per poll.
    if pane_busy(SESSION):
        _log(f"'{SESSION}' pane busy (mid-turn) — deferring nudge, will retry next poll")
        return False
    tail = f" ({n_need} need a reply)" if n_need else ""
    bd = breakdown_line(priorities) if priorities else ""
    bd_part = f" [{bd}]" if bd else ""
    line = (f"\U0001F4E5 {n_new} new fleet bus msg(s) for you{bd_part}{tail} — "
            f"reconcile agent_messages (to_agent='orch-console')")
    pane = f"={SESSION}:0.0"
    subprocess.run([TMUX, "send-keys", "-t", pane, "C-u"], capture_output=True)
    time.sleep(0.2)
    subprocess.run([TMUX, "send-keys", "-t", pane, "-l", line], capture_output=True)
    time.sleep(0.2)
    subprocess.run([TMUX, "send-keys", "-t", pane, "Enter"], capture_output=True)
    _log(f"nudged: {line}")
    return True


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
                    # Count ACTUAL new rows to orch-console, not the id-delta:
                    # agent_messages.id is a global sequence, so mx-last_max
                    # over-counts by every row addressed to other agents.
                    priorities = _new_priorities(conn, last_max)
                    n_new = len(priorities)
                    # last_max advances ONLY on an actual send (#445 coalescing) --
                    # a deferred (pane-busy) tick keeps last_max where it was, so the
                    # next poll re-counts from the same point and any further
                    # arrivals join the SAME eventual notifier instead of a new one.
                    if _nudge(n_new, need, priorities):
                        last_max = mx
        except Exception as e:  # never die on a transient DB blip
            _log(f"poll error ({type(e).__name__}): {e}")
        time.sleep(POLL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
