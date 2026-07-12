#!/usr/bin/env python3
"""migration_immutability_guard.py — gate for MIGRATION-1 (CAI-RESP-420 #50).

Bans the in-place amendment of an already-applied migration — the exact fault that
diverged 061 -> 092 (migration body edited AFTER fan-out, so goumlyne got the old
body). Content-hash of each migration file is recorded per-silo on apply; any later
divergence between the file body and the recorded hash is a HARD FAIL.

Two enforcement points (per CAI-RESP-420 Q-C, priority order):
  1. FIRST — the orchestrator apply path: scripts/apply_sql_migration.py (and
     apply_021) call check_one() BEFORE applying (refuse a changed-body re-apply)
     and record() AFTER (register the hash). This is the last line before a write.
  2. THEN — a CI check in each product repo that owns silo migrations (ihsanos
     first): assert_repo() over the repo's migrations/ dir; red PR = amended body.

Pure content-hash + a single ledger table read/write. No silo mutation, no bridge.

    python3 scripts/gates/migration_immutability_guard.py --check --repo orchestrator \
        --dir migrations --silo tscuymavysscrvoberrr        # CI / pre-apply (exit 1 on violation)
    python3 scripts/gates/migration_immutability_guard.py --record --repo orchestrator \
        --migration 021_x.sql --file migrations/021_x.sql --silo tscuymavysscrvoberrr
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ORCH = Path(__file__).resolve().parent.parent.parent
load_dotenv(ORCH / ".env")


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ledger_exists(conn) -> bool:
    # to_regclass returns NULL (not an error) if the table is absent, so it never
    # poisons the caller's transaction.
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.migration_ledger')")
        return cur.fetchone()[0] is not None


def _recorded(conn, repo: str, name: str, silo_ref: str) -> str | None:
    """Recorded hash for (repo, name, silo), or None if this migration was never
    applied (NEW). FAILS CLOSED — raises LedgerUnavailable if the ledger table is
    absent or the read errors, so the caller cannot proceed unverified."""
    if not _ledger_exists(conn):
        raise LedgerUnavailable(
            "migration_ledger table not present — cannot verify migration immutability")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sha256 FROM migration_ledger WHERE repo=%s AND migration_name=%s AND silo_ref=%s",
                (repo, name, silo_ref))
            row = cur.fetchone()
    except psycopg.Error as e:
        raise LedgerUnavailable(f"migration_ledger read failed: {e}") from e
    return row[0] if row else None


class ImmutabilityViolation(RuntimeError):
    """An already-applied migration's file body changed — the banned in-place amend."""


class LedgerUnavailable(RuntimeError):
    """The migration_ledger cannot be read (table absent / query error). The guard
    FAILS CLOSED on this: the apply path must ABORT, never silently skip the check.
    The ONLY sanctioned exception is bootstrapping the ledger-creating migration
    itself, via the explicit allow_missing_ledger flag."""


def check_one(conn, repo: str, name: str, silo_ref: str, path: str | Path,
              allow_missing_ledger: bool = False) -> str | None:
    """Return None if OK (a NEW migration with no ledger row, or an unchanged
    body); raise ImmutabilityViolation if the recorded hash differs from the
    current file (a MUTATED applied migration). Raise LedgerUnavailable (FAIL
    CLOSED) if the ledger can't be read — unless allow_missing_ledger (bootstrap
    of the ledger migration itself). Called BEFORE applying; the apply MUST abort
    on any raise."""
    cur_hash = sha256_file(path)
    try:
        rec = _recorded(conn, repo, name, silo_ref)   # None => never applied (NEW)
    except LedgerUnavailable:
        if allow_missing_ledger:
            return None       # documented bootstrap: this migration CREATES the ledger
        raise                 # FAIL CLOSED — cannot verify, refuse to apply
    if rec is not None and rec != cur_hash:
        raise ImmutabilityViolation(
            f"{repo}/{name} @ {silo_ref}: applied body hash {rec[:12]} != current file "
            f"{cur_hash[:12]} — an applied migration was AMENDED IN PLACE (the 061->092 fault). "
            f"Add a NEW migration; never edit an applied one.")
    return None


def record(conn, repo: str, name: str, silo_ref: str, path: str | Path,
           applied_by: str = "cc-infra") -> None:
    """Record the hash of a migration as applied. Refuses (raises) if a DIFFERENT
    body is already recorded — recording a changed body is itself the violation."""
    cur_hash = sha256_file(path)
    rec = _recorded(conn, repo, name, silo_ref)
    if rec is not None and rec != cur_hash:
        raise ImmutabilityViolation(
            f"{repo}/{name} @ {silo_ref}: refusing to record changed body (recorded {rec[:12]}, "
            f"current {cur_hash[:12]}).")
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-infra',true)")
        cur.execute(
            "INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (repo, migration_name, silo_ref) DO NOTHING",
            (repo, name, silo_ref, cur_hash, applied_by))
        conn.commit()


def assert_repo(repo: str, migrations_dir: str | Path, silo_ref: str, dsn: str | None = None) -> list:
    """Check every *.sql in a repo's migrations dir against the ledger for one silo.
    Returns a list of violation dicts (empty = clean). New/unrecorded files pass."""
    violations = []
    with psycopg.connect(_dsn(dsn)) as conn:
        for path in sorted(Path(migrations_dir).glob("*.sql")):
            name = path.name
            cur_hash = sha256_file(path)
            rec = _recorded(conn, repo, name, silo_ref)
            if rec is not None and rec != cur_hash:
                violations.append({"migration": name, "silo_ref": silo_ref,
                                   "recorded": rec, "current": cur_hash})
    return violations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="CI/pre-apply: hard-fail on any amended body")
    ap.add_argument("--record", action="store_true", help="record a migration's hash as applied")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--silo", required=True, help="project ref")
    ap.add_argument("--dir", default="migrations")
    ap.add_argument("--migration")
    ap.add_argument("--file")
    ap.add_argument("--by", default="cc-infra")
    a = ap.parse_args()
    if a.check:
        try:
            v = assert_repo(a.repo, a.dir, a.silo)
        except LedgerUnavailable as e:
            print(f"FAIL CLOSED — ledger unavailable, cannot verify immutability: {e}", file=sys.stderr)
            return 1
        if v:
            print(f"IMMUTABILITY VIOLATION — {len(v)} amended migration(s) in {a.repo} @ {a.silo}:")
            for x in v:
                print(f"  {x['migration']}: recorded {x['recorded'][:12]} != current {x['current'][:12]}")
            return 1
        print(f"OK — {a.repo} migrations immutable vs ledger @ {a.silo}")
        return 0
    if a.record:
        if not (a.migration and a.file):
            print("--record needs --migration and --file", file=sys.stderr)
            return 2
        with psycopg.connect(_dsn()) as conn:
            record(conn, a.repo, a.migration, a.silo, a.file, a.by)
        print(f"recorded {a.repo}/{a.migration} @ {a.silo}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
