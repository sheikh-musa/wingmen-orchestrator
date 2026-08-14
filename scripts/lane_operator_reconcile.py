#!/usr/bin/env python3
"""lane_operator_reconcile.py — a DEDICATED LANE AGENT's tag-scoped operator_log
reconcile (part-2 of #21399: cc-irsyad-coord owning gazzabyte-irsyad inbound).

Mirrors nervous_system/operator_log.py's unprocessed()/mark_handled_through() but
scopes by an EXPLICIT tag instead of the hub/console body-role scope — so a lane
agent (coord) reads + stamps ONLY its own client tag and can never eat another
surface's rows. FAIL-CLOSED: an empty/whitespace tag raises (never an unscoped
span — the 2026-07-05 cross-body-loss lesson, operator_log.py comment). Reuses
operator_log's row-shape helpers for a consistent reader experience.

Coord's per-turn loop:
    read  --tag gazzabyte-irsyad          # see Shuk's unhandled inbound (oldest-first)
    (answer via scripts/lane_reply.sh)
    handle --tag gazzabyte-irsyad --through <max_id>   # stamp handled after answering

This is the reconcile GUARANTEE (Option B): delivery is independent of the
keystroke nudge landing. At-least-once — a rare re-surface beats a silent loss.
"""
import argparse
import os
import sys

import psycopg

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from nervous_system import operator_log  # reuse _sender_label/_source_hint/_triage_for row shape


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("no DATABASE_URL/SUPABASE_DB_URL")
    return dsn


def _require_tag(tag: str) -> str:
    """FAIL-CLOSED: a lane reconcile MUST be tag-scoped. Never span all tags."""
    t = (tag or "").strip()
    if not t:
        raise SystemExit(
            "refusing an UNSCOPED lane reconcile — --tag is required and must be "
            "non-empty (an unscoped read/stamp would eat other surfaces' rows; see "
            "operator_log.py's 2026-07-05 cross-body-loss lesson)."
        )
    return t


def unprocessed(tag: str, limit: int = 20) -> list:
    """Inbound operator_messages for THIS tag not yet handled, oldest-first.
    Same return shape as operator_log.unprocessed()."""
    tag = _require_tag(tag)
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, tag, text, created_at, chat_id, "
            "from_user_id, from_username, from_name, cos_triage FROM operator_messages "
            "WHERE direction='inbound' AND handled_at IS NULL AND tag=%s "
            "ORDER BY id ASC LIMIT %s",
            (tag, limit),
        )
        rows = cur.fetchall()
    return [
        (rid, rtag, text, created_at,
         operator_log._sender_label(fuid, fname, funame),
         operator_log._source_hint(chat_id, fuid),
         operator_log._triage_for(cos_triage, text, rtag))
        for (rid, rtag, text, created_at, chat_id, fuid, funame, fname, cos_triage) in rows
    ]


def mark_handled_through(max_id: int, tag: str) -> int:
    """Stamp every inbound for THIS tag up to and including max_id as handled.
    Tag-scoped — cannot stamp another surface's rows. Returns rows stamped."""
    tag = _require_tag(tag)
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        # identity attribution (the lane agent), consistent with operator_log stamps
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)",
                    (os.environ.get("ORCH_AGENT_ID", "cc-irsyad-coord"),))
        cur.execute(
            "UPDATE operator_messages SET handled_at=now() "
            "WHERE direction='inbound' AND handled_at IS NULL AND tag=%s AND id <= %s",
            (tag, max_id),
        )
        n = cur.rowcount
        conn.commit()
        return n


def main() -> int:
    ap = argparse.ArgumentParser(description="lane-agent tag-scoped operator_log reconcile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="show unhandled inbound for --tag (oldest-first)")
    r.add_argument("--tag", required=True)
    r.add_argument("--limit", type=int, default=20)
    h = sub.add_parser("handle", help="stamp inbound for --tag handled through --through")
    h.add_argument("--tag", required=True)
    h.add_argument("--through", type=int, required=True)
    a = ap.parse_args()

    if a.cmd == "read":
        rows = unprocessed(a.tag, a.limit)
        if not rows:
            print(f"(no unhandled inbound on tag={a.tag!r})")
            return 0
        print(f"{len(rows)} unhandled on tag={a.tag!r} (oldest-first):")
        for (rid, tag, text, created, sender, source, triage) in rows:
            print(f"  #{rid} [{created:%Y-%m-%d %H:%M}Z] {sender} ({source}): {text[:200]}")
        print(f"→ after answering, run: handle --tag {a.tag} --through {rows[-1][0]}")
        return 0
    if a.cmd == "handle":
        n = mark_handled_through(a.through, a.tag)
        print(f"stamped {n} row(s) handled on tag={a.tag!r} through #{a.through}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
