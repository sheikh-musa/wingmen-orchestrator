#!/usr/bin/env python3
"""ddl_coverage_watchdog.py — detect-only backstop for apply_migration.py's
known gap (b) (op#22669 item 3, bus #44135/#44139/#44140).

THE PROBLEM: apply_migration.py's --gate enforcement only fires for applies
that go THROUGH apply_migration.py. Nothing stops a lane from running DDL
against a PRODUCTION_SILOS member directly (raw psql/DSN) instead — bus
#44135 confirmed this live: cc-cosem-platform applied a migration against
the cosem-platform production store `ywrpttpxwfcoodovxhsr` (docs/data-
store-registry.md; LAYER-VOCAB-001 — this is the REAL production silo, not
the repo's demo/dev project) outside the tool, with a gate row on record
saying how to do it correctly. No ledger row, no gate check.

ORIGINAL Phase-1 design proposed a DDL event trigger, but CREATE EVENT
TRIGGER is database-wide and typically superuser-gated — Supabase's
connection role is usually not a true superuser, so it may be refused.
Per orch-console's ruling (bus #44140), this uses a FALLBACK needing no
special privilege instead: a schema fingerprint (a hash over
information_schema.columns + pg_policies + pg_proc signatures/ACLs +
pg_indexes) is snapshotted per silo and diffed against the previous
snapshot on every scan. A fingerprint change with NO new migration_ledger
row in that SAME silo since the last scan is unledgered drift -> page.

Detect-only, same shape as nervous_system/ingest.py's pinned-channel-drift
check: page-once-EVER via a durable bus-row marker (survives a daemon
restart; a state-file flag would not), never costs the caller's loop an
exception, never touches the silo beyond read-only catalog queries.

DSN separation (the #1 correctness requirement here): the silo being
fingerprinted and the fleet bus (where paging lands) are almost always
DIFFERENT databases — watching ywrpttpxwfcoodovxhsr must still page
orch-console on the SUBSTRATE bus, tscuymavysscrvoberrr, never ywrpt
itself. Both DSNs are REQUIRED CLI arguments with no ambient-env fallback
(no $DATABASE_URL guessing) so this can never silently page into the wrong
database the way an ambient-env mixup would — mirrors bus_send.py's
"--priority has no default" discipline (op#22517). The one exception:
watching the substrate itself, where the two DSNs are naturally the same
value.

Registers its own identity in `agents` via migrations/074_ddl_coverage_
watchdog_agent.sql BEFORE this ever runs live — applying bus #44035's
lesson (ingest-watchdog's own first page hit agent_messages_from_agent_fkey
because no migration had ever created that row).

Usage:
    scripts/ddl_coverage_watchdog.py --silo <ref> --silo-dsn <dsn> \\
        --bus-dsn <dsn> [--once] [--dry-run]

State: a local JSON file (logs/ddl_coverage_watchdog_state.json), keyed by
silo ref -- {fingerprint, ledger_count, checked_at}. The FIRST scan for a
silo only establishes a baseline; it never pages (no prior state to diff
against, same "clean boot" shape as every other watchdog in this fleet).

KNOWN LIMITATION (orch-console, bus #44153): the ledger check is a COUNT
delta (did migration_ledger grow since the last scan), not a per-event
correlation. A legitimately ledgered migration and an unledgered raw DDL
landing in the SAME scan window both show up as "ledger grew" -- the
unledgered one hides behind the legitimate one and this scan reports clean.
Mitigation: keep scans frequent (e.g. every 10 min) to shrink the window a
masking pair could land in. Real fix (not built here, P3 follow-up):
correlate migration_ledger.applied_at against the specific catalog rows
that changed, so two co-occurring changes can't hide each other.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ORCH = Path(__file__).resolve().parent.parent
STATE_FILE = ORCH / "logs" / "ddl_coverage_watchdog_state.json"

PAGE_FROM_AGENT = "ddl-coverage-watchdog"  # mirrors ingest.py's PAGE_FROM_AGENT convention
PAGE_TO_AGENT = "orch-console"

_COLUMNS_SQL = """
    SELECT table_schema, table_name, column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY table_schema, table_name, ordinal_position
"""

_POLICIES_SQL = """
    SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
    FROM pg_policies
    ORDER BY schemaname, tablename, policyname
