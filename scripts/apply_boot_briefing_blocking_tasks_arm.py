"""BUG-035 / CAI-RESP-205: boot_briefing open_blocking_tasks arm (psycopg-apply).

Surfaces open cross-agent blockers in the boot index so digests / chases key on
task-cleared, not read_at. Arm-level surgery (append one arm; preserve all
others verbatim) to avoid the decision-962 arm-stripping hazard. Idempotent:
skips if the arm already exists.

Usage:
  python scripts/apply_boot_briefing_blocking_tasks_arm.py            # dry-run
  python scripts/apply_boot_briefing_blocking_tasks_arm.py --apply    # commit
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

NEW_ARM = """ SELECT 'open_blocking_task'::text AS source,
    obt.owner_agent AS key,
    json_build_object('task_id', obt.id, 'owner', obt.owner_agent, 'created_by', obt.created_by, 'subject', obt.subject, 'ruling_issued', obt.ruling_issued, 'unblocking_ruling_ref', obt.unblocking_ruling_ref, 'age_minutes', (EXTRACT(epoch FROM obt.age) / 60)::integer) AS context
   FROM open_blocking_tasks obt"""


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
            if "'open_blocking_task'::text AS source" in viewdef:
                print("arm already present; nothing to do.")
                conn.rollback()
                return 0
            new_def = viewdef.rstrip().rstrip(";") + "\nUNION ALL\n" + NEW_ARM
            cur.execute(f"CREATE OR REPLACE VIEW boot_briefing AS {new_def}")
            cur.execute(
                "select count(*) from boot_briefing where source = 'open_blocking_task'"
            )
            n = cur.fetchone()[0]
            cur.execute("select count(*) from boot_briefing")
            total = cur.fetchone()[0]
            print(f"boot_briefing total rows: {total}  open_blocking_task rows: {n}")

            if apply:
                conn.commit()
                print("\nAPPLIED + committed.")
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
