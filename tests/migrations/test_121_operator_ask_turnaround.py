"""Ephemeral wet-prove for migration 121: operator-ask turnaround (Musa op#34803; orch-console
#78190 plan, approved in #78198).

Runs against the ephemeral PG17 harness (tests/conftest.py pg_dsn). NEVER touches DATABASE_URL
or any live silo. The DDL is read FROM migrations/121_operator_ask_turnaround.sql (not
re-typed), so this breaks if the file drifts. A minimal operator_asks stand-in is created first,
because 121 ALTERs it.
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

MIG = pathlib.Path(__file__).resolve().parent.parent.parent / "migrations" / "121_operator_ask_turnaround.sql"


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


@pytest.fixture
def ta_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role", "console_readonly"):
            cur.execute(f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated")
        cur.execute("CREATE TABLE public.operator_asks (id bigint PRIMARY KEY, source_msg_id bigint, "
                    "closed_at timestamptz, triage_state text)")
        cur.execute(_executable_sql(MIG))
    return fresh_db


def test_operator_asks_gains_delivered_at_and_slow_paged_at(ta_db):
    with psycopg.connect(ta_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='operator_asks' "
                    "AND column_name IN ('delivered_at','slow_paged_at') ORDER BY 1")
        assert [r[0] for r in cur.fetchall()] == ["delivered_at", "slow_paged_at"]


@pytest.mark.parametrize("obj", ["operator_ask_turnaround", "operator_ask_turnaround_daily"])
def test_anon_and_authenticated_are_locked_out_console_can_read(ta_db, obj):
    with psycopg.connect(ta_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE"):
                if obj.endswith("_daily") and priv != "SELECT":
                    continue
                cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{obj}", priv))
                assert cur.fetchone()[0] is False, f"{role} has {priv} on {obj}"
        cur.execute("SELECT has_table_privilege('console_readonly', %s, 'SELECT')", (f"public.{obj}",))
        assert cur.fetchone()[0] is True


def _ins(cur, ask_id, surface, total_min, queue_min, build_min, day=datetime(2026, 10, 27, 6, tzinfo=timezone.utc)):
    cur.execute("INSERT INTO operator_asks (id, source_msg_id) VALUES (%s, %s)", (ask_id, 30000 + ask_id))
    cur.execute(
        "INSERT INTO operator_ask_turnaround (ask_id, op, surface, asked_at, delivered_at, delivered_source, "
        "queue_wait, build_to_live, total) VALUES (%s,%s,%s,%s,%s,'delivered_at',%s,%s,%s)",
        (ask_id, 30000 + ask_id, surface, day - timedelta(minutes=total_min), day,
         timedelta(minutes=queue_min), timedelta(minutes=build_min), timedelta(minutes=total_min)))


def test_delivered_source_check(ta_db):
    with psycopg.connect(ta_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO operator_asks (id) VALUES (1)")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("INSERT INTO operator_ask_turnaround (ask_id, op, surface, asked_at, delivered_at, "
                        "delivered_source, total) VALUES (1, 1, 'fleet', now(), now(), 'guess', '1 min')")


def test_daily_rollup_per_surface_and_all(ta_db):
    with psycopg.connect(ta_db, autocommit=True) as conn, conn.cursor() as cur:
        _ins(cur, 1, "cosem-adcda", 28, 10, 18)
        _ins(cur, 2, "cosem-adcda", 45, 20, 25)
        _ins(cur, 3, "cosem-adcda", 307, 238, 33)
        _ins(cur, 4, "fleet", 60, 30, 30)
        cur.execute("SELECT surface, delivered, median_total FROM operator_ask_turnaround_daily "
                    "WHERE day_sgt = '2026-10-27' ORDER BY surface")
        rows = {s: (n, med) for s, n, med in cur.fetchall()}
        assert rows["cosem-adcda"] == (3, timedelta(minutes=45))
        assert rows["fleet"] == (1, timedelta(minutes=60))
        assert rows["all"][0] == 4
        assert rows["all"][1] == timedelta(minutes=52, seconds=30)   # median of 28,45,60,307


def test_migration_is_re_run_safe(ta_db):
    with psycopg.connect(ta_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(_executable_sql(MIG))   # second apply must not error
        cur.execute("SELECT to_regclass('public.operator_ask_turnaround') IS NOT NULL")
        assert cur.fetchone()[0] is True