"""

_PROCS_SQL = """
    SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), p.proacl
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY n.nspname, p.proname, pg_get_function_identity_arguments(p.oid)
"""

_INDEXES_SQL = """
    SELECT schemaname, tablename, indexname, indexdef
    FROM pg_indexes
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY schemaname, tablename, indexname
"""


def compute_schema_fingerprint(cur) -> str:
    """A hash over information_schema.columns + pg_policies + pg_proc
    signatures/ACLs + pg_indexes (orch-console bus #44140 spec). Read-only —
    needs no privilege beyond ordinary catalog SELECT access."""
    cur.execute(_COLUMNS_SQL)
    columns_part = cur.fetchall()
    cur.execute(_POLICIES_SQL)
    policies_part = cur.fetchall()
    cur.execute(_PROCS_SQL)
    procs_part = cur.fetchall()
    cur.execute(_INDEXES_SQL)
    indexes_part = cur.fetchall()
    payload = repr((columns_part, policies_part, procs_part, indexes_part)).encode()
    return hashlib.sha256(payload).hexdigest()


def _ledger_count(cur, silo: str) -> int:
    cur.execute("SELECT count(*) FROM migration_ledger WHERE silo_ref = %s", (silo,))
    return cur.fetchone()[0]


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def check_unledgered_schema_drift(cur, silo: str, state: dict) -> tuple[bool, str, dict]:
    """Returns (unledgered_drift, fingerprint, new_state_entry_for_this_silo).

    unledgered_drift is only ever True from the SECOND scan onward for a
    given silo -- the first scan has nothing to diff against and only
    establishes a baseline (never pages), same as every other watchdog's
    clean-boot behavior in this fleet."""
    fingerprint = compute_schema_fingerprint(cur)
    ledger_count = _ledger_count(cur, silo)
    new_entry = {
        "fingerprint": fingerprint,
        "ledger_count": ledger_count,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    prev = state.get(silo)
    if prev is None:
        return False, fingerprint, new_entry
    drifted = fingerprint != prev["fingerprint"]
    ledger_grew = ledger_count > prev.get("ledger_count", ledger_count)
    unledgered = drifted and not ledger_grew
    return unledgered, fingerprint, new_entry


def _page_once(bus_dsn: str, silo: str, fingerprint: str) -> None:
    """Page ONCE per (silo, fingerprint) -- durable dedup via a bus-row
    marker (survives a daemon restart), never a hand-written INSERT INTO
    agent_messages (CLAUDE.md / bus #43651)."""
    import psycopg

    marker = f"DDL-DRIFT:{silo}:{fingerprint[:12]}"
    with psycopg.connect(bus_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM agent_messages WHERE body LIKE %s LIMIT 1", (f"{marker}%",))
        if cur.fetchone():
            return
    from scripts import bus_send

    bus_send.send(
        from_agent=PAGE_FROM_AGENT, to=PAGE_TO_AGENT, mtype="blocker",
        subject=f"unledgered schema drift on silo {silo}",
        body=(f"{marker}: silo {silo}'s schema fingerprint (information_schema."
              f"columns + pg_policies + pg_proc signatures/ACLs + pg_indexes) "
              f"changed with NO new migration_ledger row in that silo since "
              f"the last scan -- a DDL apply bypassed apply_migration.py's "
              f"--gate entirely (op#22669 item 3 known gap, bus #44135 "
              f"precedent). Review what changed and whether it needs a "
              f"retroactive ledger entry. Page-once-ever for this exact "
              f"fingerprint; won't repeat unless the fingerprint changes again."),
        priority="P1", req=True, dsn=bus_dsn,
    )


def run_scan(*, silo: str, silo_dsn: str, bus_dsn: str, dry_run: bool = False) -> bool:
    """Returns True if unledgered drift was detected this scan. Never raises
    for a detected drift -- only for a genuine connection/query failure,
    which the caller (main/a cron wrapper) is expected to let surface loudly
    rather than swallow, since a watchdog that can't even connect needs a
    human, not a silent skip."""
    import psycopg

    state = load_state()
    with psycopg.connect(silo_dsn) as conn, conn.cursor() as cur:
        unledgered, fingerprint, new_entry = check_unledgered_schema_drift(cur, silo, state)

    if unledgered:
        print(f"WATCHDOG: silo {silo} schema drifted with no new migration_ledger row "
              f"(fingerprint {fingerprint[:12]})")
        if not dry_run:
            _page_once(bus_dsn, silo, fingerprint)
    else:
        print(f"WATCHDOG: silo {silo} clean (fingerprint {fingerprint[:12]})")

    if not dry_run:
        state[silo] = new_entry
        save_state(state)
    return unledgered


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--silo", required=True, help="project ref of the silo to fingerprint")
    p.add_argument("--silo-dsn", required=True, help="DSN of the silo being fingerprinted (never $DATABASE_URL implicitly)")
    p.add_argument("--bus-dsn", required=True, help="DSN of the fleet bus (substrate) -- where a page lands, NOT necessarily --silo-dsn")
    p.add_argument("--once", action="store_true", help="scan once and exit (default; reserved for a future --loop)")
    p.add_argument("--dry-run", action="store_true", help="detect + print, never pages, never writes state")
    args = p.parse_args(argv)

    drifted = run_scan(silo=args.silo, silo_dsn=args.silo_dsn, bus_dsn=args.bus_dsn, dry_run=args.dry_run)
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
