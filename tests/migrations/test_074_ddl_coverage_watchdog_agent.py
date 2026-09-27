"""Ephemeral wet-prove for migration 074 — registers 'ddl-coverage-watchdog'
in public.agents (bus #44135/#44139/#44140, op#22669 item 3 coverage gap).

Mirrors tests/migrations/test_073_ingest_watchdog_agent.py's shape exactly —
same incident class, same fix. This closes the loop so a future rename of
either the migration's id or ddl_coverage_watchdog.PAGE_FROM_AGENT/
PAGE_TO_AGENT without updating the other fails CI, instead of silently
reproducing bus #44035's ForeignKeyViolation for a second watchdog.
"""
from __future__ import annotations

import pathlib
import sys

import psycopg
import pytest

MIG = (pathlib.Path(__file__).resolve().parent.parent.parent
       / "migrations" / "074_ddl_coverage_watchdog_agent.sql")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
from scripts import ddl_coverage_watchdog as watchdog  # noqa: E402


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def _make_fixture_schema(cur):
    cur.execute("""
        CREATE TABLE public.agents (
          id             TEXT PRIMARY KEY,
          display_name   TEXT NOT NULL,
          repo_scope     TEXT[] NOT NULL DEFAULT '{}',
          status         TEXT NOT NULL DEFAULT 'idle',
          current_task   TEXT,
          last_heartbeat TIMESTAMPTZ,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("""
        INSERT INTO public.agents (id, display_name, repo_scope, status) VALUES
          ('sla-watchdog', 'SLA Watchdog (priority-SLA escalation daemon)', '{}', 'active')
    """)
    cur.execute("""
        CREATE TABLE public.agent_messages (
          id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          from_agent         TEXT NOT NULL REFERENCES public.agents(id),
          to_agent           TEXT NOT NULL REFERENCES public.agents(id),
          message_type       TEXT NOT NULL,
          subject            TEXT,
          body               TEXT,
          priority           TEXT,
          requires_response  BOOLEAN NOT NULL DEFAULT false,
          thread_id          UUID,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("""
        INSERT INTO public.agents (id, display_name) VALUES ('orch-console', 'orch-console')
    """)


def _apply_074(cur):
    cur.execute(_executable_sql(MIG))


@pytest.fixture
def watchdog_agent_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        _make_fixture_schema(cur)
        _apply_074(cur)
    return fresh_db


def test_074_registers_ddl_coverage_watchdog(watchdog_agent_db):
    with psycopg.connect(watchdog_agent_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT display_name, repo_scope, status, current_task, last_heartbeat "
            "FROM agents WHERE id = 'ddl-coverage-watchdog'"
        )
        row = cur.fetchone()
        assert row is not None
        display_name, repo_scope, status, current_task, last_heartbeat = row
        assert status == "active"
        assert repo_scope == []
        assert current_task is None
        assert last_heartbeat is None


def test_074_row_shape_mirrors_sla_watchdog(watchdog_agent_db):
    with psycopg.connect(watchdog_agent_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT repo_scope, status, current_task, last_heartbeat FROM agents WHERE id = 'sla-watchdog'")
        sla_shape = cur.fetchone()
        cur.execute("SELECT repo_scope, status, current_task, last_heartbeat FROM agents WHERE id = 'ddl-coverage-watchdog'")
        watchdog_shape = cur.fetchone()
        assert watchdog_shape == sla_shape


def test_074_id_matches_watchdog_page_from_agent(watchdog_agent_db):
    """The whole point of 074: ddl_coverage_watchdog.PAGE_FROM_AGENT must be a
    real row. A rename of either side without the other reproduces bus
    #44035's ForeignKeyViolation for this second watchdog."""
    with psycopg.connect(watchdog_agent_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agents WHERE id = %s", (watchdog.PAGE_FROM_AGENT,))
        assert cur.fetchone()[0] == 1


def test_074_page_to_agent_already_exists_independent_of_074(watchdog_agent_db):
    with psycopg.connect(watchdog_agent_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agents WHERE id = %s", (watchdog.PAGE_TO_AGENT,))
        assert cur.fetchone()[0] == 1


def test_074_a_page_insert_succeeds_against_the_real_fk(watchdog_agent_db):
    with psycopg.connect(watchdog_agent_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, "
            "body, priority, requires_response) VALUES (%s, %s, 'blocker', 'test', "
            "'DDL-DRIFT:test:abc', 'P1', true)",
            (watchdog.PAGE_FROM_AGENT, watchdog.PAGE_TO_AGENT),
        )
        cur.execute("SELECT count(*) FROM agent_messages")
        assert cur.fetchone()[0] == 1


def test_074_is_idempotent(watchdog_agent_db):
    with psycopg.connect(watchdog_agent_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply_074(cur)
        cur.execute("SELECT count(*) FROM agents WHERE id = 'ddl-coverage-watchdog'")
        assert cur.fetchone()[0] == 1
