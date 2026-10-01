"""Tests for scripts/lib/substrate_dsn.dsn_from_env_file (op#24342).

The 3 console-approved cases (#48279) plus two parsing/edge cases:
  1. a STALE inherited os.environ DATABASE_URL + a DIFFERENT .env file value
     => the FILE value wins (the whole point: a rotation's stale cred can't win);
  2. file missing => falls back to the environment;
  3. neither file nor env has it => raises LOUD (RuntimeError);
  (4) file present but with no DATABASE_URL line => env fallback;
  (5) export-prefix + quotes are parsed/stripped.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import substrate_dsn  # noqa: E402

FILE_DSN = "postgresql://file-user:file-pw@aws-pooler:5432/postgres"
ENV_DSN = "postgresql://STALE-user:STALE-pw@aws-pooler:5432/postgres"


def _write_env(tmp_path, body):
    p = tmp_path / ".env"
    p.write_text(body)
    return str(p)


def test_file_value_wins_over_stale_env(tmp_path):
    # The rotation's single push-point is the FILE; a long-lived process holding a
    # stale DATABASE_URL in its env must NOT win.
    env_path = _write_env(tmp_path, "DATABASE_URL=%s\n" % FILE_DSN)
    got = substrate_dsn.dsn_from_env_file(env_path=env_path, env={"DATABASE_URL": ENV_DSN})
    assert got == FILE_DSN


def test_falls_back_to_env_when_file_missing(tmp_path):
    missing = str(tmp_path / "does-not-exist.env")
    got = substrate_dsn.dsn_from_env_file(env_path=missing, env={"DATABASE_URL": ENV_DSN})
    assert got == ENV_DSN


def test_raises_loud_when_neither(tmp_path):
    missing = str(tmp_path / "does-not-exist.env")
    with pytest.raises(RuntimeError):
        substrate_dsn.dsn_from_env_file(env_path=missing, env={})


def test_file_without_dsn_line_falls_back_to_env(tmp_path):
    env_path = _write_env(tmp_path, "OTHER=1\nSUPABASE_URL=https://x\n")
    got = substrate_dsn.dsn_from_env_file(env_path=env_path, env={"DATABASE_URL": ENV_DSN})
    assert got == ENV_DSN


def test_parses_export_prefix_and_quotes(tmp_path):
    env_path = _write_env(tmp_path, 'export DATABASE_URL="%s"\n' % FILE_DSN)
    got = substrate_dsn.dsn_from_env_file(env_path=env_path, env={})
    assert got == FILE_DSN


def test_last_match_wins_on_duplicate_lines(tmp_path):
    # Mirrors `set -a; . .env` (LAST-wins). An append-rotation must NOT leave a stale
    # first value winning (cc-quality MEDIUM on PR #242).
    old = "postgresql://OLD:OLD@aws-pooler:5432/postgres"
    env_path = _write_env(tmp_path, "DATABASE_URL=%s\nDATABASE_URL=%s\n" % (old, FILE_DSN))
    assert substrate_dsn.dsn_from_env_file(env_path=env_path, env={}) == FILE_DSN


def test_strips_unquoted_trailing_inline_comment(tmp_path):
    env_path = _write_env(tmp_path, "DATABASE_URL=%s  # rotated 2026-10-01\n" % FILE_DSN)
    assert substrate_dsn.dsn_from_env_file(env_path=env_path, env={}) == FILE_DSN


def test_hash_in_unquoted_password_is_not_a_comment(tmp_path):
    # A '#' with no preceding whitespace is part of the value (shell keeps it).
    dsn = "postgresql://u:p#ss@aws-pooler:5432/postgres"
    env_path = _write_env(tmp_path, "DATABASE_URL=%s\n" % dsn)
    assert substrate_dsn.dsn_from_env_file(env_path=env_path, env={}) == dsn


def test_non_utf8_file_falls_back_to_env(tmp_path):
    p = tmp_path / ".env"
    p.write_bytes(b"DATABASE_URL=postgresql://x\xff\xfe@h/db\n")  # invalid UTF-8
    got = substrate_dsn.dsn_from_env_file(env_path=str(p), env={"DATABASE_URL": ENV_DSN})
    assert got == ENV_DSN
