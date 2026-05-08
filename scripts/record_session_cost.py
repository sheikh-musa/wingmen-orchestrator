#!/usr/bin/env python3
"""record_session_cost — manual cc_session_costs row writer.

Per CAI-RESP-154 Q2 (manual-v1 capture mechanism). Each CC (and cai) runs
this at session end (or restart) with their token spend numbers from the
session JSON / Anthropic billing dashboard / claude.ai per-conversation
counter.

Visibility-only-30-day window per CAI-RESP-153 P2; data-quality tolerance
is high. Outlier threshold (default 50000) lives in boot_briefing_config.

Usage:
  scripts/record_session_cost.py \\
      --cc-identity cc-orchestrator \\
      --sub-tag cc-orchestrator-2 \\
      --session-id <session-uuid-or-tag> \\
      --input-tokens 12000 \\
      --output-tokens 4000 \\
      --source claude_code_cli \\
      [--has-per-message-detail] \\
      [--notes 'wrap session, compaction at 70%']

Reads connection details from the project's .env (DATABASE_URL or
SUPABASE_DB_URL). Exits 0 on success, 1 on DB error, 2 on usage error.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


_VALID_SOURCES = ("claude_code_cli", "anthropic_api", "claude_ai_web", "unknown")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Record a cc_session_costs row at session end. "
                    "Per CAI-RESP-153 P2 / CAI-RESP-154 Q2 (manual-v1).",
        prog="record_session_cost",
    )
    parser.add_argument("--cc-identity", required=True,
                        help="CC identity (e.g. cc-orchestrator, cc-ihsanos, cai)")
    parser.add_argument("--sub-tag", default=None,
                        help="Optional sub-tag (e.g. cc-orchestrator-2)")
    parser.add_argument("--session-id", default=None,
                        help="Optional session id or label")
    parser.add_argument("--started-at", default=None,
                        help="ISO 8601 timestamp; defaults to now() minus 1 hour if absent")
    parser.add_argument("--ended-at", default=None,
                        help="ISO 8601 timestamp; defaults to current time")
    parser.add_argument("--input-tokens", type=int, required=True,
                        help="Total input tokens consumed")
    parser.add_argument("--output-tokens", type=int, required=True,
                        help="Total output tokens generated")
    parser.add_argument("--source", required=True, choices=_VALID_SOURCES,
                        help="Capture origin: where the token counts came from")
    parser.add_argument("--has-per-message-detail", action="store_true",
                        help="Set true only if you also INSERT into cc_session_messages")
    parser.add_argument("--notes", default=None,
                        help="Free-form notes, e.g. 'compaction at 70 percent mark'")

    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    # Lazy imports so --help works without DB deps
    try:
        import psycopg
        from dotenv import load_dotenv
    except ImportError as exc:
        print(f"ERROR: missing dependency ({exc})", file=sys.stderr)
        return 1

    load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: DATABASE_URL or SUPABASE_DB_URL must be set", file=sys.stderr)
        return 1

    now_iso = datetime.now(timezone.utc).isoformat()
    started_at = args.started_at or now_iso
    ended_at = args.ended_at or now_iso

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cc_session_costs
                      (cc_identity, sub_tag, session_id, started_at, ended_at,
                       input_tokens, output_tokens, source,
                       has_per_message_detail, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        args.cc_identity,
                        args.sub_tag,
                        args.session_id,
                        started_at,
                        ended_at,
                        args.input_tokens,
                        args.output_tokens,
                        args.source,
                        args.has_per_message_detail,
                        args.notes,
                    ),
                )
                row_id = cur.fetchone()[0]
    except Exception as exc:
        print(f"ERROR: DB write failed: {exc}", file=sys.stderr)
        return 1

    total = args.input_tokens + args.output_tokens
    print(f"recorded cc_session_costs id={row_id} ({total} tokens for {args.cc_identity})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
