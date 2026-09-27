"""Tests for apply_migration.py's --gate enforcement (op#22669/op#22521 item 3,
orch-console ruling bus #43869).

Incident: a fork applied migration 072 straight to the live substrate with no
gate at all. This closes that gap in CODE: a non-dry-run apply against a
PRODUCTION_SILOS member now requires a valid --gate <agent_messages id>.

Runs entirely against the ephemeral PG17 harness (tests/migrations/conftest.py).
NEVER touches DATABASE_URL / any live silo. The gate row is looked up via a
SEPARATE dsn (gate_dsn) exactly as production does (agent_messages lives on
the fleet substrate bus, not necessarily the target silo) -- here both
connections point at the same ephemeral instance, with a hand-rolled
agent_messages table standing in for the real bus.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

# A real production silo ref (docs/data-store-registry.md) -- the gate is only
# enforced for silos in am.PRODUCTION_SILOS, so the test silo must be a real one.
PROD_SILO = "tscuymavysscrvoberrr"


def _make_ledger_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE migration_ledger (
                 repo text NOT NULL,
                 migration_name text NOT NULL,
                 silo_ref text NOT NULL,
                 sha256 text NOT NULL,
                 applied_at timestamptz NOT NULL DEFAULT now(),
                 applied_by text,
                 note text,
                 PRIMARY KEY (repo, migration_name, silo_ref)
               )"""
        )


def _make_agent_messages_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE agent_messages (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 from_agent text NOT NULL,
                 to_agent text NOT NULL,
                 message_type text NOT NULL,
                 subject text,
                 body text,
                 priority text,
                 requires_response boolean NOT NULL DEFAULT false,
                 thread_id uuid,
                 created_at timestamptz NOT NULL DEFAULT now()
               )"""
        )


@pytest.fixture
def gated_db(fresh_db):
    # application_name=<ref> gives the residency guard (silo ref must appear in
    # the resolved DSN) something real to check, exactly like test_apply_migration.py.
    dsn = f"{fresh_db} application_name={PROD_SILO}"
    _make_ledger_table(dsn)
    _make_agent_messages_table(dsn)
    return dsn


def _write(tmp_path: Path, name: str, body: str, silo: str = PROD_SILO) -> Path:
    f = tmp_path / name
    f.write_text(f"-- ledger: silo={silo}\n{body}\n")
    return f


def _insert_gate_row(
    dsn: str,
    *,
    from_agent: str = "orch-console",
    to_agent: str = "cc-substrate",
    message_type: str = "decision",
    body: str = "",
    created_at: datetime | None = None,
) -> int:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, priority, created_at)
               VALUES (%s, %s, %s, 'gate', %s, 'P1', COALESCE(%s, now())) RETURNING id""",
            (from_agent, to_agent, message_type, body, created_at),
        )
        return cur.fetchone()[0]


# --------------------------------------------------------------------------------
# Non-production silos stay ungated (the existing ephemeral-harness convention
# in test_apply_migration.py uses "testsilo00000000000000", which is not in
# PRODUCTION_SILOS -- this is what keeps those 55 tests passing unmodified).
# --------------------------------------------------------------------------------

def test_non_production_silo_is_never_gated(fresh_db, tmp_path):
    dsn = f"{fresh_db} application_name=testsilo00000000000000"
    _make_ledger_table(dsn)
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo="testsilo00000000000000")
    result = am.apply_migration(dsn, f, silo="testsilo00000000000000")
    assert result["status"] == "applied"


# --------------------------------------------------------------------------------
# Production silo: --gate is required for a real (non-dry-run) apply.
# --------------------------------------------------------------------------------

