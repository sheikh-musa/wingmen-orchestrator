"""ci_bootstrap silo scope (orch-console #80426/#80462, guard per #80463): the
substrate bootstrap builds CI's ephemeral SUBSTRATE schema by walking
migrations/*.sql unconditionally — numbering a STORE-targeted migration (129)
into migrations/ put it in that walk, and its section-0 asserts refuse on the
ephemeral substrate (no fleet_provisioner there), killing CI at the bootstrap
step.

The behavior this file pins (console #80463 GO, with its guard):
- a migrations/*.sql file whose `-- ledger: silo=<ref>` header declares a
  REGISTERED production store other than the orchestrator substrate is out
  of the substrate bootstrap's scope BY ITS OWN DECLARATION — skipped
  loudly, never applied (129 is the first);
- a header naming an UNKNOWN ref FAILS the bootstrap — a typo can never
  silently drop a substrate migration out of CI;
- headerless files (the 53 historical migrations predating the convention)
  and substrate-declaring files apply exactly as before.

The registered set is apply_migration's own PRODUCTION_SILOS, never a typed
list. Pure-logic tests, the style of test_ci_bootstrap_schema_assert_gate.py:
no database is ever reached.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import apply_migration as am  # noqa: E402
import ci_bootstrap_schema as cbs  # noqa: E402

# a registered non-substrate production store, derived never typed
_A_REGISTERED_STORE = next(s for s in am.PRODUCTION_SILOS
                           if s != am.ORCHESTRATOR_SUBSTRATE_SILO)


def _never_connect(*a, **k):
    raise AssertionError("psycopg.connect must not be reached — the silo-scope "
                          "decision happens before any connection opens")


class _RecordingCursor:
    def __init__(self, calls):
        self.calls = calls
        self._last_sql = ""

    def execute(self, sql, params=None):
        self.calls.append(sql)
        self._last_sql = sql

    def fetchone(self):
        # run_assertions probes: to_regclass says the table exists,
        # has_table_privilege says the REVOKE stuck — assert passes
        if "to_regclass" in self._last_sql:
            return (True,)
        return (False,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RecordingConn:
    def __init__(self, calls):
        self.calls = calls

    def cursor(self):
        return _RecordingCursor(self.calls)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_registered_store_declared_migration_is_skipped_before_any_connection(
        tmp_path, monkeypatch):
    monkeypatch.setattr(cbs.psycopg, "connect", _never_connect)
    fixture = tmp_path / "999_fixture_store_targeted.sql"
    fixture.write_text(
        "-- 999_fixture_store_targeted.sql\n"
        f"-- ledger: silo={_A_REGISTERED_STORE}\n"
        "-- assert: no_table_privilege anon public.something SELECT\n"
        "REVOKE ALL ON public.something FROM anon;\n"
    )
    cbs._apply_file("postgresql://unused", fixture)  # must simply return


def test_unknown_silo_ref_fails_the_bootstrap_loudly(tmp_path, monkeypatch):
    # the #80463 guard: an unregistered ref is a typo until proven otherwise
    # — failing loud beats silently dropping a substrate migration from CI
    monkeypatch.setattr(cbs.psycopg, "connect", _never_connect)
    fixture = tmp_path / "999_fixture_unknown_silo.sql"
    fixture.write_text(
        "-- 999_fixture_unknown_silo.sql\n"
        "-- ledger: silo=zzzzteststore0000\n"
        "CREATE TABLE public.legacy_thing (id int);\n"
    )
    with pytest.raises(RuntimeError, match="zzzzteststore0000"):
        cbs._apply_file("postgresql://unused", fixture)


def test_substrate_declared_migration_still_applies(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cbs.psycopg, "connect",
                        lambda *a, **k: _RecordingConn(calls))
    monkeypatch.setattr(cbs, "ROOT", tmp_path)  # _apply_sql labels by relpath
    fixture = tmp_path / "999_fixture_substrate_targeted.sql"
    fixture.write_text(
        "-- 999_fixture_substrate_targeted.sql\n"
        f"-- ledger: silo={am.ORCHESTRATOR_SUBSTRATE_SILO}\n"
        "-- assert: no_table_privilege anon public.something SELECT\n"
        "REVOKE ALL ON public.something FROM anon;\n"
    )
    cbs._apply_file("postgresql://unused", fixture)
    assert any("REVOKE" in s for s in calls), "substrate-declared file was skipped"


def test_headerless_migration_still_applies(tmp_path, monkeypatch):
    # the 53 historical migrations predate the header convention; the walk
    # must keep applying them, so the skip keys ONLY off a present header
    calls: list[str] = []
    monkeypatch.setattr(cbs.psycopg, "connect",
                        lambda *a, **k: _RecordingConn(calls))
    monkeypatch.setattr(cbs, "ROOT", tmp_path)  # _apply_sql labels by relpath
    fixture = tmp_path / "001_fixture_no_header.sql"
    fixture.write_text(
        "-- 001_fixture_no_header.sql\n"
        "CREATE TABLE public.legacy_thing (id int);\n"
    )
    cbs._apply_file("postgresql://unused", fixture)
    assert any("CREATE TABLE" in s for s in calls), "headerless file was skipped"
