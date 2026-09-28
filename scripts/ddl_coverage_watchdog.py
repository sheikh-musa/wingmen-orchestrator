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

    # or, when the watching body doesn't hold the silo's DSN directly:
    scripts/ddl_coverage_watchdog.py --silo <ref> \\
        --silo-dsn-vault-key <vault secret name> --bus-dsn <dsn>

State: a local JSON file (logs/ddl_coverage_watchdog_state.json), keyed by
silo ref -- {schema_version, fingerprint, ledger_count, checked_at,
snapshot}, where snapshot is the raw per-component rows (bus #44439). The
FIRST scan for a silo, or the first scan after STATE_SCHEMA_VERSION bumps
(the fingerprinted surface itself changed), only establishes a baseline; it
never pages (no comparable prior state to diff against, same "clean boot"
shape as every other watchdog in this fleet).

KNOWN LIMITATION (orch-console, bus #44153): the ledger check is a COUNT
delta (did migration_ledger grow since the last scan), not a per-event
correlation. A legitimately ledgered migration and an unledgered raw DDL
landing in the SAME scan window both show up as "ledger grew" -- the
unledgered one hides behind the legitimate one and this scan reports clean.
Mitigation: keep scans frequent (e.g. every 10 min) to shrink the window a
masking pair could land in. Real fix (not built here, P3 follow-up):
correlate migration_ledger.applied_at against the specific catalog rows
that changed, so two co-occurring changes can't hide each other.

SCOPE: 'public' only, not "every non-system schema" (bus #44439 postmortem).
The original filter was `table_schema NOT IN ('pg_catalog', 'information_
schema')` -- a blocklist that still swept up Supabase's own platform-managed
schemas (realtime, auth, storage, vault, cron, extensions, graphql,
graphql_public, pgbouncer, supabase_migrations). Those rotate via Supabase's
OWN internal jobs, not apply_migration.py: confirmed live on the substrate
(tscuymavysscrvoberrr) at 2026-09-28T02:2x -- `realtime.messages_2026_10_01`
(+ its indexes) appeared, owned by `supabase_realtime_admin`, as part of
Realtime's routine rolling-window daily partition creation. No lane ran
that DDL, no lane COULD have gated it through apply_migration.py, and it
recurs roughly daily -- so scoping to "non-system" produced a page that
looked exactly like an unledgered gate-bypass but was actually inert
platform housekeeping (bus #44438/#44439). Every migration under
migrations/*.sql targets `public` (or an unqualified name, which defaults to
it) -- `public` is the ENTIRE surface apply_migration.py's --gate governs,
so it is the entire surface this watchdog needs to fingerprint.

SNAPSHOT PERSISTENCE (orch-console, bus #44439): the state file now keeps
the raw per-component rows (not just their hash) for the last-seen scan, so
a genuine drift page can name the actual added/removed rows instead of just
a fingerprint prefix nobody can act on without live archaeology.
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
    WHERE table_schema = 'public'
    ORDER BY table_schema, table_name, ordinal_position
"""

_POLICIES_SQL = """
    SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
    FROM pg_policies
    WHERE schemaname = 'public'
    ORDER BY schemaname, tablename, policyname
"""

_PROCS_SQL = """
    SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), p.proacl
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
    ORDER BY n.nspname, p.proname, pg_get_function_identity_arguments(p.oid)
"""

_INDEXES_SQL = """
    SELECT schemaname, tablename, indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = 'public'
    ORDER BY schemaname, tablename, indexname
"""

_SNAPSHOT_QUERIES = {
    "columns": _COLUMNS_SQL,
    "policies": _POLICIES_SQL,
    "procs": _PROCS_SQL,
    "indexes": _INDEXES_SQL,
}

# Bumped when the fingerprinted surface itself changes (e.g. the bus #44439
# blocklist->'public'-allowlist rescope) so an old, apples-to-oranges state
# entry is treated as "no prior state" -- a fresh baseline, not a page --
# instead of comparing snapshots taken over two different schema scopes.
STATE_SCHEMA_VERSION = 2


def compute_schema_snapshot(cur) -> dict[str, list[list]]:
    """Per-component raw rows for the 'public' schema (orch-console bus
    #44140 spec: information_schema.columns + pg_policies + pg_proc
    signatures/ACLs + pg_indexes). Read-only -- needs no privilege beyond
    ordinary catalog SELECT access. Rows are lists (not tuples) so the
    snapshot is JSON-serializable and can be persisted verbatim (bus #44439:
    "persist the per-component snapshot, not just the hash")."""
    snapshot: dict[str, list[list]] = {}
    for component, sql in _SNAPSHOT_QUERIES.items():
        cur.execute(sql)
        snapshot[component] = [list(row) for row in cur.fetchall()]
    return snapshot


def fingerprint_snapshot(snapshot: dict[str, list[list]]) -> str:
    payload = repr(tuple(snapshot[c] for c in _SNAPSHOT_QUERIES)).encode()
    return hashlib.sha256(payload).hexdigest()


def compute_schema_fingerprint(cur) -> str:
    """Convenience wrapper over compute_schema_snapshot + fingerprint_snapshot
    for callers that only need the hash."""
    return fingerprint_snapshot(compute_schema_snapshot(cur))


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


def _diff_snapshots(old: dict[str, list[list]], new: dict[str, list[list]]) -> dict[str, dict[str, list]]:
    """Per-component added/removed rows. Sorted by repr(), not the row's own
    values -- some columns (e.g. column_default) can be NULL, and Python 3
    can't order None against str, so sorting the raw tuples can raise."""
    diff: dict[str, dict[str, list]] = {}
    for component, new_rows in new.items():
        old_set = {tuple(r) for r in old.get(component, [])}
        new_set = {tuple(r) for r in new_rows}
        added = sorted((list(r) for r in new_set - old_set), key=repr)
        removed = sorted((list(r) for r in old_set - new_set), key=repr)
        if added or removed:
            diff[component] = {"added": added, "removed": removed}
    return diff


def check_unledgered_schema_drift(
    cur, silo: str, state: dict
) -> tuple[bool, str, dict, dict]:
    """Returns (unledgered_drift, fingerprint, new_state_entry_for_this_silo,
    diff_if_unledgered).

    unledgered_drift is only ever True from the SECOND comparable scan
    onward for a given silo -- the first scan (or a scan following a
    STATE_SCHEMA_VERSION bump, i.e. a rescope of what's fingerprinted) has
    nothing comparable to diff against and only establishes a fresh
    baseline (never pages), same as every other watchdog's clean-boot
    behavior in this fleet."""
    snapshot = compute_schema_snapshot(cur)
    fingerprint = fingerprint_snapshot(snapshot)
    ledger_count = _ledger_count(cur, silo)
    new_entry = {
        "schema_version": STATE_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "ledger_count": ledger_count,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": snapshot,
    }
    prev = state.get(silo)
    if prev is None or prev.get("schema_version") != STATE_SCHEMA_VERSION:
        return False, fingerprint, new_entry, {}
    drifted = fingerprint != prev["fingerprint"]
    ledger_grew = ledger_count > prev.get("ledger_count", ledger_count)
    unledgered = drifted and not ledger_grew
    diff = _diff_snapshots(prev.get("snapshot", {}), snapshot) if unledgered else {}
    return unledgered, fingerprint, new_entry, diff


def _format_diff(diff: dict[str, dict[str, list]]) -> str:
    if not diff:
        return ("(no row-level diff available -- prior state predates snapshot "
                "persistence or predates the current fingerprinted scope)")
    lines = []
    for component, changes in diff.items():
        for label, rows in (("added", changes.get("added", [])), ("removed", changes.get("removed", []))):
            if not rows:
                continue
            shown = "; ".join(str(r) for r in rows[:10])
            lines.append(f"  {component} {label} ({len(rows)}): {shown}")
            if len(rows) > 10:
                lines.append(f"    ... and {len(rows) - 10} more")
    return "\n".join(lines)


def _page_once(bus_dsn: str, silo: str, fingerprint: str, diff: dict[str, dict[str, list]]) -> None:
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
        body=(f"{marker}: silo {silo}'s public-schema fingerprint (information_"
              f"schema.columns + pg_policies + pg_proc signatures/ACLs + "
              f"pg_indexes, public schema only) changed with NO new "
              f"migration_ledger row in that silo since the last scan -- a DDL "
              f"apply bypassed apply_migration.py's --gate entirely (op#22669 "
              f"item 3 known gap, bus #44135 precedent). Review what changed "
              f"and whether it needs a retroactive ledger entry. Page-once-ever "
              f"for this exact fingerprint; won't repeat unless the fingerprint "
              f"changes again.\n\nDiff since last scan:\n{_format_diff(diff)}"),
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
        unledgered, fingerprint, new_entry, diff = check_unledgered_schema_drift(cur, silo, state)

    if unledgered:
        print(f"WATCHDOG: silo {silo} schema drifted with no new migration_ledger row "
              f"(fingerprint {fingerprint[:12]})")
        if not dry_run:
            _page_once(bus_dsn, silo, fingerprint, diff)
    else:
        print(f"WATCHDOG: silo {silo} clean (fingerprint {fingerprint[:12]})")

    if not dry_run:
        state[silo] = new_entry
        save_state(state)
    return unledgered


def resolve_silo_dsn(args) -> str:
    """--silo-dsn is a plain value; --silo-dsn-vault-key fetches it from
    nervous_system.vault at runtime (op#21338) instead -- for a lane being
    watched by a DIFFERENT body than the one that holds its DSN (e.g.
    cc-fleet-health watching ywrpttpxwfcoodovxhsr via a read-only DSN
    cc-cosem-platform put in the vault, bus #44153 item 2). Never both, and
    never neither -- enforced by the mutually-exclusive-required group in
    main()'s parser, not by convention."""
    if args.silo_dsn_vault_key:
        from nervous_system.vault import vault
        return vault.get(
            args.silo_dsn_vault_key,
            reason=f"ddl_coverage_watchdog scan of silo {args.silo}",
        ).value
    return args.silo_dsn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--silo", required=True, help="project ref of the silo to fingerprint")
    dsn_group = p.add_mutually_exclusive_group(required=True)
    dsn_group.add_argument("--silo-dsn", default=None, help="DSN of the silo being fingerprinted (never $DATABASE_URL implicitly)")
    dsn_group.add_argument("--silo-dsn-vault-key", default=None, help="fetch --silo-dsn from nervous_system.vault at runtime instead (e.g. a client silo's read-only DSN the watcher doesn't hold directly)")
    p.add_argument("--bus-dsn", required=True, help="DSN of the fleet bus (substrate) -- where a page lands, NOT necessarily --silo-dsn")
    p.add_argument("--once", action="store_true", help="scan once and exit (default; reserved for a future --loop)")
    p.add_argument("--dry-run", action="store_true", help="detect + print, never pages, never writes state")
    args = p.parse_args(argv)

    silo_dsn = resolve_silo_dsn(args)
    drifted = run_scan(silo=args.silo, silo_dsn=silo_dsn, bus_dsn=args.bus_dsn, dry_run=args.dry_run)
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