def test_production_silo_without_gate_refuses(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    with pytest.raises(am.Refuse, match="requires --gate"):
        am.apply_migration(gated_db, f, silo=PROD_SILO)


def test_production_silo_dry_run_stays_ungated(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    result = am.apply_migration(gated_db, f, silo=PROD_SILO, dry_run=True)
    assert result["status"] == "dry_run_ok"


def test_production_silo_with_gate_but_no_gate_dsn_refuses(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    with pytest.raises(am.Refuse, match="no gate DSN resolved"):
        am.apply_migration(gated_db, f, silo=PROD_SILO, gate=1)


def test_valid_gate_allows_apply_and_records_note(gated_db, tmp_path):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);")
    sha = am.file_sha256(f)
    gate_id = _insert_gate_row(gated_db, body=f"authorizing sha256={sha[:12]} for {PROD_SILO}")

    result = am.apply_migration(gated_db, f, silo=PROD_SILO, gate=gate_id, gate_dsn=gated_db)
    assert result["status"] == "applied"
    assert result["note"] == f"gate={gate_id} from=orch-console"

    row = am.status(gated_db, f, silo=PROD_SILO)
    assert row["note"] == f"gate={gate_id} from=orch-console"


def test_gate_row_not_found_refuses(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    with pytest.raises(am.Refuse, match="does not exist"):
        am.apply_migration(gated_db, f, silo=PROD_SILO, gate=999999, gate_dsn=gated_db)


def test_gate_wrong_message_type_refuses(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    sha = am.file_sha256(f)
    gate_id = _insert_gate_row(gated_db, message_type="status", body=sha[:12])
    with pytest.raises(am.Refuse, match="not 'decision'"):
        am.apply_migration(gated_db, f, silo=PROD_SILO, gate=gate_id, gate_dsn=gated_db)


def test_gate_from_unallowlisted_agent_refuses(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    sha = am.file_sha256(f)
    gate_id = _insert_gate_row(gated_db, from_agent="cc-substrate", body=sha[:12])
    with pytest.raises(am.Refuse, match="not an allowlisted gate owner"):
        am.apply_migration(gated_db, f, silo=PROD_SILO, gate=gate_id, gate_dsn=gated_db)


def test_gate_from_cai_is_allowed(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    sha = am.file_sha256(f)
    gate_id = _insert_gate_row(gated_db, from_agent="cai", body=sha[:12])
    result = am.apply_migration(gated_db, f, silo=PROD_SILO, gate=gate_id, gate_dsn=gated_db)
    assert result["status"] == "applied"


def test_gate_predating_file_mtime_refuses(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    sha = am.file_sha256(f)
    stale = datetime.now(timezone.utc) - timedelta(days=1)
    gate_id = _insert_gate_row(gated_db, body=sha[:12], created_at=stale)
    with pytest.raises(am.Refuse, match="does not post-date"):
        am.apply_migration(gated_db, f, silo=PROD_SILO, gate=gate_id, gate_dsn=gated_db)


def test_gate_body_missing_sha_prefix_refuses(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    gate_id = _insert_gate_row(gated_db, body="approved, go ahead")
    with pytest.raises(am.Refuse, match="does not authorize THIS exact content"):
        am.apply_migration(gated_db, f, silo=PROD_SILO, gate=gate_id, gate_dsn=gated_db)


def test_gate_for_different_content_refuses(gated_db, tmp_path):
    """A gate authorizing one file's sha must not authorize a DIFFERENT file,
    even to the same silo -- the sha-prefix check is content-specific. Both
    files are written BEFORE the gate row so the mtime check passes and this
    exercises the content check specifically."""
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    other = _write(tmp_path, "999_other.sql", "create table other (id int);")
    other_sha = am.file_sha256(other)
    gate_id = _insert_gate_row(gated_db, body=other_sha[:12])

    with pytest.raises(am.Refuse, match="does not authorize THIS exact content"):
        am.apply_migration(gated_db, f, silo=PROD_SILO, gate=gate_id, gate_dsn=gated_db)


def test_unregistered_silo_with_gate_owners_missing_refuses():
    """check_gate itself refuses cleanly if ever called for a silo with no
    allowlist configured (defensive -- apply_migration only calls it for
    PRODUCTION_SILOS members, all of which have a default allowlist)."""
    with pytest.raises(am.Refuse, match="no gate-owner allowlist configured"):
        am.check_gate("unused", 1, silo="not-a-registered-silo", path=Path("x.sql"), sha="a" * 64)


def test_main_cli_gate_flags_wired(gated_db, tmp_path, capsys, monkeypatch):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);")
    sha = am.file_sha256(f)
    gate_id = _insert_gate_row(gated_db, body=sha[:12])

    monkeypatch.setenv("MY_GATE_DSN", gated_db)
    rc = am.main([
        str(f), "--silo", PROD_SILO, "--dsn", gated_db,
        "--gate", str(gate_id), "--gate-dsn-env", "MY_GATE_DSN",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"gate={gate_id}" in out


def test_main_cli_gate_missing_returns_refuse_code(gated_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);")
    rc = am.main([str(f), "--silo", PROD_SILO, "--dsn", gated_db])
    assert rc == 3
