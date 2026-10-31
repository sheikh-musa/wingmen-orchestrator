"""Tests for scripts/lib/dsn_at_use.py (rotation hardening, bus 87803 step 5)."""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import dsn_at_use  # noqa: E402

SCHEME = "postgre" + "sql://"
STALE = f"{SCHEME}stale:oldvalue@example.invalid:5432/db"
FRESH = f"{SCHEME}fresh:newvalue@example.invalid:5432/db"


def _env(tmp_path, body):
    p = tmp_path / ".env"
    p.write_text(body)
    return p


def test_the_file_wins_over_a_stale_inherited_variable(tmp_path, monkeypatch):
    """The whole point: the boot shell's value is dead after a rotation."""
    env = _env(tmp_path, f"DATABASE_URL={FRESH}\n")
    monkeypatch.setenv("DATABASE_URL", STALE)
    assert dsn_at_use.dsn(str(env)) == FRESH


def test_it_falls_back_to_the_environment_when_there_is_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", STALE)
    monkeypatch.setattr(dsn_at_use, "CANDIDATES", (str(tmp_path / "absent"),))
    assert dsn_at_use.dsn() == STALE


def test_supabase_db_url_is_accepted_when_database_url_is_absent(tmp_path):
    env = _env(tmp_path, f"SUPABASE_DB_URL={FRESH}\n")
    assert dsn_at_use.from_file(str(env)) == FRESH


def test_first_match_wins_database_url_over_supabase_db_url(tmp_path):
    env = _env(tmp_path, f"SUPABASE_DB_URL={STALE}\nDATABASE_URL={FRESH}\n")
    assert dsn_at_use.from_file(str(env)) == FRESH


def test_quotes_and_export_prefix_are_stripped(tmp_path):
    env = _env(tmp_path, f'export DATABASE_URL="{FRESH}"\n')
    assert dsn_at_use.from_file(str(env)) == FRESH


def test_unrelated_keys_do_not_match(tmp_path):
    env = _env(tmp_path, "NOT_DATABASE_URL=x\nDATABASE_URL_OLD=y\n")
    assert dsn_at_use.from_file(str(env)) is None


def test_a_missing_file_returns_none_without_raising(tmp_path):
    assert dsn_at_use.from_file(str(tmp_path / "nope")) is None


def test_orch_env_file_overrides_the_candidate_list(tmp_path, monkeypatch):
    env = _env(tmp_path, f"DATABASE_URL={FRESH}\n")
    monkeypatch.setenv("ORCH_ENV_FILE", str(env))
    monkeypatch.setattr(dsn_at_use, "CANDIDATES", ())
    assert dsn_at_use.from_file() == FRESH
