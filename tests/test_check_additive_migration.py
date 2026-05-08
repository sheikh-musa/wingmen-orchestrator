"""TDD tests for scripts/check_additive_migration.py

Each test creates a temp SQL file, runs the script via subprocess, and asserts
exit code + output. Tests must FAIL before the script exists (Step 2), then
PASS after implementation (Step 4).
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_additive_migration.py"
PYTHON = sys.executable


def run_linter(sql: str, tmp_path: Path) -> subprocess.CompletedProcess:
    f = tmp_path / "migration.sql"
    f.write_text(textwrap.dedent(sql))
    return subprocess.run(
        [PYTHON, str(SCRIPT), str(f)],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# ADDITIVE — must exit 0
# ---------------------------------------------------------------------------

def test_add_column_if_not_exists_is_safe(tmp_path):
    result = run_linter(
        """
        ALTER TABLE foo ADD COLUMN IF NOT EXISTS bar TEXT;
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_create_table_is_safe(tmp_path):
    result = run_linter(
        """
        CREATE TABLE IF NOT EXISTS new_table (id SERIAL PRIMARY KEY, val TEXT);
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_create_or_replace_view_is_safe(tmp_path):
    result = run_linter(
        """
        CREATE OR REPLACE VIEW boot_briefing AS
        SELECT 'repo_context'::text AS source, rc.repo AS key
          FROM repo_context rc;
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_create_or_replace_function_is_safe(tmp_path):
    result = run_linter(
        """
        CREATE OR REPLACE FUNCTION my_func() RETURNS void AS $$
        BEGIN
          NULL;
        END;
        $$ LANGUAGE plpgsql;
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_delete_with_id_eq_restriction_is_safe(tmp_path):
    result = run_linter(
        """
        DELETE FROM foo WHERE id = 5;
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_delete_with_id_in_restriction_is_safe(tmp_path):
    result = run_linter(
        """
        DELETE FROM foo WHERE id IN (1, 2, 3);
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_update_with_where_clause_is_safe(tmp_path):
    result = run_linter(
        """
        UPDATE foo SET x = 1 WHERE status = 'new';
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_drop_trigger_then_create_same_name_is_safe(tmp_path):
    result = run_linter(
        """
        DROP TRIGGER IF EXISTS trg_announce ON notifications;
        CREATE TRIGGER trg_announce
          AFTER INSERT ON notifications
          FOR EACH ROW EXECUTE FUNCTION notify_fn();
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_empty_file_is_safe(tmp_path):
    result = run_linter("", tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout


def test_comments_only_is_safe(tmp_path):
    result = run_linter(
        """
        -- This migration does nothing
        /* just a placeholder */
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_insert_on_conflict_do_nothing_is_safe(tmp_path):
    result = run_linter(
        """
        INSERT INTO schema_migrations (version) VALUES ('001')
        ON CONFLICT DO NOTHING;
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_real_synthetic_filter_migration_is_safe(tmp_path):
    """The migration we just shipped must pass — it has UPDATE WHERE status IN (...),
    CREATE OR REPLACE VIEW, and a DO $$ ... $$ plpgsql block."""
    migration = (
        Path(__file__).parent.parent
        / "supabase/migrations/20260508_bug_reports_synthetic_filter.sql"
    )
    result = subprocess.run(
        [PYTHON, str(SCRIPT), str(migration)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Real migration flagged as destructive:\n{result.stderr}\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# DESTRUCTIVE — must exit 1 and mention the pattern
# ---------------------------------------------------------------------------

def test_drop_table_is_blocked(tmp_path):
    result = run_linter(
        """
        DROP TABLE IF EXISTS foo;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    assert "DROP TABLE" in result.stdout or "DROP TABLE" in result.stderr


def test_drop_column_is_blocked(tmp_path):
    result = run_linter(
        """
        ALTER TABLE foo DROP COLUMN bar;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "DROP COLUMN" in out


def test_truncate_is_blocked(tmp_path):
    result = run_linter(
        """
        TRUNCATE foo;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "TRUNCATE" in out


def test_drop_constraint_is_blocked(tmp_path):
    result = run_linter(
        """
        ALTER TABLE foo DROP CONSTRAINT bar;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "DROP CONSTRAINT" in out


def test_delete_without_where_is_blocked(tmp_path):
    result = run_linter(
        """
        DELETE FROM foo;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "DELETE" in out


def test_delete_where_no_id_restriction_is_blocked(tmp_path):
    result = run_linter(
        """
        DELETE FROM foo WHERE x = 1;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "DELETE" in out


def test_update_without_where_is_blocked(tmp_path):
    result = run_linter(
        """
        UPDATE foo SET x = 1;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "UPDATE" in out


def test_alter_type_drop_value_is_blocked(tmp_path):
    result = run_linter(
        """
        ALTER TYPE my_enum DROP VALUE 'old_val';
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "DROP VALUE" in out or "ALTER TYPE" in out


def test_drop_index_is_blocked(tmp_path):
    result = run_linter(
        """
        DROP INDEX IF EXISTS idx_foo_bar;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "DROP INDEX" in out


def test_drop_function_is_blocked(tmp_path):
    result = run_linter(
        """
        DROP FUNCTION IF EXISTS my_func();
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "DROP FUNCTION" in out


def test_comment_mentioning_drop_table_does_not_trigger(tmp_path):
    """Comments like '-- DROP TABLE would be bad' must NOT be flagged."""
    result = run_linter(
        """
        -- DROP TABLE would be a bad idea here, so we don't do it
        /* Also DROP COLUMN is destructive */
        ALTER TABLE foo ADD COLUMN IF NOT EXISTS baz INT;
        """,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr + result.stdout


# ---------------------------------------------------------------------------
# INLINE ALLOWLIST (-- linter-allow: <reason>) — opt-out for pre-approved ops
# ---------------------------------------------------------------------------


def test_inline_allowlist_suppresses_finding(tmp_path):
    """A `-- linter-allow: <reason>` comment on the line BEFORE a destructive op
    suppresses that one finding. Reason text is required (forces author to
    document why)."""
    result = run_linter(
        """
        -- linter-allow: legacy schema relaxation, pre-CAI-RESP-102
        ALTER TABLE foo ALTER COLUMN bar DROP NOT NULL;
        """,
        tmp_path,
    )
    assert result.returncode == 0, f"expected 0 (allowlist suppressed), got {result.returncode}: {result.stdout}{result.stderr}"


def test_inline_allowlist_without_reason_does_not_suppress(tmp_path):
    """Bare `-- linter-allow` (no reason text) is NOT a valid allowlist —
    forces the author to document why."""
    result = run_linter(
        """
        -- linter-allow
        ALTER TABLE foo ALTER COLUMN bar DROP NOT NULL;
        """,
        tmp_path,
    )
    assert result.returncode == 1


def test_inline_allowlist_only_suppresses_next_line(tmp_path):
    """Allowlist applies ONLY to the line immediately after the comment.
    A second destructive op below is still flagged."""
    result = run_linter(
        """
        -- linter-allow: relaxation
        ALTER TABLE a ALTER COLUMN x DROP NOT NULL;
        DROP TABLE b;
        """,
        tmp_path,
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "DROP TABLE" in out
