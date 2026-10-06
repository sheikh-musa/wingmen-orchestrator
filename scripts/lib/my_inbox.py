#!/usr/bin/env python3
"""my_inbox.py — the ONE sanctioned way for a lane to read its OWN unread
agent_messages, base-id and instance-id inclusive.

WHY (orch-console bus #53620, bus #51166 regression class): a lane's mail
can be addressed to either its base id (CC_BASE_AGENT_ID, e.g. 'cc-substrate')
or its instance id (CC_AGENT_ID, e.g. 'cc-substrate-1') — build_launch_context.py
already fixed this for the boot-context arm, but nothing stopped a lane from
hand-rolling a single-id query mid-session and silently missing instance-
addressed mail (the exact shape of #53585 sitting unread while #53582/#53588,
both base-addressed, were read fine). This is the standing fix: always
derive both ids from the environment, never hardcode one.

Usage:
    python3 scripts/lib/my_inbox.py                 # unread, oldest first
    python3 scripts/lib/my_inbox.py --include-read   # everything, newest first
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.getcwd())

import psycopg
from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))


def my_ids() -> list[str]:
    """[base] if no distinct instance id is set, else [base, instance] — never
    just one when both exist and differ (the regression this file exists to
    close)."""
    base = os.environ.get("CC_BASE_AGENT_ID") or os.environ.get("AGENT_ID")
    if not base:
        raise SystemExit("my_inbox.py: no CC_BASE_AGENT_ID/AGENT_ID in this process's environment.")
    instance = os.environ.get("CC_AGENT_ID")
    if instance and instance != base:
        return [base, instance]
    return [base]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-read", action="store_true")
    args = parser.parse_args(argv)

    ids = my_ids()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("my_inbox.py: DATABASE_URL not set.")

    where = "to_agent = ANY(%s)"
    if not args.include_read:
        where += " AND read_at IS NULL"
    order = "created_at DESC" if args.include_read else "created_at ASC"

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id, from_agent, to_agent, priority, subject, created_at "
            f"FROM agent_messages WHERE {where} ORDER BY {order}",
            (ids,),
        )
        rows = cur.fetchall()

    print(f"# inbox for {ids} ({'all' if args.include_read else 'unread'}, {len(rows)} row(s))")
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
