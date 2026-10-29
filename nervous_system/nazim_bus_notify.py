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

# STOPGAP (bus #75459/#75469/#75470, 2026-10-24, with cc-fleet-health's explicit
# go — option 2): suppress the WAKE for the recurring bayanqa/encoder COVERAGE-GAP.
# ORIGINALLY wake-only (row stays unread, surfaces on the next reconcile);
# _autoack_suppressed below (bus #76351) now ALSO marks the row read, but ONLY
# while cc-scholar reads as confirmed-healthy -- see its docstring. If scholar
# itself looks stale/offline, the row is left unread exactly as originally
# designed, so a genuine scholar-down event still reaches the slower generic
# unread-count backstop.
# Ruled out as a Mini-local emitter (canonical file, all 5 stale worktree copies
# overwritten+verified, pg_cron, duplicate launchd, stray --loop, launchctl
# setenv) BEFORE the real root cause landed. Scoped to this EXACT pattern so ANY
# other coverage-gap (different sessions) still wakes me normally.
# ROOT CAUSE FOUND 2026-10-25/26 (bus #76244/#76245): bayanqa/encoder are real,
# intentional tmux sessions for the gzb-hosted 'scholar' lane, not a mystery
# emitter. REVERSIBLE: delete _WAKE_SUPPRESS and the "AND NOT (...)" clauses below
# once the real fix (teach the coverage-gap check scholar's session names) lands
# and is verified quiet; left in place pending that (orch-console's call as the
# stopgap's owner).
# NB: the LIKE patterns are BOUND PARAMETERS, never inlined — psycopg3 parses the
# query text for %-placeholders, so a literal '%' in the SQL string throws
# ProgrammingError ("got '%C'"). Passing them as params is both %-safe and
# injection-safe. (This bit me once: inlining broke every poll silently — the
# daemon kept running but stopped nudging. Verify the log is clean after any edit.)
_WAKE_SUPPRESS_SQL = ("from_agent=%s AND subject ILIKE %s "
                      "AND body ILIKE %s AND body ILIKE %s")
_WAKE_SUPPRESS_PARAMS = ("cc-fleet-health", "%COVERAGE-GAP%", "%bayanqa%", "%encoder%")
# orch-console #76351: the auto-ack below (added after the WAKE-only comment above
# was written, hence the mismatch) marks the suppressed-pattern row READ outright --
# but that EXACT pattern also matches a genuine scholar-lane-DOWN event, which would
# then never surface. Gate the auto-ack on a CONFIRMED-healthy read of cc-scholar;
# any uncertainty fails toward LEAVING the row unread (so the slower generic
# unread-count backstop still catches a real outage) rather than toward masking it.
_SCHOLAR_LIVENESS_GRACE_S = 600  # 10 min


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
        cur.execute("SELECT coalesce(max(id),0) FROM agent_messages WHERE to_agent='orch-console' "
                    f"AND NOT ({_WAKE_SUPPRESS_SQL})", _WAKE_SUPPRESS_PARAMS)
        mx = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent='orch-console' "
                    "AND requires_response=true AND responded_at IS NULL")
        need = cur.fetchone()[0]
    return mx, need


