#!/usr/bin/env python3
"""lane_operator_reconcile.py — a DEDICATED LANE AGENT's tag-scoped operator_log
reconcile (part-2 of #21399: cc-irsyad-coord owning gazzabyte-irsyad inbound).

Mirrors nervous_system/operator_log.py's unprocessed()/mark_handled() but
scopes by an EXPLICIT tag instead of the hub/console body-role scope — so a lane
agent (coord) reads + stamps ONLY its own client tag and can never eat another
surface's rows. FAIL-CLOSED: an empty/whitespace tag raises (never an unscoped
span — the 2026-07-05 cross-body-loss lesson, operator_log.py comment). Reuses
operator_log's row-shape helpers for a consistent reader experience.

EXPLICIT IDS ONLY (Fable audit 2026-09-30 B-2, op#23531): `handle --through N` was
a HIGH-WATER stamp — a coord that answered only the newest message and stamped
"through" its id silently marked every OLDER unanswered message handled (the
stamp-past-unanswered incident). It is now REFUSED; a reply names the ids it
actually answered.

Coord's per-turn loop:
    read  --tag gazzabyte-irsyad          # see Shuk's unhandled inbound (oldest-first)
    (answer via scripts/lane_reply.sh)
    handle --tag gazzabyte-irsyad --ids <id,id>   # stamp EXACTLY the ids you answered

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


def _agent_identity() -> str:
    """This lane's OWN identity for the audit GUC — never a hub default."""
    return (os.environ.get("CC_BASE_AGENT_ID") or os.environ.get("AGENT_ID")
            or os.environ.get("ORCH_AGENT_ID") or "unknown-lane")


def mark_handled(ids, tag: str) -> int:
    """Stamp EXACTLY the given inbound ids as handled, within THIS tag (an id from
    another tag is not stamped even if passed). Returns rows stamped; [] -> 0."""
    tag = _require_tag(tag)
    id_list = sorted({int(i) for i in (ids or []) if i is not None})
    if not id_list:
        return 0
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id',%s,true)", (_agent_identity(),))
        cur.execute(
            "UPDATE operator_messages SET handled_at=now() "
            "WHERE direction='inbound' AND handled_at IS NULL AND tag=%s AND id = ANY(%s)",
            (tag, id_list),
        )
        n = cur.rowcount
        conn.commit()
        return n


def _unhandled_ids_through(max_id: int, tag: str) -> list:
    tag = _require_tag(tag)
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM operator_messages WHERE direction='inbound' "
                    "AND handled_at IS NULL AND tag=%s AND id <= %s ORDER BY id",
                    (tag, max_id))
        return [r[0] for r in cur.fetchall()]


class HighWaterRefused(SystemExit):
    """Exit 2 with the ids the refused --through would have eaten (message on .why)."""
    def __init__(self, why: str):
        super().__init__(2)
        self.why = why


def mark_handled_through(max_id: int, tag: str) -> int:
    """REFUSED (Fable audit 2026-09-30 B-2): a high-water stamp marks every older
    unanswered row handled. Prints what it would have eaten to stderr and exits 2 —
    re-run with mark_handled(ids, tag) / `handle --ids` naming the ids you answered."""
    would = _unhandled_ids_through(max_id, tag)
    why = (
        f"refusing `--through {max_id}` on tag={tag!r}: a high-water stamp silently marks "
        f"every older unanswered message handled (stamp-past-unanswered, Fable audit "
        f"2026-09-30). Unhandled ids <= {max_id}: {would}. Answer each (or defer it), then "
        f"stamp EXACTLY what you answered: handle --tag {tag} --ids "
        f"{','.join(map(str, would)) or '<id,id>'}"
    )
    print(why, file=sys.stderr, flush=True)
    raise HighWaterRefused(why)


def main() -> int:
    ap = argparse.ArgumentParser(description="lane-agent tag-scoped operator_log reconcile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="show unhandled inbound for --tag (oldest-first)")
    r.add_argument("--tag", required=True)
    r.add_argument("--limit", type=int, default=20)
    h = sub.add_parser("handle", help="stamp EXACTLY the given inbound ids for --tag handled")
    h.add_argument("--tag", required=True)
    g = h.add_mutually_exclusive_group(required=True)
    g.add_argument("--ids", help="comma-separated inbound ids you actually answered")
    g.add_argument("--through", type=int,
                   help="REFUSED (high-water stamp = silent loss); kept only to print the ids it would eat")
    a = ap.parse_args()

    if a.cmd == "read":
        rows = unprocessed(a.tag, a.limit)
        if not rows:
            print(f"(no unhandled inbound on tag={a.tag!r})")
            return 0
        print(f"{len(rows)} unhandled on tag={a.tag!r} (oldest-first):")
        for (rid, tag, text, created, sender, source, triage) in rows:
            print(f"  #{rid} [{created:%Y-%m-%d %H:%M}Z] {sender} ({source}): {text[:200]}")
        print(f"→ answer EACH, then stamp exactly those: handle --tag {a.tag} "
              f"--ids {','.join(str(r[0]) for r in rows)}")
        return 0
    if a.cmd == "handle":
        if a.through is not None:
            mark_handled_through(a.through, a.tag)   # always raises SystemExit(2-text)
        ids = [int(x) for x in a.ids.split(",") if x.strip()]
        n = mark_handled(ids, a.tag)
        print(f"stamped {n} row(s) handled on tag={a.tag!r} ids={ids}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
