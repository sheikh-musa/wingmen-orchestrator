#!/usr/bin/env python3
"""One-shot: mark SHADOWED tg_media rows so a lane can't be misled into
reading another chat's bytes (bus #47469 follow-up F1, orch-console #47496).

A SHADOWED row is one of the 446 identified by scope_tg_media_collisions.py:
its logged path was later overwritten by a DIFFERENT tag's download (the
pre-fix basename-only naming bug), so the path on disk no longer holds what
this row originally downloaded. The row that currently owns the bytes (the
latest writer, rn=1) is left untouched -- only the SHADOWED rows (rn>1) get
a marker appended to their stored `text`, e.g.:

    [media lost: file overwritten by a later download from another chat;
    do not open this path]

Metadata-only (CAI-1034, scoped to media FILES, not this text column): the
dry-run path only ever SELECTs id, tag, created_at, path and never prints
full `text` content. The apply path additionally reads each targeted row's
`text` once, to write a reversal file -- never printed to stdout -- and the
UPDATE itself is a pure SQL `text || marker` append (the database does the
concatenation).

Safeguards required by orch-console #47508 before --apply is allowed to run:
  1. Append-only: the UPDATE never replaces/strips the original text.
  2. A reversal file (id -> original text) is written under reports/ before
     any row is touched, and a re-run refuses to re-mark an already-marked row.
  3. EXPECTED_SHADOWED_COUNT is asserted against the live count; a mismatch
     refuses outright rather than marking a different set than was reviewed.
  4. This script is tracked (not a one-off), landed via its own PR.

Usage:
    .venv/bin/python3 scripts/mark_shadowed_tg_media_rows.py            # dry-run (default)
    .venv/bin/python3 scripts/mark_shadowed_tg_media_rows.py --apply    # apply -- ONLY on explicit operator go-ahead
"""
import json
import os
import sys
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

MARKER = ("[media lost: file overwritten by a later download from another "
          "chat; do not open this path]")

# orch-console #47508 condition 3: the dry-run reported exactly 446 shadowed
# rows; apply must refuse outright if the live count has since drifted,
# rather than silently mark a different set.
EXPECTED_SHADOWED_COUNT = 446

REVERSAL_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL/SUPABASE_DB_URL not set")
    return dsn


# Same collision definition as scope_tg_media_collisions.py (>1 distinct tag
# downloaded to the same logged path); rn=1 is the CURRENT on-disk owner
# (latest writer) and is NEVER touched -- only rn>1 (shadowed) rows are marked.
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
),
shadowed AS (
    SELECT id, tag, created_at, path FROM ranked WHERE rn > 1
)
"""

# Idempotency guard: a re-run (dry-run or apply) must never double-append the
# marker to a row already marked by a prior apply.
_ALREADY_MARKED_FILTER = "AND id NOT IN (SELECT id FROM operator_messages WHERE text LIKE '%[media lost:%')"


def main() -> None:
    apply = "--apply" in sys.argv
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(_QUERY_BASE +
                    f"SELECT id, tag, created_at, path FROM shadowed "
                    f"WHERE true {_ALREADY_MARKED_FILTER} ORDER BY path, created_at")
        rows = cur.fetchall()

        print(f"{'APPLYING' if apply else 'DRY-RUN'}: {len(rows)} shadowed row(s) "
              f"{'to mark' if not apply else 'being marked'} (current-bytes-owner rows untouched)")
        print()
        by_tag: dict[str, int] = {}
        for _id, tag, _created, _path in rows:
            by_tag[tag or "(untagged)"] = by_tag.get(tag or "(untagged)", 0) + 1
        print("by tag:")
        for tag, n in sorted(by_tag.items(), key=lambda kv: -kv[1]):
            print(f"  {tag}: {n}")
        print()
        print("rows (id, tag, created_at, path):")
        for row in rows:
            print(" ", row)

        if not apply:
            print()
            print(f"Dry-run only -- no rows modified. Re-run with --apply to append the "
                  f"marker to these {len(rows)} row(s).")
            return

        if not rows:
            print()
            print("Nothing to apply.")
            return

        # orch-console #47508 condition 3: refuse if the live count drifted
        # from the reported dry-run count -- never silently mark a different set.
        if len(rows) != EXPECTED_SHADOWED_COUNT:
            print()
            raise SystemExit(
                f"REFUSED: live shadowed-row count ({len(rows)}) != expected "
                f"{EXPECTED_SHADOWED_COUNT}. Re-run the dry-run, confirm the new "
                f"count with the operator, and update EXPECTED_SHADOWED_COUNT "
                f"before applying.")

        ids = [r[0] for r in rows]

        # condition 2: write a reversal file (id, original text) BEFORE
        # touching any row, so the marker append can be undone.
        cur.execute(
            "SELECT id, text FROM operator_messages WHERE id = ANY(%s)", (ids,))
        original_text = {str(rid): text for rid, text in cur.fetchall()}
        os.makedirs(REVERSAL_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        reversal_path = os.path.join(
            REVERSAL_DIR, f"tg_media_shadow_mark_reversal_{stamp}.json")
        with open(reversal_path, "w") as f:
            json.dump({"marker": MARKER, "rows": original_text}, f, indent=2, default=str)
        print()
        print(f"Reversal file written: {reversal_path} ({len(original_text)} row(s))")

        cur.execute(
            "UPDATE operator_messages SET text = text || %s "
            "WHERE id = ANY(%s) AND text NOT LIKE '%%[media lost:%%'",
            (" " + MARKER, ids),
        )
        n = cur.rowcount
        conn.commit()
        print()
        print(f"Marked {n} row(s).")


if __name__ == "__main__":
    main()
