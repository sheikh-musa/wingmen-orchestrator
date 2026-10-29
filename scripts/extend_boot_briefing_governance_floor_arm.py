#!/usr/bin/env python3
"""extend_boot_briefing_governance_floor_arm.py — add the
'governance_floor_status' arm to the LIVE boot_briefing view (op#35246 item
1, console bus #80912/#80914/#80915).

Clone of extend_boot_briefing_arm.py's pattern (which added the
data_provenance_flag arm): builds the new view from the LIVE definition
(pg_get_viewdef on the substrate DB at apply time), never from a hardcoded
body -- that is decision 962's exact failure mode, a stale body silently
stripping arms added by other migrations since. Direct psycopg only, never
`supabase db push`.

This is where live floor status actually lives, now that
docs/governance-floors.md's generated block carries only the floor LIST
(console bus #80912: a status line baked into git can go stale the moment
it's committed -- the failure mode behind bus #80659/#80664). The arm never
silently reports PASS for a floor that has not really been checked recently:
NOT_YET_CHECKED when there is no checked_pass/checked_fail row at all, STALE
when the newest one is older than 12h (2x the reconciler's 6h cadence,
console bus #80915), otherwise the real PASS/FAIL.

Usage:
  python3 scripts/extend_boot_briefing_governance_floor_arm.py            # dry-run (default): prints evidence, rolls back
  python3 scripts/extend_boot_briefing_governance_floor_arm.py --apply     # commits
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
NEW_ARM_SOURCE = "governance_floor_status"
STALE_AFTER = "12 hours"  # console bus #80915: 2x the 6h reconcile cadence
NEW_ARM_SQL = f"""select 'governance_floor_status'::text as source,
    gf.op_ref as key,
    json_build_object(
        'sentence', gf.sentence,
        'enforced_in_code', gf.enforced_in_code,
        'status', case
            when latest.at is null then 'NOT_YET_CHECKED'
            when now() - latest.at > interval '{STALE_AFTER}' then 'STALE'
            when latest.event = 'checked_pass' then 'PASS'
            else 'FAIL'
        end,
        'last_checked_at', latest.at
    ) as context
from governance_floor gf
left join lateral (
    select event, at from governance_floor_audit
     where floor_id = gf.id and event in ('checked_pass', 'checked_fail')
     order by at desc limit 1
) latest on true
where gf.retired_at is null"""

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

    dsn = args.dsn or bus_send.dburl(os.environ.get("DATABASE_URL"))

    with psycopg.connect(dsn) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("select pg_get_viewdef(%s::regclass, true)", (VIEW_NAME,))
            live_body = cur.fetchone()[0]

            before_arms = arm_sources(live_body)
            print(f"ARMS BEFORE ({len(before_arms)}): {before_arms}")

            if NEW_ARM_SOURCE in before_arms:
                print(f"'{NEW_ARM_SOURCE}' is already an arm -- nothing to do (idempotent no-op).")
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
                    f"REFUSE: arm set changed beyond adding '{NEW_ARM_SOURCE}' -- "
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

            print(f"VERIFIED: exactly +1 arm ('{NEW_ARM_SOURCE}'), all other arms' row counts unchanged.")

            if args.apply:
                conn.commit()
                print("COMMITTED.")
            else:
                conn.rollback()
                print("DRY RUN -- rolled back, substrate unchanged. Re-run with --apply to commit.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
