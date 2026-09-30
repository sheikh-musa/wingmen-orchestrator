#!/usr/bin/env python3
"""asks_open.py — open an operator_asks row that is WAITING ON THE OPERATOR.

Sibling of scripts/asks_close.py, same vetted-script pattern: the fleet
console's DB session is read-only by construction, so a write shells out to a
single-purpose script running in the writable orchestrator env.

Piece 3 of the op#22669 "operator asks" reliability build (Musa: "if I ask
1000 things I expect you to track 1001"; bus thread
d0533248-2d25-484f-84ad-3cdd08fe1fce, bus #43749/#43765/#43767). Wired into
scripts/tg_send.sh and scripts/nazim_send.sh's optional `--ask` flag: an
OUTBOUND message that is itself a genuine ask of the operator
("WAITING ON MUSA: ...") gets its OWN ledger row (waiting_on_operator=true)
linked back to that outbound message (outbound_msg_id) — so a genuine
Telegram reply to it auto-closes the ask
(nervous_system/operator_log.py maybe_track_ask()) instead of only opening a
second, untracked one.

DESIGN INVARIANT (migration 044): status is never stored on operator_asks.
waiting_on_operator/chase_by/outbound_msg_id (migration 072) are structural
facts recorded at open time, exactly like the existing delegated_to column —
not a status cache.

Usage:
  asks_open.py "<ask text>" [--chase-hours N] [--outbound-msg-id ID] [--delegated-to BODY]

Exit 0 + prints the new row's id on success; exit 2 + stderr 'error: ...' otherwise
(bad args / no DSN / insert failure), so a shell caller can surface a clean failure.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.agent_identity import resolve_agent_id  # noqa: E402


def _dsn() -> str | None:
    """Same fallback shape as scripts/console_assign.py's _dsn(): prefer an
    already-exported DATABASE_URL/SUPABASE_DB_URL, else load the orchestrator's
    own .env by an ABSOLUTE path (robust to whatever cwd the caller shell is in —
    tg_send.sh/nazim_send.sh invoke this with an absolute interpreter path, not
    necessarily from $ORCH_DIR)."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if dsn:
        return dsn
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(env_path)
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ask", help="the ask text, e.g. 'WAITING ON MUSA: approve the migration'")
    ap.add_argument("--chase-hours", type=float, default=None,
                     help="re-chase deadline: chase_by = now() + N hours (omit for no deadline)")
    ap.add_argument("--outbound-msg-id", type=int, default=None,
                     help="operator_messages.id of the outbound send this ask is linked to "
                          "(enables reply-linked auto-close on a genuine Telegram reply)")
    ap.add_argument("--delegated-to", default=None,
                     help="agents.agent_id this ask is owned by (default: auto-resolve "
                          "fail-closed via CC_BASE_AGENT_ID/AGENT_ID/console's own "
                          "ORCH_AGENT_ID — see agent_identity.resolve_agent_id; bus #47221)")
    return ap


def open_ask(ask: str, chase_hours: float | None = None, outbound_msg_id: int | None = None,
             delegated_to: str | None = None, dsn: str | None = None) -> int:
    if not ask or not ask.strip():
        raise ValueError("ask text must not be empty")
    dsn = dsn or _dsn()
    if not dsn:
        raise RuntimeError("no DATABASE_URL/SUPABASE_DB_URL")
    delegated_to = delegated_to or resolve_agent_id(os.environ)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            # make_interval()'s `hours` parameter is an int, not double precision —
            # passing a fractional --chase-hours (e.g. 1.5) raises UndefinedFunction
            # (no make_interval(hours => float8) overload exists). Multiplying a
            # float8 by the `interval '1 hour'` literal handles fractional hours
            # correctly and needs no int truncation.
            # migration 082: rows opened here are always deliberate, already-
            # summarized asks (never raw captures), so they get triage_state='ask'
            # with triage_summary=the ask text at insert time — no human triage
            # step needed. Only maybe_track_ask()'s raw inbound captures start at
            # the 'captured' default.
            cur.execute(
                "INSERT INTO operator_asks "
                "  (ask, delegated_to, waiting_on_operator, chase_by, outbound_msg_id, "
                "   triage_state, triage_summary, triaged_at, triaged_by) "
                "VALUES (%s, %s, true, "
                "  CASE WHEN %s::float8 IS NULL THEN NULL "
                "       ELSE now() + (%s::float8 * interval '1 hour') END, "
                "  %s, 'ask', %s, now(), %s) "
                "RETURNING id",
                (ask, delegated_to, chase_hours, chase_hours, outbound_msg_id,
                 ask, "asks_open"),
            )
            return cur.fetchone()[0]


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rid = open_ask(args.ask, args.chase_hours, args.outbound_msg_id, args.delegated_to)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"error: {type(e).__name__}: {e}\n")
        return 2
    print(rid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
