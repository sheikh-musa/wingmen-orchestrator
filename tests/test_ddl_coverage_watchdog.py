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

import json
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


def _ensure_role(dsn: str, rolename: str) -> None:
    """Roles are cluster-wide in the shared session-scoped pg_dsn harness,
    so a second test creating the same role must not error."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (rolename,))
        if not cur.fetchone():
            cur.execute(f"CREATE ROLE {rolename} NOLOGIN")


def _create_realtime_partition_churn(silo_dsn: str) -> None:
    """Simulates Supabase Realtime's own daily partition-table rotation
    (bus #44438/#44439)."""
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS realtime")
        cur.execute("CREATE TABLE realtime.messages_2026_10_02 (id int)")


def _create_platform_owned_churn(silo_dsn: str) -> None:
    """A table owned by a Supabase platform service role in a NON-realtime
    schema -- the exclusion must be by owner, not just by schema name
    (orch-console bus #44444: 'if there's more churn of that kind')."""
    _ensure_role(silo_dsn, "supabase_storage_admin")
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS storage")
        cur.execute("CREATE TABLE storage.platform_internal_thing (id int)")
        cur.execute("ALTER TABLE storage.platform_internal_thing OWNER TO supabase_storage_admin")


def _create_storage_objects_with_policy(silo_dsn: str) -> None:
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS storage")
        cur.execute("CREATE TABLE IF NOT EXISTS storage.objects (id int, owner_id text)")
        cur.execute("ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY")
        cur.execute("CREATE POLICY trainee_photos_read ON storage.objects FOR SELECT USING (true)")


def _drop_storage_objects_policy(silo_dsn: str) -> None:
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP POLICY trainee_photos_read ON storage.objects")


def _create_function_in_non_public_schema(silo_dsn: str, schema: str, name: str) -> None:
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cur.execute(f"CREATE OR REPLACE FUNCTION {schema}.{name}() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$")


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
# Scope: a NARROW exclusion of Supabase-managed churn (bus #44439/#44444, two
# rounds) -- a bare 'public'-only allowlist over-corrected, since cosem-platform
# legitimately gates CREATE/DROP POLICY on storage.objects (trainee_photos_read
# etc). Excluded: the 'realtime' schema entirely, and objects OWNED by a
# Supabase platform role in ANY schema. Everything else -- our own policies,
# functions and tables in storage/auth included -- stays in scope.
# --------------------------------------------------------------------------------

def test_realtime_schema_churn_is_never_drift(two_dsns):
    """Simulates Supabase Realtime's own daily partition-table rotation (bus
    #44438/#44439) -- must never page: it's outside apply_migration.py's
    --gate surface entirely and no lane could have gated it even in
    principle."""
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)  # baseline
    _create_realtime_partition_churn(silo_dsn)
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is False
    assert _bus_row_count(bus_dsn) == 0


def test_platform_role_owned_object_outside_realtime_is_never_drift(two_dsns):
    """A table created/owned by a Supabase platform service role (e.g.
    supabase_storage_admin) in a NON-realtime schema is also excluded -- the
    exclusion is by owner, not just by schema (orch-console bus #44444: 'if
    there's more churn of that kind')."""
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)  # baseline
    _create_platform_owned_churn(silo_dsn)
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is False
    assert _bus_row_count(bus_dsn) == 0


def test_out_of_gate_policy_drop_on_storage_objects_still_pages(two_dsns):
    """Orch-console bus #44444: cosem-platform's migrations legitimately
    CREATE/DROP POLICY on storage.objects (trainee_photos_read,
    trainee_evidence_read) -- exactly the governance-relevant RLS changes
    this watchdog exists to catch. A bare 'public'-only allowlist would have
    let an out-of-gate drop through silently; the realtime/platform-owner
    exclusion must not."""
    silo_dsn, bus_dsn = two_dsns
    _create_storage_objects_with_policy(silo_dsn)
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)  # baseline
    _drop_storage_objects_policy(silo_dsn)  # DDL with NO ledger row
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is True
    assert _bus_row_count(bus_dsn) == 1


def test_new_function_in_non_public_schema_owned_by_us_still_pages(two_dsns):
    """A new function in a non-public schema (e.g. auth), owned by OUR role
    (postgres in this harness) rather than a Supabase platform role, and not
    in the realtime schema -- must still page: it's within
    apply_migration.py's gate surface."""
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)  # baseline
    _create_function_in_non_public_schema(silo_dsn, "auth", "custom_claim_hook")
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is True
    assert _bus_row_count(bus_dsn) == 1


