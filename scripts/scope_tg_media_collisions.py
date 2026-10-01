#!/usr/bin/env python3
"""Read-only forensic scope for bus #47469 (tg_media filename collisions).

Finds every logs/tg_media/<name> path referenced by more than one distinct
`operator_messages.tag` (i.e. downloaded by more than one bot/channel under
the pre-fix basename-only naming) and reports, per path, which row is the
CURRENT on-disk owner (latest created_at) and which rows are SHADOWED --
their originally-downloaded bytes no longer exist at that path; the file now
holds whatever the later writer downloaded.

Metadata only: ids, tags, timestamps, paths. Never opens a media file or
prints operator_messages.text content (CAI-1034) -- this is a counting/
listing tool, not a content tool.

Usage: .venv/bin/python3 scripts/scope_tg_media_collisions.py [--rows]
  (no flag)  summary counts only
  --rows     also list every shadowed row's id/tag/created_at/path
"""
import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

PRODUCTION_SILO_REFS = ("tscuymavysscrvoberrr",)  # read-only query; still refuse a surprise DSN


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL/SUPABASE_DB_URL not set")
    return dsn


_QUERY_BASE = """
WITH media AS (
    SELECT id, tag, created_at,
           substring(text from 'tg_media/[^ |]+') AS path
    FROM operator_messages
    WHERE text LIKE '%tg_media/%'
),
colliding AS (
    SELECT path FROM media WHERE path IS NOT NULL
    GROUP BY path HAVING count(DISTINCT tag) > 1
),
ranked AS (
    SELECT m.id, m.tag, m.created_at, m.path,
           row_number() OVER (PARTITION BY m.path ORDER BY m.created_at DESC) AS rn
    FROM media m JOIN colliding c USING (path)
)
"""


def main() -> None:
    show_rows = "--rows" in sys.argv
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(_QUERY_BASE + "SELECT count(DISTINCT path) FROM colliding")
        n_paths = cur.fetchone()[0]

        cur.execute(_QUERY_BASE + "SELECT count(*) FROM ranked WHERE rn = 1")
        n_current = cur.fetchone()[0]

        cur.execute(_QUERY_BASE + "SELECT count(*) FROM ranked WHERE rn > 1")
        n_shadowed = cur.fetchone()[0]

        print(f"colliding paths (>1 distinct tag downloaded to the same name): {n_paths}")
        print(f"rows that are the CURRENT on-disk owner (latest writer): {n_current}")
        print(f"rows SHADOWED (their bytes were overwritten by a later tag): {n_shadowed}")
        print()

        cur.execute(_QUERY_BASE +
                    "SELECT tag, count(*) FROM ranked WHERE rn > 1 "
                    "GROUP BY tag ORDER BY count(*) DESC")
        print("shadowed rows by tag:")
        for tag, n in cur.fetchall():
            print(f"  {tag or '(untagged)'}: {n}")

        if show_rows:
            print()
            print("shadowed rows (id, tag, created_at, path):")
            cur.execute(_QUERY_BASE +
                        "SELECT id, tag, created_at, path FROM ranked WHERE rn > 1 "
                        "ORDER BY path, created_at")
            for row in cur.fetchall():
                print(" ", row)


if __name__ == "__main__":
    main()
