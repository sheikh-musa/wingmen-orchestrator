#!/usr/bin/env python3
"""snapshot_live_checks.py — snapshot a live table's CHECK constraints + triggers so test
fixtures are GENERATED from the real definitions instead of hand-copied (orch-console #79986).

Why: PR#584's first version passed 339 tests and would still have failed live, because the
hand-written operator_asks fixture lacked mig127's operator_asks_bus_evidence_backfill_only_chk.
A hand-copied fixture drifts silently; this makes the drift a failing check.

READ-ONLY by construction: the session is opened read_only and only reads pg_catalog.

Usage:
  snapshot_live_checks.py [--table operator_asks] --write   # refresh tests/fixtures/schema/<t>.live.json
  snapshot_live_checks.py [--table operator_asks] --check   # exit 1 + diff if live != snapshot

The fixture side (tests/conftest.py operator_ledger_db) applies every check and every
trigger in the snapshot except the explicitly listed, explicitly justified NOT_MIRRORED ones,
and tests/test_operator_asks_fixture_parity.py asserts the fixture equals the snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = REPO / "tests" / "fixtures" / "schema"


def snapshot_path(table: str) -> Path:
    return SNAPSHOT_DIR / f"{table}.live.json"


def read_live(dsn: str, table: str) -> dict:
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            reg = f"public.{table}"
            cur.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = %s::regclass AND contype = 'c' ORDER BY conname", (reg,))
            checks = {name: d for name, d in cur.fetchall()}
            cur.execute("SELECT t.tgname, pg_get_triggerdef(t.oid), p.proname, pg_get_functiondef(p.oid) "
                        "FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
                        "WHERE t.tgrelid = %s::regclass AND NOT t.tgisinternal ORDER BY t.tgname", (reg,))
            triggers = {name: {"def": tdef, "function": fname, "function_def": fdef}
                        for name, tdef, fname, fdef in cur.fetchall()}
    return {"table": table, "checks": checks, "triggers": triggers}


def diff(live: dict, snap: dict) -> list:
    out = []
    for kind in ("checks", "triggers"):
        a, b = live.get(kind, {}), snap.get(kind, {})
        for k in sorted(set(a) | set(b)):
            if k not in b:
                out.append(f"{kind}: {k} is LIVE but not in the snapshot")
            elif k not in a:
                out.append(f"{kind}: {k} is in the snapshot but no longer LIVE")
            elif a[k] != b[k]:
                out.append(f"{kind}: {k} definition differs (live vs snapshot)")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", default="operator_asks")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    load_dotenv(REPO / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.stderr.write("error: no DATABASE_URL/SUPABASE_DB_URL\n")
        return 2
    live = read_live(dsn, a.table)
    path = snapshot_path(a.table)
    if a.write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
        print(f"wrote {path.relative_to(REPO)}: {len(live['checks'])} checks, {len(live['triggers'])} triggers")
        return 0
    if not path.exists():
        sys.stderr.write(f"error: no snapshot at {path} — run --write first\n")
        return 2
    d = diff(live, json.loads(path.read_text()))
    if d:
        sys.stderr.write(f"DRIFT: live {a.table} != {path.relative_to(REPO)} (refresh with --write, "
                         f"then make the fixture/tests pass):\n  " + "\n  ".join(d) + "\n")
        return 1
    print(f"ok: live {a.table} matches the snapshot ({len(live['checks'])} checks, {len(live['triggers'])} triggers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
