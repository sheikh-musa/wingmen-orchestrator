"""SUBSTRATE-COHERENCE-001 B3: boot_briefing.inbox_hygiene excludes is_test.

cai's B3: "inbox queries, SLA views, and boot_briefing exclude is_test=true."
The boot_briefing inbox_hygiene arm reads agent_messages directly (stale
unresponded > 12h) with no is_test filter, so a flagged test escalation could
still surface as a hygiene violation. Add the filter to that arm's inner WHERE.

Arm-level surgery on the LIVE definition (split on UNION ALL, edit only the
inbox_hygiene arm, preserve all others verbatim) to avoid the decision-962
arm-stripping hazard. CLAUDE.md forbids `supabase db push` to prod.

Usage:
  python scripts/apply_boot_briefing_inbox_hygiene_istest.py            # dry-run
  python scripts/apply_boot_briefing_inbox_hygiene_istest.py --apply    # commit
"""
from __future__ import annotations

import os
import re
import sys

import psycopg
from dotenv import load_dotenv

ANCHOR = (
    "agent_messages.read_at IS NULL AND agent_messages.created_at "
    "< (now() - '12:00:00'::interval)"
)
REPLACEMENT = ANCHOR + " AND agent_messages.is_test IS NOT TRUE"


def transform(viewdef: str) -> str:
    arms = re.split(r"\nUNION ALL\n", viewdef)
    out = []
    seen = 0
    for arm in arms:
        if "'inbox_hygiene'::text AS source" in arm:
            seen += 1
            new_arm, n = re.subn(re.escape(ANCHOR), REPLACEMENT, arm)
            assert n == 1, f"inbox_hygiene anchor matched {n} times (expected 1)"
            out.append(new_arm)
        else:
            out.append(arm)
    assert seen == 1, f"inbox_hygiene arm matched {seen} times (expected 1)"
    return "\nUNION ALL\n".join(out)


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("select pg_get_viewdef('boot_briefing'::regclass, true)")
            viewdef = cur.fetchone()[0]
            new_def = transform(viewdef)
            cur.execute(f"CREATE OR REPLACE VIEW boot_briefing AS {new_def}")

            cur.execute("""
                select count(*) from boot_briefing b
                join agent_messages am
                  on am.id = (b.context->>'message_id')::bigint
                where b.source = 'inbox_hygiene' and am.is_test is true
            """)
            leaked = cur.fetchone()[0]
            cur.execute("select count(*) from boot_briefing")
            total = cur.fetchone()[0]
            print(f"boot_briefing rows: {total}  inbox_hygiene is_test leaks: {leaked}")

            if apply and leaked == 0:
                conn.commit()
                print("\nAPPLIED + committed.")
            elif apply:
                conn.rollback()
                print(f"\nABORTED: {leaked} is_test rows still leak; not committing.")
                return 1
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
