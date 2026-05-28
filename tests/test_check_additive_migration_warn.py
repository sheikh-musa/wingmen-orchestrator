"""Tests for the M_PRIMARY supabase-CLI parent-process warning."""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_additive_migration as cam  # noqa: E402


def _fake_ps_output(cmd: str):
    """Fake subprocess.run result returning given parent command."""
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=cmd, stderr="")
    return fake


class TestSupabasePushWarning:
    def test_no_warning_under_python_parent(self):
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock, \
             patch("subprocess.run", return_value=_fake_ps_output("python scripts/check_additive_migration.py foo.sql")):
            sys_mock.stderr = capture
            cam._warn_if_supabase_db_push_context()
        assert capture.getvalue() == ""

    def test_warning_when_parent_is_supabase_binary(self):
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock, \
             patch("subprocess.run", return_value=_fake_ps_output("supabase db push --linked")):
            sys_mock.stderr = capture
            cam._warn_if_supabase_db_push_context()
        out = capture.getvalue()
        assert "supabase CLI" in out
        assert "CC-SUBSTRATE-VIEW-INTEGRITY-001-FINDINGS" in out
        assert "psycopg-apply" in out

    def test_no_warning_when_parent_is_absolute_python(self):
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock, \
             patch("subprocess.run", return_value=_fake_ps_output("/usr/local/bin/python3 foo.py")):
            sys_mock.stderr = capture
            cam._warn_if_supabase_db_push_context()
        assert capture.getvalue() == ""

    def test_no_warning_when_path_contains_supabase_but_exe_isnt(self):
        """Parent like 'python scripts/run_supabase_thing.py' must not false-positive."""
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock, \
             patch("subprocess.run", return_value=_fake_ps_output("python scripts/supabase_migrate.py")):
            sys_mock.stderr = capture
            cam._warn_if_supabase_db_push_context()
        assert capture.getvalue() == ""

    def test_subprocess_error_silent_no_warning(self):
        """If ps fails, function returns silently (don't crash the linter)."""
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock, \
             patch("subprocess.run", side_effect=OSError("ps unavailable")):
            sys_mock.stderr = capture
            cam._warn_if_supabase_db_push_context()
        assert capture.getvalue() == ""


class TestEmptyStatementsAdvisory:
    """M_LEDGER_AUDIT advisory — flag migrations that ship empty statements arrays."""

    def test_empty_array_triggers_advisory(self, tmp_path):
        sql = (
            "CREATE TABLE foo (id int);\n"
            "INSERT INTO supabase_migrations.schema_migrations\n"
            "  (version, name, statements)\n"
            "VALUES ('20260601000000', 'foo', ARRAY[]::text[])\n"
            "ON CONFLICT (version) DO NOTHING;\n"
        )
        p = tmp_path / "m.sql"
        p.write_text(sql)
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock:
            sys_mock.stderr = capture
            cam._advise_if_empty_statements(p, sql)
        out = capture.getvalue()
        assert "ADVISORY" in out
        assert "schema_migrations.statements is empty" in out

    def test_populated_array_no_advisory(self, tmp_path):
        sql = (
            "INSERT INTO supabase_migrations.schema_migrations\n"
            "  (version, name, statements)\n"
            "VALUES ('20260601000000', 'bar', ARRAY[$$ALTER TABLE x ADD COLUMN y int$$]::text[])\n"
            "ON CONFLICT (version) DO NOTHING;\n"
        )
        p = tmp_path / "m.sql"
        p.write_text(sql)
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock:
            sys_mock.stderr = capture
            cam._advise_if_empty_statements(p, sql)
        assert capture.getvalue() == ""

    def test_no_schema_migrations_no_advisory(self, tmp_path):
        """If the migration doesn't touch schema_migrations at all, advisory irrelevant."""
        sql = "ALTER TABLE foo ADD COLUMN z int;\n"
        p = tmp_path / "m.sql"
        p.write_text(sql)
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock:
            sys_mock.stderr = capture
            cam._advise_if_empty_statements(p, sql)
        assert capture.getvalue() == ""

    def test_empty_array_in_comment_no_advisory(self, tmp_path):
        """The comment-stripping path must hide false positives."""
        sql = (
            "-- DO NOT do this: ARRAY[]::text[]\n"
            "INSERT INTO supabase_migrations.schema_migrations\n"
            "VALUES ('20260601000000', 'x', ARRAY[$$ok$$]::text[])\n"
            "ON CONFLICT (version) DO NOTHING;\n"
        )
        p = tmp_path / "m.sql"
        p.write_text(sql)
        capture = io.StringIO()
        with patch.object(cam, "sys", create=True) as sys_mock:
            sys_mock.stderr = capture
            cam._advise_if_empty_statements(p, sql)
        assert capture.getvalue() == ""
