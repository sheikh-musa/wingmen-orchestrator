"""BUG-035 / CAI-RESP-205: blocking_tasks reconciliation primitive (psycopg-apply).

Creates the minimal substrate so cross-agent BLOCKING handoffs have a checked
reconciliation state (read != reconciled). Idempotent DDL. CLAUDE.md forbids
`supabase db push` to prod (decision-962); use this direct apply.

Usage:
  python scripts/apply_blocking_tasks_schema.py            # dry-run (rolled back)
  python scripts/apply_blocking_tasks_schema.py --apply    # commit
"""
from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

DDL = """
create table if not exists blocking_tasks (
    id bigint generated always as identity primary key,
    owner_agent text not null,
    created_by text not null,
    subject text not null,
    detail text,
    thread_id uuid,
    status text not null default 'open'
        check (status in ('open', 'reconciled', 'cancelled')),
    created_at timestamptz not null default now(),
    reconciled_at timestamptz,
    reconciled_by_decision_ref text,
    is_test boolean not null default false
);

alter table strategic_decisions
    add column if not exists unblocks_task_id bigint references blocking_tasks(id);

create or replace view open_blocking_tasks as
select bt.id, bt.owner_agent, bt.created_by, bt.subject, bt.detail,
       bt.thread_id, bt.created_at, bt.is_test,
       sd.decision_ref as unblocking_ruling_ref,
       (sd.decision_ref is not null) as ruling_issued,
       (now() - bt.created_at) as age
from blocking_tasks bt
left join strategic_decisions sd on sd.unblocks_task_id = bt.id
where bt.status = 'open' and bt.is_test is not true;
"""


def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("""
                select column_name from information_schema.columns
                where table_name = 'blocking_tasks' order by ordinal_position
            """)
            cols = [r[0] for r in cur.fetchall()]
            cur.execute("select count(*) from open_blocking_tasks")
            open_n = cur.fetchone()[0]
            cur.execute("""
                select 1 from information_schema.columns
                where table_name='strategic_decisions' and column_name='unblocks_task_id'
            """)
            link_ok = cur.fetchone() is not None
            print(f"blocking_tasks cols: {cols}")
            print(f"strategic_decisions.unblocks_task_id present: {link_ok}")
            print(f"open_blocking_tasks rows: {open_n}")

            ok = bool(cols) and link_ok
            if apply and ok:
                conn.commit()
                print("\nAPPLIED + committed.")
            elif apply:
                conn.rollback()
                print("\nABORTED: schema not in expected state; not committing.")
                return 1
            else:
                conn.rollback()
                print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
