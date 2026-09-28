"""Tests for apply_migration.py's --repo resolution (bus #44358).

Incident: a cosem-platform lane applied a migration to its own production
silo (ywrpttpxwfcoodovxhsr) WITHOUT passing --repo, and this tool silently
ledgered it under DEFAULT_REPO ('orchestrator') — a real mislabel that was
then "fixed" by hand-running an UPDATE on migration_ledger, bypassing this
tool entirely. resolve_repo() now refuses that silent default for any
PRODUCTION_SILOS member other than the orchestrator substrate, unless --repo
is given explicitly or derivable from the migration file's own git checkout.

Runs entirely against the ephemeral PG17 harness (tests/migrations/conftest.py)
plus scratch git repos under tmp_path. NEVER touches DATABASE_URL / any live
silo or the real REPOS.json (REPOS_JSON is monkeypatched to a scratch file).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

# A real production silo ref, deliberately NOT the orchestrator substrate —
# this is the exact class of silo the bug (and this fix) is about.
NON_SUBSTRATE_PROD_SILO = "ywrpttpxwfcoodovxhsr"
TEST_SILO = "testsilo00000000000000"  # not in PRODUCTION_SILOS at all


def _write(tmp_path: Path, name: str, body: str, silo: str) -> Path:
    f = tmp_path / name
    f.write_text(f"-- ledger: silo={silo}\n{body}\n")
    return f


def _write_repos_json(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"repos": entries}))


def _init_git_repo_with_origin(repo_dir: Path, origin_url: str) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=repo_dir, check=True)


# --------------------------------------------------------------------------------
# resolve_repo() — pure decision logic, no DB
# --------------------------------------------------------------------------------

def test_explicit_repo_always_wins_regardless_of_silo(tmp_path):
    f = tmp_path / "001_a.sql"
    f.write_text("select 1;")
    repo, source = am.resolve_repo(silo=NON_SUBSTRATE_PROD_SILO, repo="cosem-platform", path=f)
    assert (repo, source) == ("cosem-platform", "explicit")


def test_defaults_to_orchestrator_repo_for_the_substrate_silo(tmp_path):
    f = tmp_path / "001_a.sql"
    f.write_text("select 1;")
    repo, source = am.resolve_repo(silo=am.ORCHESTRATOR_SUBSTRATE_SILO, repo=None, path=f)
    assert (repo, source) == (am.DEFAULT_REPO, "default")


def test_defaults_for_a_silo_outside_production_silos(tmp_path):
    """Ephemeral/test-harness silos (not a registered production store) stay
    friction-free by construction — same scoping the existing --gate check
    already uses, so the pre-existing test_apply_migration*.py suites (which
    never pass --repo) keep passing unmodified."""
    f = tmp_path / "001_a.sql"
    f.write_text("select 1;")
    repo, source = am.resolve_repo(silo=TEST_SILO, repo=None, path=f)
    assert (repo, source) == (am.DEFAULT_REPO, "default")


def test_refuses_for_non_substrate_production_silo_with_no_repo_and_no_git_checkout(tmp_path):
    f = tmp_path / "001_a.sql"
    f.write_text("select 1;")  # tmp_path has no .git anywhere above it
    with pytest.raises(am.Refuse, match="no --repo was given"):
        am.resolve_repo(silo=NON_SUBSTRATE_PROD_SILO, repo=None, path=f)


def test_derives_repo_from_enclosing_git_checkout_origin(tmp_path, monkeypatch):
    registry = tmp_path / "REPOS.json"
    _write_repos_json(registry, [
        {"name": "cosem-platform", "github": "https://github.com/sheikh-musa/cosem-platform"},
    ])
    monkeypatch.setattr(am, "REPOS_JSON", registry)

    checkout = tmp_path / "cosem-port-lane"  # deliberately NOT named after the repo —
    _init_git_repo_with_origin(checkout, "https://github.com/sheikh-musa/cosem-platform.git")
    migration = checkout / "supabase" / "migrations" / "001_a.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("select 1;")

    repo, source = am.resolve_repo(silo=NON_SUBSTRATE_PROD_SILO, repo=None, path=migration)
    assert (repo, source) == ("cosem-platform", "derived")


def test_derivation_matches_by_origin_not_checkout_directory_basename(tmp_path, monkeypatch):
    """The exact bus #44358 root cause: the checkout directory's own name
    ('cosem-port-lane') must NEVER be used as the repo name — only the
    registered repo the origin remote actually points at."""
    registry = tmp_path / "REPOS.json"
    _write_repos_json(registry, [
        {"name": "cosem-platform", "github": "https://github.com/sheikh-musa/cosem-platform"},
    ])
    monkeypatch.setattr(am, "REPOS_JSON", registry)

    checkout = tmp_path / "cosem-port-lane"
    _init_git_repo_with_origin(checkout, "https://github.com/sheikh-musa/cosem-platform.git")
    migration = checkout / "supabase" / "migrations" / "001_a.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text("select 1;")

    repo = am.derive_repo_from_path(migration)
    assert repo == "cosem-platform"
    assert repo != checkout.name


def test_derivation_returns_none_when_origin_matches_no_registry_entry(tmp_path, monkeypatch):
    registry = tmp_path / "REPOS.json"
    _write_repos_json(registry, [
        {"name": "some-other-repo", "github": "https://github.com/sheikh-musa/some-other-repo"},
    ])
    monkeypatch.setattr(am, "REPOS_JSON", registry)

    checkout = tmp_path / "unknown-checkout"
    _init_git_repo_with_origin(checkout, "https://github.com/sheikh-musa/unregistered-repo.git")
    migration = checkout / "001_a.sql"
    migration.write_text("select 1;")

    assert am.derive_repo_from_path(migration) is None
    with pytest.raises(am.Refuse):
        am.resolve_repo(silo=NON_SUBSTRATE_PROD_SILO, repo=None, path=migration)


def test_derivation_returns_none_outside_any_git_checkout(tmp_path):
    f = tmp_path / "001_a.sql"
    f.write_text("select 1;")
    assert am.derive_repo_from_path(f) is None


# --------------------------------------------------------------------------------
# apply_migration() end-to-end: the resolution actually gates the apply, and the
# ledger row records the resolved repo — not a silent default.
# --------------------------------------------------------------------------------

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
    dsn = f"{fresh_db} application_name={NON_SUBSTRATE_PROD_SILO}"
    _make_ledger_table(dsn)
    return dsn


def test_dry_run_refuses_without_repo_for_non_substrate_prod_silo(ledger_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo=NON_SUBSTRATE_PROD_SILO)
    with pytest.raises(am.Refuse, match="no --repo was given"):
        am.apply_migration(ledger_db, f, silo=NON_SUBSTRATE_PROD_SILO, dry_run=True)


def test_dry_run_succeeds_and_reports_repo_when_explicit(ledger_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo=NON_SUBSTRATE_PROD_SILO)
    result = am.apply_migration(
        ledger_db, f, silo=NON_SUBSTRATE_PROD_SILO, repo="cosem-platform", dry_run=True,
    )
    assert result["status"] == "dry_run_ok"
    assert result["repo"] == "cosem-platform"
    assert result["repo_source"] == "explicit"


def test_dry_run_derives_repo_and_ledgers_it_would_use(ledger_db, tmp_path, monkeypatch):
    registry = tmp_path / "REPOS.json"
    _write_repos_json(registry, [
        {"name": "cosem-platform", "github": "https://github.com/sheikh-musa/cosem-platform"},
    ])
    monkeypatch.setattr(am, "REPOS_JSON", registry)

    checkout = tmp_path / "cosem-port-lane"
    _init_git_repo_with_origin(checkout, "https://github.com/sheikh-musa/cosem-platform.git")
    migration = checkout / "supabase" / "migrations" / "001_a.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text(f"-- ledger: silo={NON_SUBSTRATE_PROD_SILO}\ncreate table a (id int);\n")

    result = am.apply_migration(ledger_db, migration, silo=NON_SUBSTRATE_PROD_SILO, dry_run=True)
    assert result["status"] == "dry_run_ok"
    assert result["repo"] == "cosem-platform"
    assert result["repo_source"] == "derived"


# --------------------------------------------------------------------------------
# CLI: --repo defaults to None now (not DEFAULT_REPO); resolution is printed.
# --------------------------------------------------------------------------------

def test_cli_dry_run_prints_resolved_repo_when_explicit(ledger_db, tmp_path, capsys):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo=NON_SUBSTRATE_PROD_SILO)
    rc = am.main([
        str(f), "--silo", NON_SUBSTRATE_PROD_SILO, "--dsn", ledger_db,
        "--repo", "cosem-platform", "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "repo=cosem-platform" in out
    assert "[explicit]" in out


def test_cli_refuses_nonzero_without_repo_for_non_substrate_prod_silo(ledger_db, tmp_path):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo=NON_SUBSTRATE_PROD_SILO)
    rc = am.main([str(f), "--silo", NON_SUBSTRATE_PROD_SILO, "--dsn", ledger_db, "--dry-run"])
    assert rc == 3


def test_cli_status_prints_resolved_repo_line(ledger_db, tmp_path, capsys):
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo=NON_SUBSTRATE_PROD_SILO)
    rc = am.main([
        str(f), "--silo", NON_SUBSTRATE_PROD_SILO, "--dsn", ledger_db,
        "--repo", "cosem-platform", "--status",
    ])
    assert rc == 1  # not ledgered yet
    out = capsys.readouterr().out
    assert "repo: cosem-platform (explicit)" in out


def test_cli_still_defaults_repo_for_orchestrator_substrate_silo(tmp_path, fresh_db, capsys):
    dsn = f"{fresh_db} application_name={am.ORCHESTRATOR_SUBSTRATE_SILO}"
    _make_ledger_table(dsn)
    f = _write(tmp_path, "001_a.sql", "create table a (id int);", silo=am.ORCHESTRATOR_SUBSTRATE_SILO)
    rc = am.main([str(f), "--silo", am.ORCHESTRATOR_SUBSTRATE_SILO, "--dsn", dsn, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"repo={am.DEFAULT_REPO} [default]" in out
