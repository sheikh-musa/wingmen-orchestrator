"""TDD proof for scripts/governance_floor_reconcile.py (op#35246 Phase 1a,
PR#555 reports/joint-plan-20261028/PLAN.md section 2: "a small reconciler
(same shape as work_item_reconcile.py) evaluates every predicate_sql on a
schedule and records pass/fail."

Departs from work_item_reconcile.py's --repair flag deliberately: there is no
code-level repair for a failing governance floor (a human resolves the
underlying violation), so this reconciler only ever reads + records; --record
is the only write path, gated the same way --repair is gated there (default
read-only).

Runs entirely against tests/conftest.py's ephemeral pg_dsn. NEVER touches
DATABASE_URL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import governance_floor_reconcile as gfr  # noqa: E402


@pytest.fixture
def floor_db(pg_dsn):
    """A clean public schema per test, same reset shape as
    tests/migrations/conftest.py's fresh_db (not importable here -- this file
    lives outside tests/migrations/, so the reset is inlined)."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE governance_floor (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 sentence text NOT NULL, op_ref text NOT NULL,
                 predicate_sql text NOT NULL, enforced_in_code boolean NOT NULL DEFAULT false,
                 decision_ref text, created_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        cur.execute(
            """CREATE TABLE governance_floor_audit (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 floor_id bigint REFERENCES governance_floor(id),
                 event text NOT NULL, actor text NOT NULL, op_ref text NOT NULL,
                 at timestamptz NOT NULL DEFAULT now(), note text
               )"""
        )
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
            "VALUES ('always true', 'op#1', 'SELECT true')"
        )
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
            "VALUES ('always false', 'op#2', 'SELECT false')"
        )
    return pg_dsn


def _q(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_evaluate_returns_one_result_per_floor(floor_db):
    results = gfr.evaluate(floor_db)
    by_op_ref = {r["op_ref"]: r["passing"] for r in results}
    assert by_op_ref == {"op#1": True, "op#2": False}


def test_dry_run_default_writes_no_audit_rows(floor_db):
    exit_code = gfr.main(["--dsn", floor_db])
    assert exit_code == 2  # one floor failing
    assert _q(floor_db, "SELECT count(*) FROM governance_floor_audit") == [(0,)]


def test_record_writes_one_audit_row_per_floor(floor_db):
    exit_code = gfr.main(["--dsn", floor_db, "--record"])
    assert exit_code == 2
    rows = _q(floor_db, "SELECT op_ref, event FROM governance_floor_audit ORDER BY op_ref")
    assert rows == [("op#1", "checked_pass"), ("op#2", "checked_fail")]


def test_record_actor_is_the_reconciler_itself(floor_db):
    gfr.main(["--dsn", floor_db, "--record"])
    rows = _q(floor_db, "SELECT DISTINCT actor FROM governance_floor_audit")
    assert rows == [("governance_floor_reconcile.py",)]


def test_exit_code_0_when_all_floors_passing(floor_db):
    with psycopg.connect(floor_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM governance_floor WHERE op_ref = 'op#2'")
    assert gfr.main(["--dsn", floor_db]) == 0


def test_malformed_predicate_recorded_as_fail_not_a_crash(floor_db):
    with psycopg.connect(floor_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
            "VALUES ('broken', 'op#3', 'SELECT this is not valid sql')"
        )
    results = gfr.evaluate(floor_db)
    broken = [r for r in results if r["op_ref"] == "op#3"][0]
    assert broken["passing"] is False
    assert broken["error"]


def test_json_output(floor_db, capsys):
    gfr.main(["--dsn", floor_db, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 2
    assert out["passing"] == 1
