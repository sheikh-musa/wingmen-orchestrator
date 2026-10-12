"""CI assert-gate fix (bus #64418 option (a), after #64412): migration 103 shipped
through a fully green CI suite with its required `-- assert:` lines missing entirely
(CAI-RESP-1397 #5) because scripts/ci_bootstrap_schema.py never ran
apply_migration.check_required_assertions() -- it replayed each file's raw SQL
directly, bypassing the whole assert/gate pipeline a real apply_migration.py call
goes through. This is the regression test orch-console asked for: a fixture
migration with a bare, unasserted REVOKE must fail CI the same way it would fail a
real production apply.

Pure-logic tests: _apply_file's static check_required_assertions call raises before
any DB connection is attempted, so psycopg.connect is monkeypatched to blow up if
reached -- that monkeypatch IS the proof the refusal happens pre-connection, not a
convenience to avoid a real Postgres. No real cluster needed; runs in the
PYTEST_NO_DB unit lane too.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import apply_migration as am  # noqa: E402
import ci_bootstrap_schema as cbs  # noqa: E402


def _never_connect(*a, **k):
    raise AssertionError("psycopg.connect must not be reached — the static "
                          "check_required_assertions refusal happens first")


def test_bare_revoke_with_no_asserts_fails_before_touching_the_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cbs.psycopg, "connect", _never_connect)
    fixture = tmp_path / "999_fixture_bare_revoke.sql"
    fixture.write_text(
        "-- 999_fixture_bare_revoke.sql\n"
        "-- ledger: silo=tscuymavysscrvoberrr\n"
        "BEGIN;\n"
        "REVOKE ALL ON public.some_table FROM PUBLIC, anon, authenticated;\n"
        "COMMIT;\n"
    )
    with pytest.raises(am.Refuse, match="CAI-RESP-1397"):
        cbs._apply_file("postgresql://unused", fixture)


def test_bare_drop_function_with_no_asserts_also_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cbs.psycopg, "connect", _never_connect)
    fixture = tmp_path / "999_fixture_bare_drop.sql"
    fixture.write_text(
        "-- 999_fixture_bare_drop.sql\n"
        "-- ledger: silo=tscuymavysscrvoberrr\n"
        "BEGIN;\n"
        "DROP FUNCTION public.some_dangerous_fn(text);\n"
        "COMMIT;\n"
    )
    with pytest.raises(am.Refuse, match="CAI-RESP-1397"):
        cbs._apply_file("postgresql://unused", fixture)


class _FakeCursor:
    def __init__(self, calls):
        self.calls = calls
        self._last_sql = ""

    def execute(self, sql, params=None):
        self.calls.append(sql)
        self._last_sql = sql

    def fetchone(self):
        # to_regclass existence probe: report the table exists; has_table_privilege
        # probe: report the REVOKE stuck (no privilege) -- both needed for
        # run_assertions' no_table_privilege check to actually pass.
        if "to_regclass" in self._last_sql:
            return (True,)
        return (False,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, calls):
        self.calls = calls

    def cursor(self):
        return _FakeCursor(self.calls)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_revoke_with_proper_asserts_proceeds_to_apply_and_runs_them(tmp_path, monkeypatch):
    # _apply_file's final label computation does path.relative_to(cbs.ROOT) --
    # harmless in real use (every file it ever applies lives under ROOT) but needs
    # a matching ROOT here so a tmp_path fixture doesn't trip ValueError on an
    # unrelated path, which isn't what this test is about.
    monkeypatch.setattr(cbs, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(cbs.psycopg, "connect", lambda *a, **k: _FakeConn(calls))
    fixture = tmp_path / "999_fixture_asserted_revoke.sql"
    fixture.write_text(
        "-- 999_fixture_asserted_revoke.sql\n"
        "-- ledger: silo=tscuymavysscrvoberrr\n"
        "-- assert: no_table_privilege anon public.some_table SELECT\n"
        "BEGIN;\n"
        "REVOKE ALL ON public.some_table FROM PUBLIC, anon, authenticated;\n"
        "COMMIT;\n"
    )
    cbs._apply_file("postgresql://unused", fixture)  # must not raise
    joined = "\n".join(calls)
    assert "REVOKE ALL" in joined, "the migration body itself must still run"
    assert "has_table_privilege" in joined, (
        "run_assertions must execute the assert check against the (fake) cluster, "
        "not just parse it"
    )


def test_pre_1397_exempt_file_skips_the_static_check(tmp_path, monkeypatch):
    monkeypatch.setattr(cbs, "ROOT", tmp_path)  # see comment in the test above
    calls = []
    monkeypatch.setattr(cbs.psycopg, "connect", lambda *a, **k: _FakeConn(calls))
    fixture = tmp_path / "005_fixture_grandfathered_bare_revoke.sql"
    fixture.write_text(
        "-- 005_fixture_grandfathered_bare_revoke.sql\n"
        "-- ledger: silo=tscuymavysscrvoberrr\n"
        "BEGIN;\n"
        "REVOKE ALL ON public.some_table FROM PUBLIC, anon, authenticated;\n"
        "COMMIT;\n"
    )
    monkeypatch.setattr(cbs, "_PRE_1397_ASSERT_EXEMPT", frozenset({fixture}))
    cbs._apply_file("postgresql://unused", fixture)  # must not raise despite no asserts


def test_every_currently_grandfathered_path_actually_exists_on_disk():
    """Guards against the exemption set rotting: every path in it must be a real
    file this repo's walk still applies, so the set can never silently grow stale
    (e.g. a file renamed/deleted elsewhere leaving a dead, meaningless entry)."""
    for path in cbs._PRE_1397_ASSERT_EXEMPT:
        assert path.exists(), f"{path} is in _PRE_1397_ASSERT_EXEMPT but does not exist"
