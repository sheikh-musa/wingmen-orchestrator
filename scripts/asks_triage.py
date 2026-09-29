#!/usr/bin/env python3
"""asks_triage.py — set the TRIAGE axis on one operator_asks row.

Sibling of scripts/asks_open.py / scripts/asks_close.py, same vetted-script
shape (single statement, dsn fallback, exit 0/2).

Migration 082 (bus #45552 -> #45555 -> #45557 GO) adds triage_state to
operator_asks, orthogonal to the existing closed_at/closed_reason lifecycle:
"is this text even an ask" is a judgment call, not derivable from the
agent_messages thread, so it has to be recorded. See migration 082's header
for the full rationale.

Usage:
  asks_triage.py <id> ask  --summary "<one line>" [--delegated-to BODY]
  asks_triage.py <id> not
  asks_triage.py <id> done [--evidence "<bus/op ids>"]

Actions:
  ask    a real, still-open request. Sets triage_state='ask',
         triage_summary (required), optionally delegated_to. ALWAYS clears
         closed_at (reopens if needed) — this is the heuristic pre-
         classifier's undo path: a misclassified 'not_an_ask' is one
         command away from correction.
  not    not a real request (bare ack / approval / inline-answered
         question). Sets triage_state='not_an_ask', closed_at=now(),
         closed_reason='not_a_request'.
  done   a real request that has already been delivered. Sets
         triage_state='done', closed_at=now(), closed_reason='done',
         optionally triage_evidence_ref.

triaged_at/triaged_by are always stamped (triaged_by defaults to
$ORCH_AGENT_ID, else 'orch-console' — same fallback as asks_open.py's
delegated_to).

Exit 0 + prints 'ok' on exactly one row updated; exit 2 + stderr
'error: ...' otherwise (bad args / no DSN / unknown id), so a shell caller
can surface a clean failure.
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv


def _dsn() -> str | None:
    """Same fallback shape as asks_open.py's _dsn(): prefer an already-exported
    DATABASE_URL/SUPABASE_DB_URL, else load the orchestrator's own .env by an
    ABSOLUTE path (robust to whatever cwd the caller shell is in)."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if dsn:
        return dsn
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(env_path)
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", type=int, help="operator_asks.id")
    ap.add_argument("action", choices=("ask", "not", "done"))
    ap.add_argument("--summary", default=None, help="required for action=ask: triage_summary")
    ap.add_argument("--delegated-to", default=None, help="action=ask only: agents.agent_id owner")
    ap.add_argument("--evidence", default=None, help="action=done only: triage_evidence_ref")
    return ap


def triage(item_id: int, action: str, *, summary: str | None = None,
           delegated_to: str | None = None, evidence: str | None = None,
           dsn: str | None = None) -> int:
    if action == "ask" and not (summary and summary.strip()):
        raise ValueError("--summary is required for action=ask")
    dsn = dsn or _dsn()
    if not dsn:
        raise RuntimeError("no DATABASE_URL/SUPABASE_DB_URL")
    triaged_by = os.environ.get("ORCH_AGENT_ID") or "orch-console"

    if action == "ask":
        sql = (
            "UPDATE operator_asks SET "
            "  triage_state = 'ask', triage_summary = %s, "
            "  delegated_to = COALESCE(%s, delegated_to), "
            "  closed_at = NULL, closed_reason = NULL, "
            "  triaged_at = now(), triaged_by = %s "
            "WHERE id = %s"
        )
        params = (summary, delegated_to, triaged_by, item_id)
    elif action == "not":
        sql = (
            "UPDATE operator_asks SET "
            "  triage_state = 'not_an_ask', closed_at = now(), closed_reason = 'not_a_request', "
            "  triaged_at = now(), triaged_by = %s "
            "WHERE id = %s AND closed_at IS NULL"
        )
        params = (triaged_by, item_id)
    else:  # done
        sql = (
            "UPDATE operator_asks SET "
            "  triage_state = 'done', closed_at = now(), closed_reason = 'done', "
            "  triage_evidence_ref = %s, triaged_at = now(), triaged_by = %s "
            "WHERE id = %s AND closed_at IS NULL"
        )
        params = (evidence, triaged_by, item_id)

    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        n = triage(args.id, args.action, summary=args.summary,
                   delegated_to=args.delegated_to, evidence=args.evidence)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"error: {type(e).__name__}: {e}\n")
        return 2
    if n == 1:
        print("ok")
        return 0
    sys.stderr.write(f"error: {n} rows updated (id {args.id} not found or not eligible?)\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
