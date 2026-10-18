"""Isolation proof-test for the hc#95 vault_access_log draft (migrations/drafts/
hc95_vault_access_log.sql) — the gate is: PUBLIC has NOTHING on this table, and
exec_broker_role can INSERT/UPDATE but never DELETE — proven, not asserted.

Runs against the repo's OWN sanctioned ephemeral Postgres fixture (pg_dsn,
tests/conftest.py) — never a manually-set DSN env var, never the real
substrate (assert_dsn_is_not_production guards every DSN this test builds).
Mirrors tests/test_life_graph_isolation.py's pattern.
"""
from __future__ import annotations

import pathlib

import psycopg
import pytest

from tests.conftest import assert_dsn_is_not_production

DRAFT = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "drafts" / "hc95_vault_access_log.sql"


@pytest.fixture
def db(pg_dsn):
    assert_dsn_is_not_production(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            # pg_dsn is session-scoped and shared with every other test module.
            # Some of them (e.g. test_ingest_pinned_channel_drift.py) rebuild
            # 'public' via DROP SCHEMA public CASCADE; CREATE SCHEMA public --
            # unlike the schema initdb bootstraps, a schema created that way
            # has NO implicit USAGE grant to PUBLIC, so if one of those tests
            # ran earlier in this session, a non-superuser role created below
            # can't even resolve an unqualified table name (reported as
            # "does not exist", not a permission error). Every real Postgres
            # database -- including the actual deploy target -- already has
            # that USAGE grant; restoring it here just re-establishes the
            # normal baseline this test needs, and is independent of (does
            # not weaken) the table-level REVOKE ALL this test proves below.
            cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
            cur.execute(DRAFT.read_text())
            # Ephemeral-only: trust auth means no password is needed to connect
            # as these roles; the real apply mints exec_broker_role's actual
            # LOGIN/password out-of-band (orch-console, never committed).
            cur.execute("ALTER ROLE exec_broker_role LOGIN")
            cur.execute(
                "DO $$ BEGIN CREATE ROLE vault_log_public_test LOGIN; "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
        yield pg_dsn


def _as_role(dsn: str, role: str) -> str:
    assert_dsn_is_not_production(dsn)
    return dsn.replace("user=postgres", f"user={role}")


def test_public_has_no_select(db):
    dsn = _as_role(db, "vault_log_public_test")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("SELECT * FROM vault_access_log")


def test_public_has_no_insert(db):
    dsn = _as_role(db, "vault_log_public_test")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO vault_access_log (exec_ledger_id, caller, project_ref, sql_sha256, reason, status) "
                "VALUES ('e', 'c', 'p', 's', '', 'started')"
            )


def test_exec_broker_role_can_insert_and_update(db):
    dsn = _as_role(db, "exec_broker_role")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vault_access_log (exec_ledger_id, caller, project_ref, sql_sha256, reason, gate_id, status) "
            "VALUES ('e1', 'c', 'p', 's', '', NULL, 'started') RETURNING id"
        )
        row_id = cur.fetchone()[0]
        cur.execute("UPDATE vault_access_log SET status = 'ok', ts_end = now() WHERE id = %s", (row_id,))
        cur.execute("SELECT status FROM vault_access_log WHERE id = %s", (row_id,))
        assert cur.fetchone()[0] == "ok"


def test_exec_broker_role_cannot_delete(db):
    dsn = _as_role(db, "exec_broker_role")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vault_access_log (exec_ledger_id, caller, project_ref, sql_sha256, reason, gate_id, status) "
            "VALUES ('e2', 'c', 'p', 's', '', NULL, 'started') RETURNING id"
        )
        row_id = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM vault_access_log WHERE id = %s", (row_id,))


def test_status_check_constraint_rejects_unknown_status(db):
    dsn = _as_role(db, "exec_broker_role")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO vault_access_log (exec_ledger_id, caller, project_ref, sql_sha256, reason, status) "
                "VALUES ('e3', 'c', 'p', 's', '', 'bogus-status')"
            )
