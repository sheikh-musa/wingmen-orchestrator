#!/usr/bin/env python3
"""apply_sql_migration.py — generic direct-psycopg applier for pure-DDL migrations.

    .venv/bin/python3 scripts/apply_sql_migration.py migrations/022_x.sql VERSION NAME [--dry-run]

Strips line comments BEFORE splitting on ';' (comments contain semicolons — a
naive split fractures CREATE TABLE mid-statement). Safe for DDL with no '--'
inside string literals and no dollar-quoted function bodies. Records the migration
in supabase_migrations.schema_migrations. NEVER db push (CLAUDE.md / decision 962).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ORCH = Path(__file__).resolve().parent.parent
load_dotenv(ORCH / ".env")
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def statements(sql: str):
    stripped = "\n".join(
        (line[:line.index("--")] if "--" in line else line)
        for line in sql.splitlines()
    )
    return [s.strip() for s in stripped.split(";") if s.strip()]


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if len(args) < 3:
        print("usage: apply_sql_migration.py <file> <version> <name> [--dry-run]", file=sys.stderr)
        return 2
    path, version, name = args[0], args[1], args[2]
    sql = Path(path).read_text()
    stmts = statements(sql)
    print(f"apply {name} ({version}) — {len(stmts)} statements (dry_run={dry})")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-infra',true)")
        for s in stmts:
            print(f"  {' '.join(s.split())[:72]}")
            if not dry:
                cur.execute(s)
        if not dry:
            cur.execute(
                "INSERT INTO supabase_migrations.schema_migrations (version, name, statements) "
                "VALUES (%s,%s,%s) ON CONFLICT (version) DO NOTHING",
                (version, name, [f"{name}: {len(stmts)} DDL statements"]),
            )
            conn.commit()
        else:
            conn.rollback()
    print("done" if not dry else "dry-run only (rolled back)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
