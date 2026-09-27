"""Ephemeral wet-prove for migration 073 — registers 'ingest-watchdog' in
public.agents (Musa op#22669 item 3 fallout, bus #44035).

PR #182's runtime pinned-channel-drift check pages the operator with
from_agent='ingest-watchdog' on its very first live cycle. The live substrate
has agent_messages_from_agent_fkey/_to_agent_fkey REFERENCES agents(id) — no
migration had ever created that row, so the FIRST real page failed with
ForeignKeyViolation. tests/test_ingest_pinned_channel_drift.py's harness had
no agents table and no FK at all, so nothing in CI could have caught this.

This file proves two separate things:
  1. Migration 073 itself is a correct, idempotent, real-shaped agents insert
     (wet-proved against the ephemeral PG17 harness, never a live silo).
  2. The row it creates actually matches what nervous_system/ingest.py pages
     as — closing the loop so a future rename of either the migration's id or
     ingest.PAGE_FROM_AGENT/PAGE_TO_AGENT without updating the other fails CI,
     instead of silently reproducing this exact incident.
"""
from __future__ import annotations

import pathlib
import sys

import psycopg
import pytest

MIG = (pathlib.Path(__file__).resolve().parent.parent.parent
       / "migrations" / "073_ingest_watchdog_agent.sql")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
from nervous_system import ingest  # noqa: E402


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
    # sla-watchdog pre-exists on the live substrate (the shape 073 mirrors) —
    # seeded here so a test can compare row shapes without hardcoding them twice.
    cur.execute("""
        INSERT INTO public.agents (id, display_name, repo_scope, status) VALUES
          ('sla-watchdog', 'SLA Watchdog (priority-SLA escalation daemon)', '{}', 'active')
    """)
    # agent_messages + its two FKs — the exact constraint that rejected the
    # live page (agent_messages_from_agent_fkey/_to_agent_fkey).
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
    # orch-console (PAGE_TO_AGENT) is a pre-existing fleet body, not created by
    # 073 — seeded here the same way it already exists on the live substrate.
    cur.execute("""
        INSERT INTO public.agents (id, display_name) VALUES ('orch-console', 'orch-console')
    """)


def _apply_073(cur):
    cur.execute(_executable_sql(MIG))


@pytest.fixture
def ingest_agent_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        _make_fixture_schema(cur)
        _apply_073(cur)
    return fresh_db


def test_073_registers_ingest_watchdog(ingest_agent_db):
    with psycopg.connect(ingest_agent_db) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT display_name, repo_scope, status, current_task, last_heartbeat "
            "FROM agents WHERE id = 'ingest-watchdog'"
        )
        row = cur.fetchone()
        assert row is not None
        display_name, repo_scope, status, current_task, last_heartbeat = row
        assert status == "active"
        assert repo_scope == []
        # mirrors sla-watchdog: not a polled/heartbeating lane, so both stay NULL.
        assert current_task is None
        assert last_heartbeat is None


def test_073_row_shape_mirrors_sla_watchdog(ingest_agent_db):
    with psycopg.connect(ingest_agent_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT repo_scope, status, current_task, last_heartbeat FROM agents WHERE id = 'sla-watchdog'")
        sla_shape = cur.fetchone()
        cur.execute("SELECT repo_scope, status, current_task, last_heartbeat FROM agents WHERE id = 'ingest-watchdog'")
        ingest_shape = cur.fetchone()
        assert ingest_shape == sla_shape


def test_073_id_matches_ingest_py_page_from_agent(ingest_agent_db):
    """The whole point of 073: ingest.PAGE_FROM_AGENT must be a real row. A
    rename of either side without the other reproduces bus #44035's
    ForeignKeyViolation on the very next live page."""
    with psycopg.connect(ingest_agent_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agents WHERE id = %s", (ingest.PAGE_FROM_AGENT,))
        assert cur.fetchone()[0] == 1


def test_073_page_to_agent_already_exists_independent_of_073(ingest_agent_db):
    """073 does NOT create orch-console -- it must already exist. Confirms the
    fixture models the live substrate's actual pre-existing state, not a
    tautology where 073 quietly creates both sides of the FK itself."""
    with psycopg.connect(ingest_agent_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agents WHERE id = %s", (ingest.PAGE_TO_AGENT,))
        assert cur.fetchone()[0] == 1


def test_073_a_page_insert_succeeds_against_the_real_fk(ingest_agent_db):
    """End-to-end: an INSERT shaped exactly like _page_pinned_drift_once's real
    write must not raise ForeignKeyViolation now that 073 has run."""
    with psycopg.connect(ingest_agent_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, "
            "body, priority, requires_response) VALUES (%s, %s, 'blocker', 'test', "
            "'PINNED-CHANNEL-DRIFT:test:host', 'P1', true)",
            (ingest.PAGE_FROM_AGENT, ingest.PAGE_TO_AGENT),
        )
        cur.execute("SELECT count(*) FROM agent_messages")
        assert cur.fetchone()[0] == 1


def test_073_is_idempotent(ingest_agent_db):
    with psycopg.connect(ingest_agent_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply_073(cur)  # re-apply must not raise / must not duplicate
        cur.execute("SELECT count(*) FROM agents WHERE id = 'ingest-watchdog'")
        assert cur.fetchone()[0] == 1
