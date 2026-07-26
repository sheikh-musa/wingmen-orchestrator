#!/usr/bin/env python3
"""hub_heartbeat.py — write cc-orchestrator's agent_status liveness row.

WHY THIS EXISTS, AND WHY IT IS NOT A CRON
=========================================
The hub had NO agent_status row at all (found 2026-07-26, orch-console #11611):
seven bodies present, the hub absent. Every liveness check keyed on that table
returned "nothing to report" rather than "unknown" — which reads identical to
healthy. Absence is invisible; that is strictly worse than stale.

The obvious fix is a launchd timer that writes "hub is alive" every minute. That
would be WRONG, and wrong in tonight's signature way: it would report the
TIMER's liveness, not the hub's. A frozen or dead hub would keep publishing
green forever and every check downstream would be satisfied. That is a check
reporting on something narrower than the claim it supports — the exact defect
class this fleet spent 2026-07-25/26 cataloguing (six instances by 02:15Z).

So this script is invoked from a Claude Code hook fired by the hub's OWN tool
activity. The heartbeat is therefore a BYPRODUCT OF WORKING, not a statement
about working. If the hub stops, nothing calls this, and the row goes stale on
its own — which is the correct and detectable failure.

    Timer-driven heartbeat : dead hub -> reports GREEN      (false negative)
    Activity-driven        : dead hub -> goes STALE         (detectable)

DEBOUNCE: hooks can fire many times per turn. We write at most once per
HEARTBEAT_INTERVAL_S using a stamp file, so the DB cost is bounded regardless of
tool-call volume.

FAIL-OPEN, ALWAYS: this must never break the hub's turn. Every failure path
exits 0 silently. A heartbeat that can wedge the body it measures is worse than
no heartbeat.
"""
from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

AGENT_ID = "cc-orchestrator"
HEARTBEAT_INTERVAL_S = 60
ORCH_DIR = Path("/Users/Musa/wingmen/orchestrator")
STAMP = Path("/tmp/.wingmen_hub_heartbeat_stamp")


def _should_write() -> bool:
    """True if the debounce window has elapsed. Missing/garbled stamp -> write."""
    try:
        return (time.time() - STAMP.stat().st_mtime) >= HEARTBEAT_INTERVAL_S
    except (OSError, ValueError):
        return True


def _touch_stamp() -> None:
    try:
        STAMP.touch()
    except OSError:
        pass  # debounce degrades to every-call; still correct, just chattier


def main() -> int:
    if not _should_write():
        return 0

    try:
        import psycopg  # noqa: PLC0415 - deliberately lazy; keeps the no-op path cheap
        from dotenv import load_dotenv  # noqa: PLC0415

        # load_dotenv() with no argument raises under some hook invocations
        # (no caller frame), so the path is always explicit here.
        load_dotenv(str(ORCH_DIR / ".env"))
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            return 0

        # Only the lease-holding hub body may claim this row. A console body
        # (ORCH_BODY_ROLE=console / Nazim) must never publish liveness as the
        # hub — that would be the pen-(iv) class of impersonation in the
        # liveness table. Fail closed on anything that is not an explicit hub.
        if (os.environ.get("ORCH_BODY_ROLE") or "hub").strip().lower() != "hub":
            return 0

        host = socket.gethostname().split(".")[0]
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # agent_status enforces attributable writes via this GUC.
                # NOTE: `SET LOCAL app.x = %s` is a SYNTAX ERROR — SET does not
                # accept bound parameters. set_config(..., is_local=true) is the
                # parameterised equivalent and is what the canonical writer in
                # scripts/lib/auto_agent_id.py uses. Getting this wrong once
                # already produced a silent no-op that looked like success.
                cur.execute(
                    "SELECT set_config('app.current_agent_id', %s, true)",
                    (AGENT_ID,),
                )
                cur.execute(
                    """
                    INSERT INTO agent_status
                      (agent_id, base_agent_id, status, current_task,
                       scope_repos, tmux_session, host, last_heartbeat, updated_at)
                    VALUES (%s, %s, 'working', 'hub: active (activity-driven heartbeat)',
                            ARRAY['*']::text[], %s, %s, now(), now())
                    ON CONFLICT (agent_id) DO UPDATE SET
                      status = 'working',
                      tmux_session = EXCLUDED.tmux_session,
                      host = EXCLUDED.host,
                      last_heartbeat = now(),
                      updated_at = now()
                    """,
                    (AGENT_ID, AGENT_ID, os.environ.get("ORCH_TMUX_SESSION", "orch"), host),
                )
            conn.commit()
        _touch_stamp()
    except Exception as exc:
        # Deliberately silent in the hook path. See FAIL-OPEN above.
        #
        # BUT silence is how a broken heartbeat stays broken: the first cut of
        # this script used `SET LOCAL ... = %s`, which is a syntax error, and
        # the bare `except` turned it into a clean exit 0 that was
        # indistinguishable from a successful write. Set WINGMEN_HEARTBEAT_DEBUG=1
        # to make failures visible when testing. Never set it in the hook.
        if os.environ.get("WINGMEN_HEARTBEAT_DEBUG"):
            print(f"hub_heartbeat FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
