#!/usr/bin/env python3
"""irsyad_shadow_watch.py — wake cc-irsyad the moment a real client message lands.

WHY: the `gazzabyte-irsyad` channel is polled by the HUB's ingest, and that ingest nudges
the hub. cc-irsyad is on the Mini and is deliberately NOT on that channel yet (the hub still
owns the live thread), so without this it would only notice a client message on its next turn
— which makes "is the dedicated agent faster than the hub?" unanswerable and its drafts late.

WHAT IT DOES: polls the durable log for new inbound rows on the client tag and nudges the
`irsyad` tmux session (count-only — never payload, CAI-RESP-357). It sends nothing to the
client and touches no hub state; it is a shadow, running alongside the hub, not instead of it.
Both responders are then timed off the same durable log — see irsyad_latency_report.py.

DEAD-MAN'S SWITCH: a monitor that fails silently is worse than none. Every loop stamps a
heartbeat file; on boot, a heartbeat older than STALE_AFTER means we were dead through a
window and the gap is reported to Nazim on the bus. Any unhandled exception files a P1 bus
row before exiting non-zero so launchd's KeepAlive restart is visible, not silent.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import psycopg

ORCH = pathlib.Path(os.environ.get("ORCH_DIR", pathlib.Path.home() / "wingmen/orchestrator"))
CLIENT_TAG = "gazzabyte-irsyad"
LANE_SESSION = "irsyad"
POLL_SEC = int(os.environ.get("IRSYAD_SHADOW_POLL_SEC", "20"))
STALE_AFTER = timedelta(seconds=POLL_SEC * 15)
STATE = ORCH / "logs/.irsyad_shadow_offset"
HEARTBEAT = ORCH / "logs/.irsyad_shadow_heartbeat"
LOG = ORCH / "logs/irsyad_shadow_watch.log"


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.exit("DATABASE_URL not set")
    return dsn


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def bus(subject: str, body: str, priority: str = "P2") -> None:
    """Tell Nazim. Best-effort: a broken bus must not take the watcher down."""
    try:
        with psycopg.connect(_dsn(), connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, "
                "body, priority) VALUES ('substrate','orch-console','update',%s,%s,%s)",
                (subject, body, priority))
            conn.commit()
    except Exception as exc:                      # noqa: BLE001 — never fatal
        log(f"bus write failed: {exc}")


def read_offset() -> int:
    try:
        return int(STATE.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_offset(value: int) -> None:
    STATE.write_text(str(value))


def check_deadman() -> None:
    """Boot-time gap check — did we miss a window while dead?"""
    try:
        last = datetime.fromisoformat(HEARTBEAT.read_text().strip())
    except (OSError, ValueError):
        log("no prior heartbeat (first boot)")
        return
    gap = datetime.now(timezone.utc) - last
    if gap > STALE_AFTER:
        log(f"DEGRADED: heartbeat gap {gap} — the shadow was dead through that window")
        bus("irsyad shadow-watcher was DOWN — client messages may not have woken cc-irsyad",
            f"The shadow watcher's heartbeat was stale by {gap} (threshold {STALE_AFTER}).\n"
            f"During that window cc-irsyad would NOT have been woken by new client messages "
            f"on '{CLIENT_TAG}'. The hub was unaffected — it polls that channel itself, so no "
            f"client message was missed by the fleet; only cc-irsyad's shadow drafting and the "
            f"latency comparison have a hole. Check logs/irsyad_shadow_watch.log.", "P1")


def new_inbound(conn, since_id: int) -> tuple[int, int]:
    """(count of new inbound client rows, max id)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), coalesce(max(id), %s) FROM operator_messages "
            "WHERE direction='inbound' AND tag=%s AND id > %s",
            (since_id, CLIENT_TAG, since_id))
        return cur.fetchone()


def unhandled_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM operator_messages WHERE direction='inbound' AND tag=%s "
            "AND handled_at IS NULL", (CLIENT_TAG,))
        return cur.fetchone()[0]


def nudge(n_new: int, n_unhandled: int) -> bool:
    """Count-only wake. Carries no message content, ever."""
    line = (f"\U0001F4E5 {n_new} new client message(s) on '{CLIENT_TAG}' "
            f"({n_unhandled} unhandled) — reconcile operator_messages and draft. "
            f"The hub still owns the send.")
    try:
        r = subprocess.run([str(ORCH / "scripts/lane_nudge.sh"), LANE_SESSION, line],
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            log(f"nudge failed rc={r.returncode}: {r.stdout.strip()} {r.stderr.strip()}")
        return r.returncode == 0
    except Exception as exc:                      # noqa: BLE001
        log(f"nudge error: {exc}")
        return False


def main() -> None:
    log(f"shadow watcher up (poll={POLL_SEC}s, tag={CLIENT_TAG}, lane={LANE_SESSION})")
    check_deadman()
    offset = read_offset()
    if offset == 0:
        # First boot: start from NOW, don't replay history into the lane.
        with psycopg.connect(_dsn(), connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute("SELECT coalesce(max(id),0) FROM operator_messages WHERE tag=%s",
                        (CLIENT_TAG,))
            offset = cur.fetchone()[0]
        write_offset(offset)
        log(f"first boot — starting from id {offset}")

    consecutive_failures = 0
    while True:
        try:
            with psycopg.connect(_dsn(), connect_timeout=15) as conn:
                n_new, max_id = new_inbound(conn, offset)
                if n_new:
                    n_unhandled = unhandled_count(conn)
                    log(f"{n_new} new client message(s) (through id {max_id}); nudging lane")
                    if nudge(n_new, n_unhandled):
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures == 3:
                            bus("cc-irsyad is not being woken — 3 consecutive nudge failures",
                                f"The shadow watcher could not wake tmux session "
                                f"'{LANE_SESSION}' three times running (client messages ARE "
                                f"arriving on '{CLIENT_TAG}'). The lane is probably down or "
                                f"wedged. The hub is unaffected. Check `tmux ls` on the Mini.",
                                "P1")
                    offset = max_id
                    write_offset(offset)
            HEARTBEAT.write_text(datetime.now(timezone.utc).isoformat())
        except Exception:                          # noqa: BLE001
            log("FATAL:\n" + traceback.format_exc())
            bus("irsyad shadow-watcher CRASHED (launchd will restart it)",
                "Unhandled exception in irsyad_shadow_watch.py — see "
                "logs/irsyad_shadow_watch.log. cc-irsyad may not be woken by client "
                "messages until it is back. The hub is unaffected.", "P1")
            raise
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
