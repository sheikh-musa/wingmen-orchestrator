#!/usr/bin/env python3
"""bus_watch.py — the ONE sanctioned way for a lane to watch its bus inbox in a loop.

Why this exists (console #78501/#78690/#78695): a hand-rolled psycopg poll loop
written inside cosem-platform-4's own session (a Monitor loop) opened a
connection WITHOUT autocommit, so its first SELECT opened a transaction that was
never committed. That backend sat idle in transaction for ~2d21h holding a lock
on agent_messages and holding back vacuum, until console terminated it. The
query literal was in no repo, which made the owner hard to find.

This watcher polls on an AUTOCOMMIT connection (fetch_new refuses any other), so a
poll can never hold a transaction open. It watches the instance id, its base id
and the '*' broadcast address, and prints one line per new row.

Usage:
    scripts/bus_watch.py --agent cc-cosem-platform-4 [--interval 20] [--since-id N] [--once]

--since-id defaults to the current max id (only rows that arrive after start).
--once prints whatever is new and exits (for a single check instead of a loop).
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_INSTANCE_SUFFIX = re.compile(r"-[0-9]+$")


def recipients(agent: str) -> list[str]:
    """The addresses a body must watch: itself, its base (if it is an instance), '*'."""
    out = [agent]
    base = _INSTANCE_SUFFIX.sub("", agent)
    if base != agent:
        out.append(base)
    out.append("*")
    return out


def format_row(row: dict) -> str:
    subject = " ".join((row.get("subject") or "").split())
    return f"#{row['id']} {row.get('priority')} {row.get('message_type')} {row.get('from_agent')}: {subject}"


def fetch_new(conn, to_agents: list[str], last_id: int) -> list[dict]:
    """Rows addressed to any of to_agents with id > last_id, oldest first. Refuses a
    non-autocommit connection: polling on one is exactly the idle-in-transaction leak."""
    if not getattr(conn, "autocommit", False):
        raise RuntimeError("bus_watch: connection must be autocommit (a poll would hold a transaction open)")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, priority, message_type, from_agent, subject FROM agent_messages "
            "WHERE id > %s AND to_agent = ANY(%s) ORDER BY id",
            (last_id, to_agents))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _connect():
    import psycopg2
    from scripts.bus_send import dburl
    conn = psycopg2.connect(dburl(os.environ.get("DATABASE_URL")))
    conn.autocommit = True
    return conn


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--agent", required=True, help="your own id (instance id if you have one)")
    p.add_argument("--interval", type=float, default=20.0, help="seconds between polls (min 5)")
    p.add_argument("--since-id", type=int, default=None, help="start after this id (default: current max)")
    p.add_argument("--once", action="store_true", help="print what is new once, then exit")
    args = p.parse_args(argv)

    to = recipients(args.agent)
    conn = _connect()
    last = args.since_id
    if last is None:
        with conn.cursor() as cur:
            cur.execute("SELECT coalesce(max(id), 0) FROM agent_messages")
            last = cur.fetchone()[0]
    print(f"bus_watch: watching {', '.join(to)} from id>{last}", flush=True)
    while True:
        for row in fetch_new(conn, to, last):
            print(format_row(row), flush=True)
            last = max(last, row["id"])
        if args.once:
            return 0
        time.sleep(max(args.interval, 5.0))


if __name__ == "__main__":
    raise SystemExit(main())
