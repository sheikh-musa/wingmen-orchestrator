#!/usr/bin/env python3
"""mamadah_reconcile.py — cc-mamadah's own tag-scoped operator_log reconcile
(bus #47837 C1-C4), for the ONE private-family tag whose real content is NOT
in the substrate's operator_messages.text (it's a sentinel there — see
nervous_system/personal_routing.py). Deliberately a SEPARATE script from
scripts/lane_operator_reconcile.py, not a generalization of it: every other
_LANE_OWNED_TAGS lane (hk-editor, gazzabyte-irsyad) reads its real text
straight off the substrate row, so that shared tool's contract stays
untouched. This one additionally joins wingmen-personal's `mamadah_messages`
by substrate_message_id to resolve the real text.

cc-mamadah's per-turn loop:
    read  --tag mamadah             # unhandled inbound, oldest-first, REAL text
    (answer via scripts/mamadah_send.sh)
    handle --tag mamadah --ids <id,id>   # stamp EXACTLY the ids you answered

Requires the GUARDED wingmen-personal credential to already be in this
process's environment (source ~/.wingmen/private/wingmen_personal.env with
WINGMEN_PERSONAL_ALLOWED=1 first) — refuses otherwise, same fail-closed shape
as a missing DATABASE_URL.
"""
import argparse
import os
import sys

import psycopg

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from nervous_system import operator_log  # reuse _source_hint row shape
from nervous_system import personal_routing

_TAG = "mamadah"

# "Always know who's speaking" (bus #47892, orch-console ruling #47912 point 2):
# identity no longer lands in the substrate row for this tag (PR #240 HIGH fix
# — stored_from_* is NULL there by design), so resolve it from the
# wingmen-personal row's real from_user_id instead. Fixed two-person mapping
# per orch-console's explicit ruling, not derived from any env var — this
# channel has exactly two known humans.
_MUSA_ID = "286619815"
_ZAHIDAH_ID = "6606903261"


def _mamadah_sender_label(from_user_id) -> str:
    uid = str(from_user_id) if from_user_id is not None else ""
    if uid == _MUSA_ID:
        return "Musa"
    if uid == _ZAHIDAH_ID:
        return "Zahidah"
    return "unknown"


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("no DATABASE_URL/SUPABASE_DB_URL")
    return dsn


def _require_mamadah_tag(tag: str) -> str:
    t = (tag or "").strip()
    if t != _TAG:
        raise SystemExit(
            f"mamadah_reconcile.py is hard-scoped to tag={_TAG!r} only (got {t!r}) — "
            f"every other _LANE_OWNED_TAGS lane uses scripts/lane_operator_reconcile.py."
        )
    return t


def unprocessed(tag: str, limit: int = 20) -> list:
    """Inbound envelope rows for mamadah not yet handled, oldest-first, with
    the REAL text resolved from wingmen-personal (substrate's own `text`
    column holds only the sentinel)."""
    tag = _require_mamadah_tag(tag)
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, tag, created_at, chat_id FROM operator_messages "
            "WHERE direction='inbound' AND handled_at IS NULL AND tag=%s "
            "ORDER BY id ASC LIMIT %s",
            (tag, limit),
        )
        rows = cur.fetchall()
    ids = [r[0] for r in rows]
    personal = personal_routing.read_personal_content(ids)
    out = []
    for (rid, rtag, created_at, chat_id) in rows:
        prow = personal.get(rid)
        text = prow["text"] if prow else "[[wingmen-personal row missing — DATA LOSS, escalate]]"
        # Identity is sentinel-NULL on the substrate row (fuid/fname/funame
        # above) by design for this tag — resolve from wingmen-personal's
        # real from_user_id instead, never from the substrate columns.
        personal_fuid = prow.get("from_user_id") if prow else None
        out.append((
            rid, rtag, text, created_at,
            _mamadah_sender_label(personal_fuid),
            operator_log._source_hint(chat_id, personal_fuid),
        ))
    return out


def _agent_identity() -> str:
    return (os.environ.get("CC_BASE_AGENT_ID") or os.environ.get("AGENT_ID")
            or os.environ.get("ORCH_AGENT_ID") or "cc-mamadah")


def mark_handled(ids, tag: str) -> int:
    """Stamp EXACTLY the given inbound ids as handled, within tag=mamadah only."""
    tag = _require_mamadah_tag(tag)
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


def main() -> int:
    ap = argparse.ArgumentParser(description="cc-mamadah's tag-scoped operator_log reconcile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="show unhandled inbound for --tag mamadah (oldest-first, real text)")
    r.add_argument("--tag", required=True)
    r.add_argument("--limit", type=int, default=20)
    h = sub.add_parser("handle", help="stamp EXACTLY the given inbound ids for --tag mamadah handled")
    h.add_argument("--tag", required=True)
    h.add_argument("--ids", required=True, help="comma-separated inbound ids you actually answered")
    a = ap.parse_args()

    if a.cmd == "read":
        rows = unprocessed(a.tag, a.limit)
        if not rows:
            print(f"(no unhandled inbound on tag={a.tag!r})")
            return 0
        print(f"{len(rows)} unhandled on tag={a.tag!r} (oldest-first):")
        for (rid, tag, text, created, sender, source) in rows:
            print(f"  #{rid} [{created:%Y-%m-%d %H:%M}Z] {sender} ({source}): {text[:200]}")
        print(f"→ answer EACH, then stamp exactly those: handle --tag {a.tag} "
              f"--ids {','.join(str(r[0]) for r in rows)}")
        return 0
    if a.cmd == "handle":
        ids = [int(x) for x in a.ids.split(",") if x.strip()]
        n = mark_handled(ids, a.tag)
        print(f"stamped {n} row(s) handled on tag={a.tag!r} ids={ids}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
