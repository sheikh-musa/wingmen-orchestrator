"""Apply migration 056 — security_invoker + revoke on the two anon-reachable views.

CLAUDE.md forbids `supabase db push` against prod (decision-962). Direct psycopg apply.
Dry-run is the DEFAULT and genuinely rolls back; post-checks run inside the same
transaction either way, so a dry-run proves the end state before anything commits.

⚠ The post-check reads privileges via a helper, NOT via `cur.execute(...) or cur.fetchone()`
— in psycopg3 `execute()` returns the cursor, which is truthy, so that idiom reports every
role as holding every privilege. The first version of this check did exactly that and
produced a false BAD. A check that cannot fail correctly is not a check.

Usage:
  python scripts/apply_mig056_view_security_invoker.py            # dry-run
  python scripts/apply_mig056_view_security_invoker.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "056_anon_reachable_views_security_invoker.sql"
VIEWS = ["agent_observed_activity", "held_commitments_due"]


def has(cur, role: str, obj: str, priv: str = "SELECT") -> bool:
    cur.execute("SELECT has_table_privilege(%s, ('public.'||%s)::regclass, %s)", (role, obj, priv))
    return bool(cur.fetchone()[0])


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv
    sql = "\n".join(
        l for l in MIGRATION.read_text().splitlines()
        if l.strip().upper() not in ("BEGIN;", "COMMIT;")
    )
    with psycopg.connect(dsn, connect_timeout=20, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
        cur.execute(sql)
        ok = True
        print(f"== post-state ({'APPLY' if apply else 'DRY-RUN'}) ==")
        for v in VIEWS:
            cur.execute("SELECT c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relname=%s", (v,))
            si = "security_invoker=on" in str(cur.fetchone()[0])
            a, au = has(cur, "anon", v), has(cur, "authenticated", v)
            good = si and not a and not au
            ok = ok and good
            print(f"  {'OK ' if good else 'BAD'} {v:<26} security_invoker={si} anon={a} authenticated={au}")
        for v in VIEWS:
            cur.execute(f"SELECT count(*) FROM public.{v}")
            print(f"      postgres still reads {v}: {cur.fetchone()[0]} rows")
        if not ok:
            conn.rollback()
            print("\nPOST-CHECK FAILED — rolled back regardless of --apply", file=sys.stderr)
            return 2
        if apply:
            conn.commit()
            print("\nCOMMITTED.")
        else:
            conn.rollback()
            print("\nrolled back (dry-run). Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
