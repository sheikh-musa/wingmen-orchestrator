"""DB preflight for deploy_cc_family.sh — TASK-045.

Verifies:
  - agents row exists for the family with non-empty repo_scope
  - No active sibling session in agent_status (last_heartbeat within 5 min)

Returns (exit_code, human_message). Caller (bash) exits with the code.

Parent: scripts/deploy_cc_family.sh, docs/runbooks/deploy-cc-family.md
"""
from __future__ import annotations

import argparse
import enum
import os
import sys
from datetime import datetime, timezone

import psycopg


class ExitCode(enum.IntEnum):
    OK = 0
    AGENTS_MISSING = 3       # mirrors deploy_cc_family.sh exit 3
    ACTIVE_SIBLING = 7       # mirrors deploy_cc_family.sh exit 7


_SIBLING_STALE_THRESHOLD_SECONDS = 300  # 5 min — matches heartbeat interval


def check_family_preflight(family_id: str, dsn: str) -> tuple[ExitCode, str]:
    """Run agents + agent_status checks. Returns (code, message)."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, repo_scope, last_heartbeat FROM agents WHERE id = %s",
                (family_id,),
            )
            row = cur.fetchone()
            if row is None:
                return (
                    ExitCode.AGENTS_MISSING,
                    f"agents row missing for '{family_id}' — insert via CAI before deploying",
                )

            _, repo_scope, _ = row
            if not repo_scope:
                return (
                    ExitCode.AGENTS_MISSING,
                    f"agents.repo_scope empty for '{family_id}' — set scope via CAI before deploying",
                )

            # Sibling detection mirrors scripts/lib/auto_agent_id.py: agent_status has
            # no base_agent_id column; identity is encoded in agent_id via the
            # sub-tag convention (`<base>_<N>`). LIKE '<base>_%' matches all live
            # sub-tags of this family.
            cur.execute(
                """
                SELECT agent_id, last_heartbeat
                FROM agent_status
                WHERE agent_id LIKE %s
                  AND last_heartbeat > now() - interval '5 minutes'
                  AND status != 'offline'
                ORDER BY last_heartbeat DESC
                """,
                (f"{family_id}_%",),
            )
            siblings = cur.fetchall()
            if siblings:
                names = ", ".join(s[0] for s in siblings)
                return (
                    ExitCode.ACTIVE_SIBLING,
                    f"active sibling(s) already heartbeating: {names} — "
                    f"run launch_dangerous_cc.sh directly or clean up stale rows",
                )

    return (ExitCode.OK, f"{family_id}: agents row + no-active-sibling checks ok")


def main() -> int:
    p = argparse.ArgumentParser(description="DB preflight for deploy_cc_family.sh")
    p.add_argument("family_id")
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    args = p.parse_args()

    if not args.dsn:
        print("error: DATABASE_URL not set and --dsn not passed", file=sys.stderr)
        return 1

    code, msg = check_family_preflight(args.family_id, args.dsn)
    stream = sys.stdout if code == ExitCode.OK else sys.stderr
    print(msg, file=stream)
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
