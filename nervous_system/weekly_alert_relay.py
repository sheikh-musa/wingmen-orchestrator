#!/usr/bin/env python3
"""weekly_alert_relay.py — relay the SRE's operator-addressed weekly-limit
early-warnings to the operator's phone via NAZIM'S pen (@nazim_cto_bot).

OWNERSHIP: this is orch-console's (Nazim's) daemon. DRAFTED by cc-fleet-health
(the SRE) at Nazim's request (#15059), REVIEWED + RUN by Nazim as HIS launchd job.
It delivers via scripts/nazim_send.sh — Nazim's OWN identity-separated operator
voice (distinct bot token + channel), which structurally does NOT touch the hub's
pen iv (@wingmennorchbot). So running it is entirely within Nazim's pen; the SRE
only drafted the code and never runs it (staying inside its own pen boundary).

THE PIPE (#14988):
  weekly_limit_monitor.py (SRE, cc-fleet-health) emits, at 75/90, a bus row
    to_agent='musa', from_agent='cc-fleet-health', subject LIKE '⚠️%'  (durable,
    attributable — the operator-addressed early-warning of record).
  THIS relay (Nazim) watches for those rows and pushes each one to the operator's
    phone via nazim_send.sh — so the operator gets the warning DIRECTLY, not on a
    latency hop through Nazim reading his console inbox.

AT-LEAST-ONCE + NO RE-FLOOD + NO GAP (Nazim's spec):
  * A durable cursor (STATE_FILE) holds the id of the last SUCCESSFULLY relayed
    row. Each pass relays matching rows with id > cursor, in ascending id order.
  * The cursor advances ONLY after nazim_send.sh returns success, and is persisted
    after EACH send — so a crash mid-batch re-sends at most the one in flight
    (at-least-once), never the whole backlog.
  * FIRST RUN (no state file): the cursor is baselined to the current max matching
    id WITHOUT sending — so historical ⚠️ rows are not flooded on boot (mirrors
    nazim_bus_notify's "initialise to NOW"). Delete the state file to re-baseline;
    use --backfill-from <id> to deliberately replay from an older id.

DEAD-MAN'S-SWITCH parity (a silent-fail relay is worse than none):
  * A send failure is logged LOUD (log file + stderr) and does NOT advance the
    cursor — the row is retried on the next pass (so a down pen queues warnings in
    order and flushes them on recovery, rather than dropping them). Because a send
    failure means Nazim's operator voice itself is down, the relay does NOT try to
    "page about the failure" over that same dead channel — it fails loud in the log
    and via a stale heartbeat instead.
  * Every pass writes HEARTBEAT_FILE so Nazim's launchd / a watchdog can detect a
    wedged/dead relay (parity with weekly_limit_monitor's heartbeat).

Usage:
  python3 nervous_system/weekly_alert_relay.py           # resident poll loop
  python3 nervous_system/weekly_alert_relay.py --once     # single pass (StartInterval launchd)
  python3 nervous_system/weekly_alert_relay.py --dry-run  # log intended sends, do not send/advance
  python3 nervous_system/weekly_alert_relay.py --backfill-from 15000  # replay from an id (one-off)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_ORCH = Path(__file__).resolve().parent.parent
NAZIM_SEND = _ORCH / "scripts" / "nazim_send.sh"
STATE_FILE = _ORCH / "logs" / "weekly_alert_relay_state.json"
HEARTBEAT_FILE = _ORCH / "logs" / "weekly_alert_relay_heartbeat"
LOG_FILE = _ORCH / "logs" / "weekly_alert_relay.log"

POLL_SEC = int(os.environ.get("WEEKLY_RELAY_POLL_SEC", "30"))
SEND_TIMEOUT = int(os.environ.get("WEEKLY_RELAY_SEND_TIMEOUT", "90"))

# The rows this relay is responsible for. Kept narrow on purpose: ONLY the SRE's
# operator-addressed weekly-limit warnings — never any other 'musa' row. The LIKE
# pattern is a BOUND PARAM (_LIKE), not an inline literal: a literal '⚠️%' inside a
# parameterized query makes psycopg read the '%' as a placeholder and blow up.
_WHERE = ("to_agent = 'musa' AND from_agent = 'cc-fleet-health' "
          "AND subject LIKE %s")
_LIKE = "⚠️%"


def _log(msg: str) -> None:
    line = f"[weekly-alert-relay] {time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    for line in open(_ORCH / ".env"):
        if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
            return line.split("=", 1)[1].strip()
    raise SystemExit("weekly_alert_relay: no DATABASE_URL")


def _load_cursor() -> "int | None":
    try:
        return int(json.loads(STATE_FILE.read_text())["cursor"])
    except Exception:
        return None


def _save_cursor(cursor: int) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"cursor": cursor}, indent=2))


def _max_matching_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT coalesce(max(id),0) FROM agent_messages WHERE {_WHERE}", (_LIKE,))
        return cur.fetchone()[0]


def _send(body: str, dry: bool) -> bool:
    """Push one warning to the operator via Nazim's pen. Returns True on success.
    nazim_send.sh reads token+chat from .env and redacts secrets itself."""
    if dry:
        _log(f"[DRY] would nazim_send: {body.splitlines()[0] if body else ''}")
        return True
    try:
        r = subprocess.run([str(NAZIM_SEND), body], capture_output=True,
                           timeout=SEND_TIMEOUT, cwd=str(_ORCH))
        if r.returncode == 0:
            return True
        _log(f"nazim_send FAILED rc={r.returncode}: {r.stderr.decode(errors='replace')[:300]}")
        return False
    except Exception as e:
        _log(f"nazim_send EXCEPTION: {type(e).__name__}: {e}")
        return False


def relay_once(dry: bool = False) -> int:
    """One pass: relay every matching row with id > cursor, in order, stopping at
    the first send failure (retried next pass). Returns the number sent."""
    import psycopg
    dsn = _dsn()
    sent = 0
    with psycopg.connect(dsn, connect_timeout=15) as conn:
        cursor = _load_cursor()
        # FIRST RUN: baseline to current max WITHOUT sending (no backlog flood).
        if cursor is None:
            base = _max_matching_id(conn)
            _save_cursor(base)
            _log(f"first run — baselined cursor to id {base} (no backlog replayed)")
            _touch_heartbeat()
            return 0
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, body FROM agent_messages WHERE {_WHERE} AND id > %s "
                "ORDER BY id ASC", (_LIKE, cursor))
            rows = cur.fetchall()
        for row_id, body in rows:
            if _send(body or "", dry):
                sent += 1
                if not dry:
                    _save_cursor(row_id)   # advance per-row: at-least-once, no re-flood
                _log(f"relayed id {row_id} -> operator (nazim pen)")
            else:
                # LOUD: pen down. Do NOT advance — retry this row (and the ones
                # behind it, in order) on the next pass. No silent drop, no gap.
                _log(f"HELD id {row_id} — send failed; will retry next pass "
                     f"(operator NOT warned for this row yet)")
                break
    _touch_heartbeat()
    return sent


def _touch_heartbeat() -> None:
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(str(time.time()))
    except Exception as e:
        _log(f"heartbeat-write-failed: {e}")


def main() -> int:
    dry = "--dry-run" in sys.argv
    once = "--once" in sys.argv
    # One-off deliberate replay from an older id (sets the cursor BELOW it).
    if "--backfill-from" in sys.argv:
        i = sys.argv.index("--backfill-from")
        _save_cursor(int(sys.argv[i + 1]) - 1)
        _log(f"backfill: cursor set to {int(sys.argv[i + 1]) - 1}")
    if once:
        n = relay_once(dry=dry)
        _log(f"single pass done — {n} relayed")
        return 0
    _log(f"up — watching agent_messages ({_WHERE % repr(_LIKE)}), poll {POLL_SEC}s, dry={dry}")
    while True:
        try:
            relay_once(dry=dry)
        except Exception as e:  # never die on a transient DB/subprocess blip
            _log(f"pass error ({type(e).__name__}): {e}")
        time.sleep(POLL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
