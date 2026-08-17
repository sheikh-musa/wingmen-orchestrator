"""Apply migration 055 — close anon/authenticated reach on the five RLS-OFF fleet tables.

CLAUDE.md forbids `supabase db push` against prod (decision-962): the CLI's shadow-diff
path re-applies historic CREATE OR REPLACE VIEW statements and silently strips later arms.
Direct psycopg-apply instead, same pattern as scripts/apply_held_commitments.py.

Dry-run is the DEFAULT and it genuinely rolls back. The post-checks run inside the same
transaction either way, so a dry-run PROVES the end state before anything commits — a
dry-run flag that does not actually disarm the write is decorative (CAI-RESP-1017).

The migration file wraps itself in BEGIN/COMMIT; those lines are stripped so THIS script
owns the transaction boundary. Otherwise the file's COMMIT fires mid-way and a dry-run
commits anyway.

Usage:
  python scripts/apply_mig055_anon_read_lockdown.py            # dry-run (rolled back)
  python scripts/apply_mig055_anon_read_lockdown.py --apply    # commit
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "055_close_anon_read_on_five_rls_off_tables.sql"

TABLES = [
    "held_commitments",
    "fleet_proposals",
    "chat_members",
    "audit_chain_boundaries",
    "revenue_ledger",
]


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 1
    apply = "--apply" in sys.argv

    sql = MIGRATION.read_text()
    stripped = "\n".join(
        line for line in sql.splitlines()
        if line.strip().upper() not in ("BEGIN;", "COMMIT;")
    )

    with psycopg.connect(dsn, connect_timeout=20, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
            cur.execute(stripped)

            print(f"== post-state ({'APPLY' if apply else 'DRY-RUN'}) ==")
            ok = True
            for t in TABLES:
                cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid=('public.'||%s)::regclass", (t,))
                rls = cur.fetchone()[0]
                privs = {}
                for role in ("anon", "authenticated"):
                    bits = [
                        p for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
                        if _has(cur, role, t, p)
                    ]
                    privs[role] = bits
                # The bar: RLS on AND no anon/authenticated privilege left.
                good = bool(rls) and not privs["anon"] and not privs["authenticated"]
                ok = ok and good
                print(f"  {'OK ' if good else 'BAD'} {t:<24} rls={rls}  anon={privs['anon'] or '-'}  authenticated={privs['authenticated'] or '-'}")

            # console_readonly must KEEP its chat_members read — proving we did not
            # silently narrow an access that was granted on purpose.
            cur.execute("SELECT count(*) FROM pg_policies WHERE schemaname='public' "
                        "AND tablename='chat_members' AND policyname='chat_members_console_readonly_select'")
            pol = cur.fetchone()[0]
            print(f"  {'OK ' if pol == 1 else 'BAD'} chat_members console_readonly SELECT policy present ({pol})")
            ok = ok and pol == 1

            if not ok:
                print("\nPOST-CHECK FAILED — rolling back regardless of --apply", file=sys.stderr)
                conn.rollback()
                return 2

            if apply:
                conn.commit()
                print("\nCOMMITTED.")
            else:
                conn.rollback()
                print("\nrolled back (dry-run). Re-run with --apply to commit.")
    return 0


def _has(cur, role: str, table: str, priv: str) -> bool:
    cur.execute("SELECT has_table_privilege(%s, ('public.'||%s)::regclass, %s)", (role, table, priv))
    return bool(cur.fetchone()[0])


if __name__ == "__main__":
    sys.exit(main())
