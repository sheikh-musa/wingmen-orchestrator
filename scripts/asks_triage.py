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
                           [--committed-date ISO] [--outbound-msg-id ID]
  asks_triage.py <id> not
  asks_triage.py <id> done [--evidence "<bus/op ids>"]
                           [--committed-date ISO] [--outbound-msg-id ID]

Actions:
  ask    a real, still-open request. Sets triage_state='ask',
         triage_summary (required), optionally delegated_to. ALWAYS clears
         closed_at (reopens if needed) — this is the heuristic pre-
         classifier's undo path: a misclassified 'not_an_ask' is one
         command away from correction. NEVER pushes chase_by out UNLESS
         --committed-date is given (migration 085, bus #47114 item 3a/b) — a
         plain "in progress" triage must not reschedule the chase net.
  not    not a real request (bare ack / approval / inline-answered
         question). Sets triage_state='not_an_ask', closed_at=now(),
         closed_reason='not_a_request'.
  done   a real request that has already been delivered. Sets
         triage_state='done', closed_at=now(), closed_reason='done',
         optionally triage_evidence_ref.

--committed-date / --outbound-msg-id (migration 085): a dated commitment on
an ask_surface='client-channel' row counts only if it was actually SENT to
the client — outbound_msg_id must name the operator_messages row that states
the date (DB CHECK enforces the pairing; a date agreed only on the bus never
satisfies this). For a client-channel row, `done` REFUSES (raises ValueError)
unless it has --evidence, OR both --committed-date and --outbound-msg-id
(this call or already on the row). ask_surface='operator' rows are NEVER
gated — this is a client-channel-only restriction.

triaged_at/triaged_by are always stamped. triaged_by is resolved fail-closed
via scripts/lib/agent_identity.resolve_agent_id (CC_BASE_AGENT_ID, then
AGENT_ID, then ORCH_AGENT_ID only for the console body itself) — REFUSES
(exit 2) rather than default to 'orch-console' if none resolve (bus #47221:
the old $ORCH_AGENT_ID-or-'orch-console' fallback misattributed 19 rows
triaged by cc-oeh/cc-angullia to the console, since ORCH_AGENT_ID is
fleet-wide .env noise every process inherits regardless of who is actually
running the script). Pass --triaged-by to override explicitly.

Exit 0 + prints 'ok' on exactly one row updated; exit 2 + stderr
'error: ...' otherwise (bad args / no DSN / unknown id), so a shell caller
can surface a clean failure.
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
    ap.add_argument("--committed-date", default=None,
                     help="ISO datetime: a dated commitment (migration 085) — requires "
                          "--outbound-msg-id (this call or already on the row)")
    ap.add_argument("--outbound-msg-id", type=int, default=None,
                     help="operator_messages.id of the outbound send that STATED the "
                          "--committed-date (migration 085)")
    ap.add_argument("--triaged-by", default=None,
                     help="override triaged_by identity (default: auto-resolve fail-closed "
                          "via CC_BASE_AGENT_ID/AGENT_ID — see agent_identity.resolve_agent_id)")
    return ap


def triage(item_id: int, action: str, *, summary: str | None = None,
           delegated_to: str | None = None, evidence: str | None = None,
           committed_date: str | None = None, outbound_msg_id: int | None = None,
           dsn: str | None = None, triaged_by: str | None = None) -> int:
    if action == "ask" and not (summary and summary.strip()):
        raise ValueError("--summary is required for action=ask")
    dsn = dsn or _dsn()
    if not dsn:
        raise RuntimeError("no DATABASE_URL/SUPABASE_DB_URL")
    triaged_by = triaged_by or resolve_agent_id(os.environ)

    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            if action == "done":
                # Client-channel gate (migration 085, bus #47114 item 3a/3c): a dated
                # commitment only counts if it was actually SENT — evidence OR
                # committed_date+outbound_msg_id (this call or already on the row).
                # ask_surface='operator' rows are never gated (unchanged behavior).
                cur.execute(
                    "SELECT ask_surface, committed_date, outbound_msg_id "
                    "FROM operator_asks WHERE id=%s",
                    (item_id,),
                )
                row = cur.fetchone()
                if row is not None:
                    ask_surface, existing_committed, existing_outbound = row
                    has_commit = bool(committed_date or existing_committed)
                    has_outbound = bool(outbound_msg_id or existing_outbound)
                    if ask_surface == "client-channel" and not evidence and not (has_commit and has_outbound):
                        raise ValueError(
                            "client-channel 'done' requires --evidence, or --committed-date "
                            "together with --outbound-msg-id (this call or already on the "
                            "row) — bus #47114 item 3a/3c."
                        )
                sql = (
                    "UPDATE operator_asks SET "
                    "  triage_state = 'done', closed_at = now(), closed_reason = 'done', "
                    "  triage_evidence_ref = %s, triaged_at = now(), triaged_by = %s, "
                    "  committed_date = COALESCE(%s, committed_date), "
                    "  outbound_msg_id = COALESCE(%s, outbound_msg_id) "
                    "WHERE id = %s AND closed_at IS NULL"
                )
                params = (evidence, triaged_by, committed_date, outbound_msg_id, item_id)
            elif action == "ask":
                # committed_date/outbound_msg_id are optional here: a plain "in
                # progress" triage (neither given) leaves chase_by untouched; only a
                # real dated commitment reschedules the chase net (extend, never
                # shrink — GREATEST against any existing chase_by).
                cur.execute(
                    "UPDATE operator_asks SET "
                    "  triage_state = 'ask', triage_summary = %(summary)s, "
                    "  delegated_to = COALESCE(%(delegated_to)s, delegated_to), "
                    "  closed_at = NULL, closed_reason = NULL, "
                    "  triaged_at = now(), triaged_by = %(triaged_by)s, "
                    "  committed_date = COALESCE(%(cd)s::timestamptz, committed_date), "
                    "  outbound_msg_id = COALESCE(%(ob)s, outbound_msg_id), "
                    "  chase_by = CASE WHEN %(cd)s::timestamptz IS NOT NULL "
                    "                  THEN GREATEST(COALESCE(chase_by, %(cd)s::timestamptz), %(cd)s::timestamptz) "
                    "                  ELSE chase_by END "
                    "WHERE id = %(id)s",
                    {"summary": summary, "delegated_to": delegated_to, "triaged_by": triaged_by,
                     "cd": committed_date, "ob": outbound_msg_id, "id": item_id},
                )
                return cur.rowcount
            else:  # not
                sql = (
                    "UPDATE operator_asks SET "
                    "  triage_state = 'not_an_ask', closed_at = now(), closed_reason = 'not_a_request', "
                    "  triaged_at = now(), triaged_by = %s "
                    "WHERE id = %s AND closed_at IS NULL"
                )
                params = (triaged_by, item_id)

            cur.execute(sql, params)
            return cur.rowcount


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        n = triage(args.id, args.action, summary=args.summary,
                   delegated_to=args.delegated_to, evidence=args.evidence,
                   committed_date=args.committed_date, outbound_msg_id=args.outbound_msg_id,
                   triaged_by=args.triaged_by)
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
