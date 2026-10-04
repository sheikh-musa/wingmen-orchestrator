#!/usr/bin/env python3
"""extend_boot_briefing_arm.py — add the 'data_provenance_flag' arm to the LIVE
boot_briefing view, built from pg_get_viewdef() at apply time.

orch-console gate condition #1 (bus #51717, re #51716): "build the new view
from the LIVE definition (pg_get_viewdef on the substrate DB at apply time),
never from a migration file's old body (decision 962)." Decision 962
(CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS) is exactly the bug this avoids: a
hardcoded view body baked into a migration/script can silently strip arms
added by OTHER migrations that ran after it was written. This script never
hardcodes boot_briefing's body — it reads the live definition, appends
exactly one new arm, and verifies (inside the same transaction) that nothing
else changed before committing.

Direct psycopg only — never `supabase db push` (CLAUDE.md / decision 962).

Usage:
  python3 scripts/extend_boot_briefing_arm.py            # dry-run (default): prints evidence, rolls back
  python3 scripts/extend_boot_briefing_arm.py --apply     # commits
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg

sys.path.insert(0, os.path.dirname(__file__))
import bus_send  # noqa: E402

VIEW_NAME = "boot_briefing"
NEW_ARM_SOURCE = "data_provenance_flag"
NEW_ARM_SQL = """select 'data_provenance_flag'::text as source,
    (dpf.project_ref || case when dpf.org_id = '' then '' else '/' || dpf.org_id end) as key,
    json_build_object('alias', dpf.alias, 'classification', dpf.classification,
        'evidence', left(dpf.evidence, 300), 'owner', dpf.owner, 'updated_at', dpf.updated_at) as context
from data_provenance_flags dpf"""

_ARM_SOURCE_RE = re.compile(r"select\s+'([a-zA-Z_]+)'::text\s+as\s+source", re.IGNORECASE)


def arm_sources(view_body: str) -> list[str]:
    return _ARM_SOURCE_RE.findall(view_body)


def row_counts_by_source(cur, view_name: str = VIEW_NAME) -> dict[str, int]:
    cur.execute(f"select source, count(*) from {view_name} group by source")
    return dict(cur.fetchall())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="commit (default: dry-run, rolls back)")
    p.add_argument("--dsn", default=None)
    args = p.parse_args(argv)

    dsn = args.dsn or bus_send.dburl(os.environ)

    with psycopg.connect(dsn) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("select pg_get_viewdef(%s::regclass, true)", (VIEW_NAME,))
            live_body = cur.fetchone()[0]

            before_arms = arm_sources(live_body)
            print(f"ARMS BEFORE ({len(before_arms)}): {before_arms}")

            if NEW_ARM_SOURCE in before_arms:
                print(f"'{NEW_ARM_SOURCE}' is already an arm — nothing to do (idempotent no-op).")
                conn.rollback()
                return 0

            before_rowcounts = row_counts_by_source(cur)
            print(f"ROW COUNTS BEFORE: {before_rowcounts}")

            stripped = live_body.rstrip()
            if stripped.endswith(";"):
                stripped = stripped[:-1]
            new_body = f"{stripped}\nunion all\n{NEW_ARM_SQL};"

            cur.execute(f"create or replace view {VIEW_NAME} as {new_body[:-1]}")

            cur.execute("select pg_get_viewdef(%s::regclass, true)", (VIEW_NAME,))
            after_body = cur.fetchone()[0]
            after_arms = arm_sources(after_body)
            print(f"ARMS AFTER ({len(after_arms)}): {after_arms}")

            after_rowcounts = row_counts_by_source(cur)
            print(f"ROW COUNTS AFTER: {after_rowcounts}")

            # Verify: exactly +1 arm, the new one, nothing else removed or renamed.
            if set(after_arms) != set(before_arms) | {NEW_ARM_SOURCE}:
                print(
                    f"REFUSE: arm set changed beyond adding '{NEW_ARM_SOURCE}' — "
                    f"before={sorted(before_arms)} after={sorted(after_arms)}",
                    file=sys.stderr,
                )
                conn.rollback()
                return 1
            if len(after_arms) != len(before_arms) + 1:
                print(
                    f"REFUSE: arm count did not increase by exactly 1 "
                    f"({len(before_arms)} -> {len(after_arms)})",
                    file=sys.stderr,
                )
                conn.rollback()
                return 1
            # Every pre-existing source's row count must be unchanged.
            for source, count in before_rowcounts.items():
                if after_rowcounts.get(source) != count:
                    print(
                        f"REFUSE: row count for existing arm '{source}' changed "
                        f"({count} -> {after_rowcounts.get(source)})",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 1

            print("VERIFIED: exactly +1 arm ('data_provenance_flag'), all other arms' row counts unchanged.")

            if args.apply:
                conn.commit()
                print("COMMITTED.")
            else:
                conn.rollback()
                print("DRY RUN — rolled back, substrate unchanged. Re-run with --apply to commit.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
