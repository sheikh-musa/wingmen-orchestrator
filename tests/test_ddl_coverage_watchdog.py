"""ddl_coverage_watchdog.py — detect-only backstop for apply_migration.py's
known gap (b): a DDL apply against a PRODUCTION_SILOS member that bypasses
--gate entirely by not going through the tool at all (op#22669 item 3, bus
#44135/#44139/#44140).

Exercises against the ephemeral PG17 harness (tests/conftest.py's pg_dsn) —
NEVER touches DATABASE_URL / any live silo. The silo being fingerprinted and
the bus being paged live in two SEPARATE databases within the same ephemeral
cluster, mirroring the real deployment shape (watching ywrpt still pages the
substrate bus, never ywrpt itself) and proving the two DSNs are never
conflated.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from scripts import ddl_coverage_watchdog as watchdog

SILO = "test-silo-ref"


@pytest.fixture
def two_dsns(pg_dsn):
    """(silo_dsn, bus_dsn) -- two independent databases in the one ephemeral
    cluster, so a test can prove fingerprinting and paging never cross.
    pg_dsn is session-scoped (shared across all tests), so each call uses
    uniquely-named databases -- the whole cluster is torn down at session
    end anyway, so no per-test DROP is needed."""
    tag = uuid.uuid4().hex[:8]
    silo_db, bus_db = f"silo_test_{tag}", f"bus_test_{tag}"
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE {silo_db}")
        cur.execute(f"CREATE DATABASE {bus_db}")
    silo_dsn = pg_dsn.replace("dbname=postgres", f"dbname={silo_db}")
    bus_dsn = pg_dsn.replace("dbname=postgres", f"dbname={bus_db}")

    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE migration_ledger (
                 repo text, migration_name text, silo_ref text, sha256 text,
                 applied_at timestamptz NOT NULL DEFAULT now(),
                 applied_by text, note text
               )"""
        )
        cur.execute("CREATE TABLE widgets (id int PRIMARY KEY, name text)")

    with psycopg.connect(bus_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE agents (
                 id text PRIMARY KEY, display_name text NOT NULL,
                 repo_scope text[] NOT NULL DEFAULT '{}',
                 status text NOT NULL DEFAULT 'idle', current_task text,
                 last_heartbeat timestamptz, created_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        cur.execute(
            """CREATE TABLE agent_messages (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 from_agent text NOT NULL REFERENCES agents(id),
                 to_agent text NOT NULL REFERENCES agents(id),
                 message_type text NOT NULL, subject text, body text, priority text,
                 requires_response boolean NOT NULL DEFAULT false,
                 thread_id uuid, created_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        cur.execute(
            "INSERT INTO agents (id, display_name) VALUES (%s, 'test'), (%s, 'test')",
            (watchdog.PAGE_FROM_AGENT, watchdog.PAGE_TO_AGENT),
        )
    return silo_dsn, bus_dsn


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "STATE_FILE", tmp_path / "state.json")


def _bus_row_count(bus_dsn: str) -> int:
    with psycopg.connect(bus_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        return cur.fetchone()[0]


def _add_column(silo_dsn: str) -> None:
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE widgets ADD COLUMN description text")


def _insert_ledger_row(silo_dsn: str) -> None:
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO migration_ledger (repo, migration_name, silo_ref, sha256, applied_by) "
            "VALUES ('test-repo', '999_test.sql', %s, 'deadbeef', 'apply_migration.py')",
            (SILO,),
        )


# --------------------------------------------------------------------------------
# compute_schema_fingerprint(): deterministic, sensitive to real schema shape
# --------------------------------------------------------------------------------

def test_fingerprint_is_stable_across_repeated_calls(two_dsns):
    silo_dsn, _ = two_dsns
    with psycopg.connect(silo_dsn) as conn, conn.cursor() as cur:
        a = watchdog.compute_schema_fingerprint(cur)
        b = watchdog.compute_schema_fingerprint(cur)
    assert a == b


def test_fingerprint_changes_on_added_column(two_dsns):
    silo_dsn, _ = two_dsns
    with psycopg.connect(silo_dsn) as conn, conn.cursor() as cur:
        before = watchdog.compute_schema_fingerprint(cur)
    _add_column(silo_dsn)
    with psycopg.connect(silo_dsn) as conn, conn.cursor() as cur:
        after = watchdog.compute_schema_fingerprint(cur)
    assert before != after


# --------------------------------------------------------------------------------
# run_scan(): first-scan baseline, drift detection, ledger-growth exoneration
# --------------------------------------------------------------------------------

def test_first_scan_establishes_baseline_never_pages(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is False
    assert _bus_row_count(bus_dsn) == 0
    assert watchdog.STATE_FILE.exists()


def test_schema_change_with_no_ledger_row_is_drift(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)  # baseline
    _add_column(silo_dsn)  # DDL with NO ledger row -- the op#22669 gap
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is True
    assert _bus_row_count(bus_dsn) == 1


def test_schema_change_with_matching_ledger_row_is_clean(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)  # baseline
    _add_column(silo_dsn)
    _insert_ledger_row(silo_dsn)  # simulates a normal apply_migration.py run
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is False
    assert _bus_row_count(bus_dsn) == 0


def test_no_schema_change_is_clean(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is False
    assert _bus_row_count(bus_dsn) == 0


def test_ledger_row_alone_with_no_schema_change_is_clean(two_dsns):
    """A migration that only inserts/updates data (no DDL) still ledgers --
    that must never be flagged as drift; nothing here penalizes a normal,
    ledgered DML-only apply."""
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    _insert_ledger_row(silo_dsn)
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is False
    assert _bus_row_count(bus_dsn) == 0


# --------------------------------------------------------------------------------
# Page-once-ever dedup (durable via bus marker, survives a daemon restart)
# --------------------------------------------------------------------------------

def test_repeated_drift_same_fingerprint_pages_only_once(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    _add_column(silo_dsn)
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert _bus_row_count(bus_dsn) == 1


def test_dedup_survives_a_fresh_state_file(two_dsns, tmp_path, monkeypatch):
    """A daemon restart wipes in-memory state but the bus marker persists --
    dedup must hold even with a brand-new state file (same shape as
    ingest.py's PAGE-once-ever proof)."""
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    _add_column(silo_dsn)
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert _bus_row_count(bus_dsn) == 1

    # simulate restart: fresh state file -> this scan looks like a new baseline
    # (no prior state), so it does NOT re-detect drift on this scan either way,
    # but confirms no duplicate page slips through regardless.
    monkeypatch.setattr(watchdog, "STATE_FILE", tmp_path / "state-after-restart.json")
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert _bus_row_count(bus_dsn) == 1


def test_different_silos_have_independent_dedup(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    other_silo = "other-silo-ref"
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    watchdog.run_scan(silo=other_silo, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    _add_column(silo_dsn)
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    watchdog.run_scan(silo=other_silo, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    # both silos independently detect the same underlying schema change --
    # two distinct pages, not deduped against each other.
    assert _bus_row_count(bus_dsn) == 2


# --------------------------------------------------------------------------------
# --dry-run: detect + print only, never pages, never persists state
# --------------------------------------------------------------------------------

def test_dry_run_never_pages(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn, dry_run=True)
    _add_column(silo_dsn)
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn, dry_run=True)
    # dry-run never persisted the baseline from the first call, so this
    # second dry-run call also sees "no prior state" and reports no drift --
    # proving dry-run truly writes nothing, not just "writes but doesn't page".
    assert drifted is False
    assert _bus_row_count(bus_dsn) == 0
    assert not watchdog.STATE_FILE.exists()


# --------------------------------------------------------------------------------
# CLI wiring: --silo-dsn / --bus-dsn are both required (no ambient $DATABASE_URL)
# --------------------------------------------------------------------------------

def test_cli_refuses_without_silo_dsn():
    with pytest.raises(SystemExit):
        watchdog.main(["--silo", SILO, "--bus-dsn", "host=x"])


def test_cli_refuses_without_bus_dsn():
    with pytest.raises(SystemExit):
        watchdog.main(["--silo", SILO, "--silo-dsn", "host=x"])


def test_cli_exit_code_reflects_drift(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    assert watchdog.main(["--silo", SILO, "--silo-dsn", silo_dsn, "--bus-dsn", bus_dsn]) == 0
    _add_column(silo_dsn)
    assert watchdog.main(["--silo", SILO, "--silo-dsn", silo_dsn, "--bus-dsn", bus_dsn]) == 1
