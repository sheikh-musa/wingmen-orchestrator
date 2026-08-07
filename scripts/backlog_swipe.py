#!/usr/bin/env python3
"""backlog_swipe.py — apply an operator swipe to one operator_backlog row.

The fleet console's DB session is read-only by construction, so the swipe POST
handler (nervous_system/console/app.py, /api/backlog) shells out to THIS script
to perform the write — the same vetted-script pattern /api/reset uses. Runs in
the orchestrator env (writable DATABASE_URL).

Usage:  backlog_swipe.py <id> <drop|prioritise>

Actions (op 2026-08-02, operator swipe on the "Your asks" cards):
  drop        swipe-left  — status='dropped' (leaves the live plate + Nazim's
                            working memory of it; row kept in-table for history).
  prioritise  swipe-right — sort_order below the current live minimum, floating
                            the ask to the ABSOLUTE top of the worklist.

Exit 0 + prints 'ok' on exactly one row updated; exit 2 + 'error: ...' otherwise
(unknown id / bad action / no DSN), so the console can surface a clean failure.
"""
from __future__ import annotations

import os
import subprocess
import sys

import psycopg
from dotenv import load_dotenv


def _nudge_nazim(action: str) -> None:
    """Push the swipe to Nazim (the console body) so his curation reaches him
    PROMPTLY, not only on his next turn (operator 2026-08-02: "how does that info
    reach you?"). Count-only line via send-keys — same mechanism + CAI-RESP-357
    shape as the operator-message ingest nudge, and Nazim's channel is already
    nudge_when_busy=True. Best-effort: the durable truth is the operator_backlog
    table Nazim reconciles every turn, so a missed nudge loses nothing."""
    session = os.environ.get("ORCH_TMUX_SESSION", "nazim")
    tmux = next((p for p in ("/usr/local/bin/tmux", "/opt/homebrew/bin/tmux", "/usr/bin/tmux")
                 if os.path.isfile(p) and os.access(p, os.X_OK)), None)
    if not tmux:
        return
    tgt = f"={session}:0.0"
    line = f"\U0001F5C2 operator swiped ({action}) — reconcile operator_backlog for the new order"
    try:
        if subprocess.run([tmux, "has-session", "-t", tgt],
                          capture_output=True, timeout=5).returncode != 0:
            return
        subprocess.run([tmux, "send-keys", "-t", tgt, "C-u"], timeout=5)          # clear any draft
        subprocess.run([tmux, "send-keys", "-t", tgt, "-l", line], timeout=5)     # literal line, no payload
        subprocess.run([tmux, "send-keys", "-t", tgt, "Enter"], timeout=5)
    except Exception:
        pass


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: backlog_swipe.py <id> <drop|prioritise>\n")
        return 2
    raw_id, action = sys.argv[1], sys.argv[2].strip().lower()
    try:
        item_id = int(raw_id)
    except ValueError:
        sys.stderr.write(f"error: id must be an integer, got {raw_id!r}\n")
        return 2
    if action not in ("drop", "prioritise", "prioritize"):
        sys.stderr.write(f"error: unknown action {action!r}\n")
        return 2

    load_dotenv()
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.stderr.write("error: no DATABASE_URL\n")
        return 2

    if action == "drop":
        sql = "UPDATE operator_backlog SET status='dropped', updated_at=now() WHERE id=%s"
        params = [item_id]
    else:  # prioritise
        sql = (
            "UPDATE operator_backlog SET "
            "  sort_order = (SELECT COALESCE(MIN(sort_order), 0) - 1 "
            "                FROM operator_backlog WHERE status NOT IN ('done','dropped')), "
            "  updated_at = now() "
            "WHERE id = %s"
        )
        params = [item_id]

    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                n = cur.rowcount
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"error: {type(e).__name__}: {e}\n")
        return 2

    if n == 1:
        _nudge_nazim("drop" if action == "drop" else "prioritise")
        print("ok")
        return 0
    sys.stderr.write(f"error: {n} rows updated (id {item_id} not found?)\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
