"""TDD proof for scripts/extend_boot_briefing_governance_floor_arm.py
(op#35246 item 1, console bus #80912/#80914/#80915).

Exercises the script against a minimal synthetic boot_briefing-shaped view
(one placeholder pre-existing arm) in the ephemeral harness -- never the real
substrate's boot_briefing, which this script only ever touches via its own
--apply run sent to console's gate, same as extend_boot_briefing_arm.py's
data_provenance_flag arm went in.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import extend_boot_briefing_governance_floor_arm as ext  # noqa: E402


def _ago(hours: int) -> str:
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).isoformat()


@pytest.fixture
def briefing_db(pg_dsn):
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        cur.execute(
            """CREATE TABLE governance_floor (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 sentence text NOT NULL, op_ref text NOT NULL,
                 predicate_sql text NOT NULL, enforced_in_code boolean NOT NULL DEFAULT false,
                 decision_ref text, retired_at timestamptz, retired_op_ref text,
                 created_at timestamptz NOT NULL DEFAULT now()
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
        cur.execute("CREATE TABLE some_existing_thing (id bigint PRIMARY KEY, name text)")
        cur.execute("INSERT INTO some_existing_thing VALUES (1, 'a'), (2, 'b')")
        # a minimal boot_briefing with exactly one pre-existing arm, matching
        # the real view's per-arm shape (source/key/context) closely enough
        # for arm_sources()'s regex + the row-count-preserved check.
        cur.execute(
            """CREATE VIEW boot_briefing AS
               select 'some_existing_thing'::text as source,
                      id::text as key,
                      json_build_object('name', name) as context
               from some_existing_thing"""
        )
    return pg_dsn


def test_arm_sources_parses_multiple_arms():
    body = (
        "select 'arm_one'::text as source, 'k'::text as key, '{}'::json as context\n"
        "union all\n"
        "select 'arm_two'::text as source, 'k'::text as key, '{}'::json as context"
    )
    assert ext.arm_sources(body) == ["arm_one", "arm_two"]


def test_dry_run_leaves_the_view_unchanged(briefing_db):
    assert ext.main(["--dsn", briefing_db]) == 0
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select pg_get_viewdef('boot_briefing'::regclass, true)")
        body = cur.fetchone()[0]
    assert ext.NEW_ARM_SOURCE not in ext.arm_sources(body)


def test_apply_adds_exactly_one_new_arm(briefing_db):
    assert ext.main(["--dsn", briefing_db, "--apply"]) == 0
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select pg_get_viewdef('boot_briefing'::regclass, true)")
        body = cur.fetchone()[0]
    arms = ext.arm_sources(body)
    assert set(arms) == {"some_existing_thing", "governance_floor_status"}


def test_apply_preserves_the_pre_existing_arms_row_count(briefing_db):
    ext.main(["--dsn", briefing_db, "--apply"])
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select count(*) from boot_briefing where source = 'some_existing_thing'")
        assert cur.fetchone()[0] == 2


def test_apply_is_idempotent_on_rerun(briefing_db):
    assert ext.main(["--dsn", briefing_db, "--apply"]) == 0
    assert ext.main(["--dsn", briefing_db, "--apply"]) == 0
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select pg_get_viewdef('boot_briefing'::regclass, true)")
        body = cur.fetchone()[0]
    arms = ext.arm_sources(body)
    assert arms.count("governance_floor_status") == 1


def test_never_checked_floor_reports_not_yet_checked(briefing_db):
    with psycopg.connect(briefing_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
            "VALUES ('never checked', 'op#1', 'SELECT true')"
        )
    ext.main(["--dsn", briefing_db, "--apply"])
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select context from boot_briefing where source = 'governance_floor_status' and key = 'op#1'")
        ctx = cur.fetchone()[0]
    assert ctx["status"] == "NOT_YET_CHECKED"


def test_recently_passed_floor_reports_pass(briefing_db):
    with psycopg.connect(briefing_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
            "VALUES ('recently passed', 'op#2', 'SELECT true') RETURNING id"
        )
        fid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, at) "
            "VALUES (%s, 'checked_pass', 'reconciler', 'op#2', %s)",
            (fid, _ago(hours=1)),
        )
    ext.main(["--dsn", briefing_db, "--apply"])
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select context from boot_briefing where source = 'governance_floor_status' and key = 'op#2'")
        ctx = cur.fetchone()[0]
    assert ctx["status"] == "PASS"


def test_recently_failed_floor_reports_fail(briefing_db):
    with psycopg.connect(briefing_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
            "VALUES ('recently failed', 'op#3', 'SELECT true') RETURNING id"
        )
        fid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, at) "
            "VALUES (%s, 'checked_fail', 'reconciler', 'op#3', %s)",
            (fid, _ago(hours=1)),
        )
    ext.main(["--dsn", briefing_db, "--apply"])
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select context from boot_briefing where source = 'governance_floor_status' and key = 'op#3'")
        ctx = cur.fetchone()[0]
    assert ctx["status"] == "FAIL"


def test_old_pass_past_12h_reports_stale_never_pass(briefing_db):
    # console bus #80915: 6h cadence, STALE past 2x = 12h -- an old PASS must
    # never be shown as current once it's stale (same lesson as #80659/#80664,
    # now enforced where live status actually lives).
    with psycopg.connect(briefing_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql) "
            "VALUES ('stale pass', 'op#4', 'SELECT true') RETURNING id"
        )
        fid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO governance_floor_audit (floor_id, event, actor, op_ref, at) "
            "VALUES (%s, 'checked_pass', 'reconciler', 'op#4', %s)",
            (fid, _ago(hours=13)),
        )
    ext.main(["--dsn", briefing_db, "--apply"])
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select context from boot_briefing where source = 'governance_floor_status' and key = 'op#4'")
        ctx = cur.fetchone()[0]
    assert ctx["status"] == "STALE"


def test_retired_floor_excluded_from_the_arm(briefing_db):
    with psycopg.connect(briefing_db, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO governance_floor (sentence, op_ref, predicate_sql, retired_at, retired_op_ref) "
            "VALUES ('lifted', 'op#5', 'SELECT true', now(), 'op#99')"
        )
    ext.main(["--dsn", briefing_db, "--apply"])
    with psycopg.connect(briefing_db) as c, c.cursor() as cur:
        cur.execute("select count(*) from boot_briefing where source = 'governance_floor_status' and key = 'op#5'")
        assert cur.fetchone()[0] == 0
