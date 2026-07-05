#!/usr/bin/env python3
"""backup_console_memory.py — nightly snapshot of the console body's memory
dir into substrate table console_memory_backup (delta: skip files whose sha
matches the latest stored version). Restore writes the latest version of every
file back out.

Usage:
  backup_console_memory.py                 # snapshot changed files
  backup_console_memory.py --restore DIR   # write latest versions into DIR
"""
import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

ORCH = os.path.expanduser("~/wingmen/orchestrator")
load_dotenv(os.path.join(ORCH, ".env"))

MEMORY_DIR = os.path.expanduser(
    "~/.claude/projects/-Users-musa-wingmen-orchestrator/memory")


def _conn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    conn = psycopg.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
    return conn


def backup() -> int:
    files = sorted(f for f in os.listdir(MEMORY_DIR) if f.endswith(".md"))
    written = skipped = 0
    with _conn() as conn, conn.cursor() as cur:
        for name in files:
            content = open(os.path.join(MEMORY_DIR, name), encoding="utf-8").read()
            sha = hashlib.sha256(content.encode()).hexdigest()
            cur.execute(
                "SELECT content_sha256 FROM console_memory_backup "
                "WHERE file_name=%s ORDER BY backed_up_at DESC LIMIT 1", (name,))
            row = cur.fetchone()
            if row and row[0] == sha:
                skipped += 1
                continue
            cur.execute(
                "INSERT INTO console_memory_backup (file_name, content, content_sha256) "
                "VALUES (%s,%s,%s)", (name, content, sha))
            written += 1
        conn.commit()
    print(f"[{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}] "
          f"memory backup: {written} written, {skipped} unchanged, {len(files)} total")
    return 0


def restore(target: str) -> int:
    os.makedirs(target, exist_ok=True)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (file_name) file_name, content, backed_up_at "
            "FROM console_memory_backup ORDER BY file_name, backed_up_at DESC")
        rows = cur.fetchall()
    for name, content, ts in rows:
        safe = os.path.basename(name)
        with open(os.path.join(target, safe), "w", encoding="utf-8") as f:
            f.write(content)
        print(f"restored {safe} (as of {ts:%Y-%m-%d %H:%M}Z)")
    print(f"{len(rows)} files restored to {target}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", metavar="DIR")
    a = ap.parse_args()
    sys.exit(restore(a.restore) if a.restore else backup())
