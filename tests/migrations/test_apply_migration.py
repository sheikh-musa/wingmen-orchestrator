"""Tests for scripts/apply_migration.py — the generic migration applier (op#19103 item 3).

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

SILO = "testsilo00000000000000"


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


@pytest.fixture
def ledger_db(fresh_db):
    # libpq connstrings accept arbitrary space-separated keyword=value pairs;
    # append application_name=<SILO> so the residency guard (which asserts the
    # silo ref appears in the resolved DSN, mirroring how a real pooler URL
    # embeds the project ref in the username) has something real to check
    # against the local ephemeral socket DSN.
    dsn = f"{fresh_db} application_name={SILO}"
    _make_ledger_table(dsn)
    return dsn


def _write(tmp_path: Path, name: str, body: str, silo: str = SILO) -> Path:
    f = tmp_path / name
    f.write_text(f"-- ledger: silo={silo}\n{body}\n")
    return f


def test_resolve_migration_path_literal(tmp_path):
    f = tmp_path / "x.sql"
    f.write_text("-- ledger: silo=foo\nselect 1;")
    assert am.resolve_migration_path(str(f)) == f


def test_resolve_migration_path_numeric_prefix(tmp_path):
    f = tmp_path / "042_thing.sql"
    f.write_text("-- ledger: silo=foo\nselect 1;")
    assert am.resolve_migration_path("042", migrations_dir=tmp_path) == f


def test_resolve_migration_path_ambiguous(tmp_path):
    (tmp_path / "042_a.sql").write_text("select 1;")
    (tmp_path / "042_b.sql").write_text("select 1;")
    with pytest.raises(am.Refuse, match="ambiguous"):
        am.resolve_migration_path("042", migrations_dir=tmp_path)


def test_resolve_migration_path_missing(tmp_path):
    with pytest.raises(am.Refuse, match="no migrations"):
        am.resolve_migration_path("999", migrations_dir=tmp_path)


def test_missing_ledger_header_refuses(tmp_path):
    f = tmp_path / "no_header.sql"
    f.write_text("create table foo (id int);")
    with pytest.raises(am.Refuse, match="missing required"):
        am.parse_ledger_header(f.read_text())


def test_silo_mismatch_vs_header_refuses(ledger_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo=SILO)
    with pytest.raises(am.Refuse, match="does not match"):
        am.apply_migration(ledger_db, f, silo="some-other-silo")


def test_silo_not_in_dsn_refuses(ledger_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo="not-in-the-dsn-at-all")
    with pytest.raises(am.Refuse, match="residency guard"):
        am.apply_migration(ledger_db, f, silo="not-in-the-dsn-at-all")


def test_strip_txn_control_removes_begin_commit():
    body = "BEGIN;\ncreate table a (id int);\nCOMMIT;\n"
    assert am.strip_txn_control(body).strip() == "create table a (id int);"


def test_strip_txn_control_leaves_survivor_and_refuses():
    # a ROLLBACK mid-body isn't top-level-strippable by our simple line match test above,
    # but a stray one that IS on its own line should still be caught if it's not BEGIN/COMMIT.
    body = "ROLLBACK;\ncreate table a (id int);\n"
    with pytest.raises(am.Refuse, match="survived the strip"):
        am.strip_txn_control(body)


def test_apply_new_migration_ledgers_in_same_transaction(ledger_db, tmp_path):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    result = am.apply_migration(ledger_db, f, silo=SILO)
    assert result["status"] == "applied"

    with psycopg.connect(ledger_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.widgets') IS NOT NULL")
        assert cur.fetchone()[0] is True
        cur.execute(
            "SELECT sha256 FROM migration_ledger WHERE migration_name='001_make_widgets.sql' AND silo_ref=%s",
            (SILO,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == am.file_sha256(f)


def test_apply_same_migration_twice_is_noop_not_error(ledger_db, tmp_path):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    first = am.apply_migration(ledger_db, f, silo=SILO)
    assert first["status"] == "applied"
    second = am.apply_migration(ledger_db, f, silo=SILO)
    assert second["status"] == "already_applied"

    with psycopg.connect(ledger_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM migration_ledger WHERE migration_name='001_make_widgets.sql'")
        assert cur.fetchone()[0] == 1


def test_rename_and_reapply_same_content_refuses(ledger_db, tmp_path):
    original = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    am.apply_migration(ledger_db, original, silo=SILO)

    renamed = _write(tmp_path, "002_make_widgets_renamed.sql", "create table widgets (id int);", silo=SILO)
    with pytest.raises(am.Refuse, match="cannot be renamed"):
        am.apply_migration(ledger_db, renamed, silo=SILO)


def test_edited_after_apply_drift_refuses(ledger_db, tmp_path):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    am.apply_migration(ledger_db, f, silo=SILO)

    # same name, different body on disk now (simulates a post-apply edit)
    f.write_text(f"-- ledger: silo={SILO}\ncreate table widgets (id int, extra text);\n")
    with pytest.raises(am.Refuse, match="drifted"):
        am.apply_migration(ledger_db, f, silo=SILO)


def test_dry_run_does_not_commit_or_ledger(ledger_db, tmp_path):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    result = am.apply_migration(ledger_db, f, silo=SILO, dry_run=True)
    assert result["status"] == "dry_run_ok"

    with psycopg.connect(ledger_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.widgets')")
        assert cur.fetchone()[0] is None
        cur.execute("SELECT count(*) FROM migration_ledger")
        assert cur.fetchone()[0] == 0


def test_dry_run_then_real_apply_still_works(ledger_db, tmp_path):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    am.apply_migration(ledger_db, f, silo=SILO, dry_run=True)
    result = am.apply_migration(ledger_db, f, silo=SILO)
    assert result["status"] == "applied"


def test_broken_sql_leaves_no_partial_ledger_row(ledger_db, tmp_path):
    f = _write(tmp_path, "001_broken.sql", "create table widgets (id int); this is not sql;", silo=SILO)
    with pytest.raises(psycopg.errors.SyntaxError):
        am.apply_migration(ledger_db, f, silo=SILO)

    with psycopg.connect(ledger_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM migration_ledger")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT to_regclass('public.widgets')")
        assert cur.fetchone()[0] is None


def test_status_reports_none_before_apply(ledger_db, tmp_path):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    assert am.status(ledger_db, f, silo=SILO) is None


def test_status_reports_row_after_apply(ledger_db, tmp_path):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    am.apply_migration(ledger_db, f, silo=SILO)
    row = am.status(ledger_db, f, silo=SILO)
    assert row is not None
    assert row["sha256"] == am.file_sha256(f)


def test_main_cli_status_and_apply(ledger_db, tmp_path, capsys):
    f = _write(tmp_path, "001_make_widgets.sql", "create table widgets (id int);", silo=SILO)
    rc = am.main([str(f), "--silo", SILO, "--dsn", ledger_db, "--status"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "NOT LEDGERED" in out

    rc = am.main([str(f), "--silo", SILO, "--dsn", ledger_db])
    assert rc == 0

    rc = am.main([str(f), "--silo", SILO, "--dsn", ledger_db, "--status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LEDGERED" in out


def test_main_cli_refuse_returns_nonzero(ledger_db, tmp_path):
    f = tmp_path / "no_header.sql"
    f.write_text("create table a (id int);")
    rc = am.main([str(f), "--silo", SILO, "--dsn", ledger_db])
    assert rc == 3