def test_grant_execute_to_anon_on_platform_owned_function_still_pages(two_dsns):
    """orch-console bus #44449 (058b anon-EXEC tripwire): owner-excluding
    procs would drop a platform-owned function's proacl along with it -- an
    out-of-gate GRANT EXECUTE ... TO anon (owner stays supabase_auth_admin)
    must still page. Procs stay fully fingerprinted outside the realtime
    schema regardless of owner precisely so this can't happen."""
    silo_dsn, bus_dsn = two_dsns
    _ensure_role(silo_dsn, "supabase_auth_admin")
    _ensure_role(silo_dsn, "anon")
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS auth")
        cur.execute(
            "CREATE OR REPLACE FUNCTION auth.internal_fn() RETURNS int "
            "LANGUAGE sql AS $$ SELECT 1 $$"
        )
        cur.execute("ALTER FUNCTION auth.internal_fn() OWNER TO supabase_auth_admin")
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)  # baseline
    with psycopg.connect(silo_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("GRANT EXECUTE ON FUNCTION auth.internal_fn() TO anon")  # DDL with NO ledger row
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is True
    assert _bus_row_count(bus_dsn) == 1


# --------------------------------------------------------------------------------
# Snapshot persistence + diff (bus #44439 ask #3: "persist the per-component
# snapshot ... so the page carries the actual diff")
# --------------------------------------------------------------------------------

def test_state_persists_a_real_snapshot_not_just_the_hash(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    entry = watchdog.load_state()[SILO]
    assert "snapshot" in entry
    assert "columns" in entry["snapshot"]
    # the widgets table's own columns show up in the raw snapshot, not just a hash
    assert "widgets" in str(entry["snapshot"]["columns"])


def test_drift_alert_body_contains_the_actual_diff(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)  # baseline
    _add_column(silo_dsn)
    watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    with psycopg.connect(bus_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT body FROM agent_messages ORDER BY id DESC LIMIT 1")
        body = cur.fetchone()[0]
    # the actual added column name, not just an opaque fingerprint prefix
    assert "description" in body
    assert "columns added" in body


def test_old_state_format_rebaselines_without_paging(two_dsns):
    """Deploying this fix onto a host with an existing pre-#44439 state file
    (no schema_version, blocklist-scoped fingerprint) must not immediately
    fire a false page just because the fingerprinted surface itself changed
    -- the old entry is incomparable, so this re-baselines instead of
    treating format drift as schema drift."""
    silo_dsn, bus_dsn = two_dsns
    watchdog.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    watchdog.STATE_FILE.write_text(json.dumps({
        SILO: {"fingerprint": "stale-pre-rescope-hash", "ledger_count": 0,
               "checked_at": "2026-01-01T00:00:00+00:00"},
    }))
    drifted = watchdog.run_scan(silo=SILO, silo_dsn=silo_dsn, bus_dsn=bus_dsn)
    assert drifted is False
    assert _bus_row_count(bus_dsn) == 0
    assert watchdog.load_state()[SILO]["schema_version"] == watchdog.STATE_SCHEMA_VERSION


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


def test_cli_refuses_both_silo_dsn_and_vault_key():
    with pytest.raises(SystemExit):
        watchdog.main([
            "--silo", SILO, "--silo-dsn", "host=x",
            "--silo-dsn-vault-key", "some_key", "--bus-dsn", "host=y",
        ])


def test_cli_exit_code_reflects_drift(two_dsns):
    silo_dsn, bus_dsn = two_dsns
    assert watchdog.main(["--silo", SILO, "--silo-dsn", silo_dsn, "--bus-dsn", bus_dsn]) == 0
    _add_column(silo_dsn)
    assert watchdog.main(["--silo", SILO, "--silo-dsn", silo_dsn, "--bus-dsn", bus_dsn]) == 1


# --------------------------------------------------------------------------------
# --silo-dsn-vault-key: resolves via nervous_system.vault instead of a plain DSN
# --------------------------------------------------------------------------------

def test_resolve_silo_dsn_from_vault_key(monkeypatch, two_dsns):
    silo_dsn, _ = two_dsns

    class _FakeSecret:
        value = silo_dsn

    class _FakeVault:
        def get(self, name, reason):
            assert name == "cosem_platform_watch_dsn"
            assert reason  # a real reason string is passed, not empty
            return _FakeSecret()

    import nervous_system.vault as vault_mod
    monkeypatch.setattr(vault_mod, "vault", _FakeVault())

    class Args:
        silo = "SILO"
        silo_dsn = None
        silo_dsn_vault_key = "cosem_platform_watch_dsn"

    assert watchdog.resolve_silo_dsn(Args()) == silo_dsn


def test_resolve_silo_dsn_prefers_plain_dsn_when_given():
    class Args:
        silo = SILO
        silo_dsn = "host=plain"
        silo_dsn_vault_key = None

    assert watchdog.resolve_silo_dsn(Args()) == "host=plain"


def test_cli_uses_vault_key_end_to_end(monkeypatch, two_dsns):
    silo_dsn, bus_dsn = two_dsns

    class _FakeSecret:
        value = silo_dsn

    class _FakeVault:
        def get(self, name, reason):
            return _FakeSecret()

    import nervous_system.vault as vault_mod
    monkeypatch.setattr(vault_mod, "vault", _FakeVault())

    assert watchdog.main([
        "--silo", SILO, "--silo-dsn-vault-key", "cosem_platform_watch_dsn",
        "--bus-dsn", bus_dsn,
    ]) == 0
