#!/usr/bin/env python3
"""apply_migration.py — the ONE generic migration applier (Phase 3 item 3, op#19103).

Replaces the 44 one-off `apply_*.py` scripts (6 of which already write
migration_ledger by hand, 38 of which don't) with a single tool that ALWAYS
ledgers, in the same transaction as the DDL, and refuses rather than guesses
on every ambiguous case. Direct psycopg apply only — NEVER `supabase db push`
(decision 962 / CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS: the CLI's shadow-diff
path silently strips later view arms).

Contract (per the work order, msg 37652 item 3):
  (a) refuses if (repo, sha256) is already ledgered under a DIFFERENT name —
      a migration cannot be renamed and reapplied as if new.
  (b) requires a `-- ledger: silo=<ref>` header line in the .sql — the file
      declares which store it targets; this is checked against --silo before
      anything touches the database (LAYER-VOCAB-001: name the store).
  (c) writes migration_ledger in the SAME transaction as the apply — an
      applied-but-unledgered migration and a ledgered-but-unapplied row are
      both treated as equally wrong.
  (d) never uses `supabase db push` — this module only ever opens a direct
      psycopg connection and runs the file's own SQL.

Usage:
  python scripts/apply_migration.py <NNN|path> --silo <ref> [--dsn <url>] [--dry-run]
  python scripts/apply_migration.py <NNN|path> --silo <ref> --status

<NNN|path>: either a numeric prefix (looked up as migrations/<NNN>_*.sql) or
an explicit path to a .sql file (tests pass explicit paths).

--dsn defaults to $DATABASE_URL. --silo is a residency assertion, checked
against BOTH the file's `-- ledger:` header and the resolved DSN (the ref
string must appear in the DSN) before anything runs — a mismatch on either
side refuses.

--dry-run runs the migration body inside a transaction, verifies it applies
cleanly, then ROLLS BACK (no ledger row written, nothing committed).
--status only reads migration_ledger; touches nothing else.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"
DEFAULT_REPO = "orchestrator"

_LEDGER_HEADER_RE = re.compile(r"^--\s*ledger:\s*silo=(\S+)\s*$", re.MULTILINE)
# BEGIN/COMMIT are stripped (we supply our own transaction). Any OTHER top-level
# transaction-control statement is refused outright rather than silently dropped —
# a migration that ROLLBACKs or nests transactions is not safe to auto-wrap.
_STRIPPABLE_TXN_CTL = {"BEGIN;", "COMMIT;"}
_FORBIDDEN_TXN_CTL = {"ROLLBACK;", "START TRANSACTION;", "END TRANSACTION;", "END;"}


class Refuse(Exception):
    """Raised for any condition that must stop the apply before it mutates anything."""


def resolve_migration_path(arg: str, migrations_dir: Path = MIGRATIONS_DIR) -> Path:
    """A literal existing path wins; otherwise treat arg as a numeric prefix."""
    literal = Path(arg)
    if literal.exists():
        return literal
    if not re.fullmatch(r"\d+", arg):
        raise Refuse(f"not a path and not a bare numeric prefix: {arg!r}")
    matches = sorted(migrations_dir.glob(f"{arg}_*.sql"))
    if not matches:
        raise Refuse(f"no migrations/{arg}_*.sql found under {migrations_dir}")
    if len(matches) > 1:
        raise Refuse(f"ambiguous prefix {arg!r} — matches {[m.name for m in matches]}")
    return matches[0]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_ledger_header(sql_text: str) -> str:
    """Return the declared silo ref from a `-- ledger: silo=<ref>` line, or refuse."""
    m = _LEDGER_HEADER_RE.search(sql_text)
    if not m:
        raise Refuse(
            "missing required '-- ledger: silo=<ref>' header line — every migration "
            "this tool applies must declare its target store."
        )
    return m.group(1)


def strip_txn_control(sql_text: str) -> str:
    """Strip top-level BEGIN/COMMIT so a self-committing migration can't escape our
    own transaction and ledger-atomicity guarantee (see the self-committing-migration
    finding: a `\\i`-ed inner BEGIN/COMMIT otherwise breaks caller ROLLBACK). Any other
    top-level transaction-control statement (ROLLBACK, nested BEGIN, ...) refuses."""
    kept = []
    for ln in sql_text.splitlines():
        token = ln.strip().upper()
        if token in _STRIPPABLE_TXN_CTL:
            continue
        if token in _FORBIDDEN_TXN_CTL:
            raise Refuse(f"top-level transaction-control statement survived the strip: {ln!r}")
        kept.append(ln)
    return "\n".join(kept)


def check_residency(dsn: str, silo: str, header_silo: str) -> None:
    if header_silo != silo:
        raise Refuse(
            f"--silo {silo!r} does not match the file's '-- ledger: silo={header_silo}' header "
            f"— refusing (residency guard)."
        )
    if silo not in dsn:
        raise Refuse(f"resolved DSN does not contain silo ref {silo!r} — refusing (residency guard).")


def check_ledger_collision(cur, repo: str, name: str, silo: str, sha: str) -> str:
    """Returns 'new' | 'already_applied'. Raises Refuse on any drift/rename case."""
    cur.execute(
        "SELECT migration_name, sha256 FROM migration_ledger WHERE repo=%s AND silo_ref=%s AND sha256=%s",
        (repo, silo, sha),
    )
    same_content = cur.fetchall()
    for other_name, _ in same_content:
        if other_name != name:
            raise Refuse(
                f"content sha256={sha[:12]}… is already ledgered as {other_name!r} in {silo} — "
                f"a migration cannot be renamed and reapplied as new."
            )

    cur.execute(
        "SELECT sha256 FROM migration_ledger WHERE repo=%s AND silo_ref=%s AND migration_name=%s",
        (repo, silo, name),
    )
    row = cur.fetchone()
    if row is None:
        return "new"
    existing_sha = row[0]
    if existing_sha != sha:
        raise Refuse(
            f"{name} is already ledgered in {silo} with sha256={existing_sha[:12]}… but the file on "
            f"disk hashes to {sha[:12]}… — the applied migration and the tracked file have drifted; "
            f"refusing rather than guessing which is truth."
        )
    return "already_applied"


def apply_migration(
    dsn: str,
    path: Path,
    *,
    silo: str,
    repo: str = DEFAULT_REPO,
    dry_run: bool = False,
    applied_by: str = "apply_migration.py",
) -> dict:
    sql_text = path.read_text()
    header_silo = parse_ledger_header(sql_text)
    check_residency(dsn, silo, header_silo)
    sha = file_sha256(path)
    body = strip_txn_control(sql_text)

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        state = check_ledger_collision(cur, repo, path.name, silo, sha)
        if state == "already_applied":
            conn.rollback()
            return {"status": "already_applied", "migration": path.name, "silo": silo, "sha256": sha}

        cur.execute(body)
        cur.execute(
            """INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by)
               VALUES (%s, %s, %s, %s, %s)""",
            (repo, path.name, silo, sha, applied_by),
        )

        if dry_run:
            conn.rollback()
            return {"status": "dry_run_ok", "migration": path.name, "silo": silo, "sha256": sha}

        conn.commit()
        return {"status": "applied", "migration": path.name, "silo": silo, "sha256": sha}


def status(dsn: str, path: Path, *, silo: str, repo: str = DEFAULT_REPO) -> dict | None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT migration_name, silo_ref, sha256, applied_at, applied_by, note
               FROM migration_ledger WHERE repo=%s AND silo_ref=%s AND migration_name=%s""",
            (repo, silo, path.name),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cols = ["migration_name", "silo_ref", "sha256", "applied_at", "applied_by", "note"]
    return dict(zip(cols, row))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("migration", help="numeric prefix (NNN) or explicit path to a .sql file")
    p.add_argument("--silo", required=True, help="project ref this migration must target (residency guard)")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    args = p.parse_args(argv)

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        print("✗ no DSN: pass --dsn or set DATABASE_URL", file=sys.stderr)
        return 2

    try:
        path = resolve_migration_path(args.migration)
        if args.status:
            row = status(dsn, path, silo=args.silo, repo=args.repo)
            if row is None:
                print(f"NOT LEDGERED: {path.name} in {args.silo}")
                return 1
            print(f"LEDGERED: {row}")
            return 0

        result = apply_migration(dsn, path, silo=args.silo, repo=args.repo, dry_run=args.dry_run)
        print(f"✓ {result['status']}: {result['migration']} ({args.silo}, sha256 {result['sha256'][:12]}…)")
        return 0
    except Refuse as e:
        print(f"✗ REFUSE (did not apply): {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