def _unread_count(conn, since_id: int) -> int:
    """LIVE recount of still-UNREAD rows to orch-console with id>since_id, excluding
    the suppressed pattern -- run at SEND TIME (orch-console #83404), not from the
    earlier _state()/_new_priorities() snapshot. Between that snapshot and the
    moment _nudge() actually fires, Nazim can reconcile some of those rows himself
    (his own turn, or the generic '[wake]' backstop) -- a stale snapshot then nudges
    about rows that are already handled. This recount is the final, authoritative
    check right before a keystroke lands."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages WHERE to_agent='orch-console' "
                    "AND id>%s AND read_at IS NULL "
                    f"AND NOT ({_WAKE_SUPPRESS_SQL})",
                    (since_id, *_WAKE_SUPPRESS_PARAMS))
        return cur.fetchone()[0]


def _new_priorities(conn, since_id: int) -> list:
    """Priority of each row to orch-console with id>since_id — feeds the breakdown
    line (#445). A plain count already existed; this is the SAME rows, one extra
    column, no new query shape. Excludes the suppressed coverage-gap pattern, same
    as _state's max(id) query, so a suppressed row never counts toward n_new."""
    with conn.cursor() as cur:
        cur.execute("SELECT priority FROM agent_messages "
                    "WHERE to_agent='orch-console' AND id>%s "
                    f"AND NOT ({_WAKE_SUPPRESS_SQL})",
                    (since_id, *_WAKE_SUPPRESS_PARAMS))
        return [r[0] for r in cur.fetchall()]


def _default_pane_busy(session: str) -> bool:
    from nervous_system.agent_wake import _pane_busy
    return _pane_busy(session)


def _nudge(n_new: int, n_need: int, priorities=(), *, pane_busy=_default_pane_busy,
           recheck_unread=None) -> bool:
    """Send the coalesced count-only nudge. Returns True iff it actually sent (the
    caller only advances its high-water mark on True, so a deferred tick re-checks
    and re-coalesces next poll rather than losing track of what's unread).

    `recheck_unread`, when given, is a zero-arg callable returning the LIVE count of
    still-unread rows right now (orch-console #83404: ~half of one night's wakes
    were for rows Nazim had already handled between the earlier snapshot and this
    send). A zero recount means every row in range is already handled -- treat it
    exactly like any other deferred tick (return False, no keystroke, caller does
    not advance its high-water mark). None preserves the old snapshot-only
    behaviour for callers that don't pass it."""
    if recheck_unread is not None and recheck_unread() == 0:
        _log("recount at send-time: 0 still unread (already handled) — skip")
        return False
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


def _scholar_confirmed_healthy(conn, now=None) -> bool:
    """True ONLY on a confident read that cc-scholar (the gzb-hosted lane whose tmux
    sessions are literally named bayanqa/encoder) is alive and not flagged offline.
    Any uncertainty -- DB error, no matching row, a stale heartbeat, an explicit
    'offline' status -- returns False. _autoack_suppressed treats False as "do not
    mask this row"; the fail direction is always toward SURFACING a possible real
    scholar-down event (orch-console #76351), never toward silently swallowing one."""
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status, last_heartbeat FROM agent_status "
                        "WHERE agent_id='cc-scholar-1' OR base_agent_id='cc-scholar'")
            rows = cur.fetchall()
    except Exception:  # noqa: BLE001 — unreadable -> don't mask
        return False
    if not rows:
        return False
    for status, hb in rows:
        if status == "offline":
            return False
        if hb is None:
            return False
        try:
            if (now - hb).total_seconds() > _SCHOLAR_LIVENESS_GRACE_S:
                return False
        except Exception:  # noqa: BLE001 — un-ageable -> don't mask
            return False
    return True


def _autoack_suppressed(conn, scholar_healthy=_scholar_confirmed_healthy) -> int:
    """Mark the suppressed-pattern rows (the bayanqa/encoder COVERAGE-GAP) read as
    they arrive so they never sit UNREAD. This stops BOTH this daemon's nudge AND the
    separate generic '[wake] new inbox item' backstop (which fires on unread COUNT,
    not on this daemon) from waking the console every ~7min on noise whose emitter
    fleet-health owns the (still-unfound) source hunt for. Scoped to the EXACT
    pattern — a genuinely different coverage-gap stays unread and still wakes me.
    ONLY acts while cc-scholar reads as confirmed-healthy (#76351) -- if scholar
    itself looks stale/offline/unreadable, this is a no-op and the row is LEFT
    unread, so a genuine scholar-down event still reaches the generic backstop.
    Reversible with the rest of the stopgap. Params are bound, never inlined."""
    if not scholar_healthy(conn):
        return 0
    with conn.cursor() as cur:
        cur.execute("UPDATE agent_messages SET read_at=now() "
                    "WHERE to_agent='orch-console' AND read_at IS NULL "
                    f"AND ({_WAKE_SUPPRESS_SQL})", _WAKE_SUPPRESS_PARAMS)
        n = cur.rowcount
    conn.commit()
    return n


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
                acked = _autoack_suppressed(conn)
                if acked:
                    _log(f"auto-acked {acked} suppressed coverage-gap row(s)")
                mx, need = _state(conn)
                if mx > last_max:
                    # Count ACTUAL new rows to orch-console, not the id-delta:
                    # agent_messages.id is a global sequence, so mx-last_max
                    # over-counts by every row addressed to other agents.
                    priorities = _new_priorities(conn, last_max)
                    n_new = len(priorities)
                    # last_max advances ONLY on an actual send (#445 coalescing) --
                    # a deferred (pane-busy, or now a zero-recount) tick keeps
                    # last_max where it was, so the next poll re-counts from the
                    # same point and any further arrivals join the SAME eventual
                    # notifier instead of a new one.
                    _since = last_max
                    if _nudge(n_new, need, priorities,
                              recheck_unread=lambda: _unread_count(conn, _since)):
                        last_max = mx
        except Exception as e:  # never die on a transient DB blip
            _log(f"poll error ({type(e).__name__}): {e}")
        time.sleep(POLL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
