"""Proof-test for hc#95 B3 single-use gates (cutover spec bus #89814 §4.2-4.3):
migrations/143_hc95_exec_broker_gate_single_use.sql + vault_log.consume_gate.

The binding must be enforced by the DATABASE so a replay or a concurrent
duplicate fails closed -- proven here against the repo's sanctioned ephemeral
Postgres fixture (pg_dsn, tests/conftest.py), never a real substrate
(assert_dsn_is_not_production guards every DSN). Runs as exec_broker_role,
the broker's real DB role, not as the superuser (a superuser proof would not
show that the trigger/index bind the role that actually writes the table).
"""
from __future__ import annotations

import os
import pathlib
import sys
import threading

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import assert_dsn_is_not_production

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "lib"))

from exec_broker.vault_log import (  # noqa: E402
    UNIQUE_VIOLATION_SQLSTATE,
    VaultLogEntry,
    consume_gate,
    log_finalized,
    log_started,
)

_MIGRATIONS = _ROOT / "migrations"
_BASE = _MIGRATIONS / "drafts" / "hc95_exec_broker_access_log.sql"   # live; outside the numbered chain
_GATE_REF = _MIGRATIONS / "142_hc95_exec_broker_gate_ref.sql"
_SINGLE_USE = _MIGRATIONS / "143_hc95_exec_broker_gate_single_use.sql"


# CI MUST RUN THESE, NEVER SKIP (console #89841 ruling e): ci.yml's "Lint &
# Test" job sets WINGMEN_REQUIRE_PG_PROOFS=1, under which a missing PG server
# binary FAILS this module instead of letting tests/conftest.py's pg_dsn skip
# it. Locally (flag unset) the shared fixture's skip behaviour is unchanged.
_REQUIRE_PG = os.environ.get("WINGMEN_REQUIRE_PG_PROOFS") == "1"


@pytest.fixture
def db(request):
    if _REQUIRE_PG:
        pg_bin = os.environ.get("WINGMEN_PG17_BIN", "/usr/local/opt/postgresql@17/bin")
        for name in ("initdb", "pg_ctl", "postgres"):
            path = os.path.join(pg_bin, name)
            if not os.path.exists(path):
                pytest.fail(
                    f"WINGMEN_REQUIRE_PG_PROOFS=1 but PG server binary {path} is missing -- "
                    "the B3 single-use DB proofs must RUN in CI, never skip"
                )
    pg_dsn = request.getfixturevalue("pg_dsn")
    assert_dsn_is_not_production(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        # Same baseline restoration + platform roles as
        # tests/test_exec_broker_access_log_isolation.py (see its comments).
        cur.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
        for role in ("authenticated", "anon", "service_role"):
            cur.execute(
                f"DO $$ BEGIN CREATE ROLE {role} LOGIN; "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
        cur.execute(_BASE.read_text())
        cur.execute(_GATE_REF.read_text())
        cur.execute(_SINGLE_USE.read_text())
        # Idempotency: a re-apply must be a no-op, not an error.
        cur.execute(_GATE_REF.read_text())
        cur.execute(_SINGLE_USE.read_text())
        cur.execute("ALTER ROLE exec_broker_role LOGIN")
    yield pg_dsn


def _broker_conn(dsn: str) -> psycopg.Connection:
    assert_dsn_is_not_production(dsn)
    return psycopg.connect(dsn.replace("user=postgres", "user=exec_broker_role"), row_factory=dict_row)


def _started(cur, gate_id: str, ledger: str, gate_ref: str | None = None) -> int:
    return log_started(cur, VaultLogEntry(
        exec_ledger_id=ledger, caller="c", project_ref="syntheticbrokerqfakesilo",
        guarded_sql="update t set a = 1", reason="", gate_id=gate_id, gate_ref=gate_ref,
    ))


def test_first_consumer_binds_gate_to_its_exec_ledger_id_and_records_gate_ref(db):
    with _broker_conn(db) as conn, conn.cursor() as cur:
        row_id = _started(cur, "910001", "ledger-a", gate_ref="op#51")
        consume_gate(cur, row_id)
        conn.commit()
        cur.execute("SELECT exec_ledger_id, gate_consumed, gate_ref FROM exec_broker_access_log WHERE id = %s", (row_id,))
        assert cur.fetchone() == {"exec_ledger_id": "ledger-a", "gate_consumed": True, "gate_ref": "op#51"}


def test_second_exec_ledger_id_citing_a_consumed_gate_fails_closed(db):
    with _broker_conn(db) as conn, conn.cursor() as cur:
        consume_gate(cur, _started(cur, "910002", "ledger-a"))
        conn.commit()
        for ledger in ("ledger-b", "ledger-a"):  # another ledger AND a same-ledger retry
            row_id = _started(cur, "910002", ledger)
            conn.commit()
            with pytest.raises(psycopg.errors.UniqueViolation) as exc:
                consume_gate(cur, row_id)
            assert exc.value.sqlstate == UNIQUE_VIOLATION_SQLSTATE
            conn.rollback()


def test_concurrent_duplicate_blocks_then_fails_closed(db):
    """Two in-flight consumers of the same gate: the second blocks on the
    unique index until the first commits, then gets unique_violation --
    there is no check-then-write window."""
    result = {}
    with _broker_conn(db) as a, a.cursor() as ca, _broker_conn(db) as b, b.cursor() as cb:
        ra = _started(ca, "910003", "ledger-a")
        a.commit()
        rb = _started(cb, "910003", "ledger-b")
        b.commit()
        consume_gate(ca, ra)  # A holds the index entry, uncommitted

        def _b():
            try:
                consume_gate(cb, rb)
                result["b"] = "consumed"
            except psycopg.errors.UniqueViolation:
                result["b"] = "unique_violation"

        t = threading.Thread(target=_b)
        t.start()
        t.join(timeout=1.0)
        assert t.is_alive(), "B must block on the unique index while A is in flight"
        a.commit()
        t.join(timeout=10.0)
        assert not t.is_alive()
        assert result["b"] == "unique_violation"
        b.rollback()


def test_concurrent_duplicate_may_proceed_if_the_first_rolls_back(db):
    result = {}
    with _broker_conn(db) as a, a.cursor() as ca, _broker_conn(db) as b, b.cursor() as cb:
        ra = _started(ca, "910004", "ledger-a")
        a.commit()
        rb = _started(cb, "910004", "ledger-b")
        b.commit()
        consume_gate(ca, ra)

        def _b():
            consume_gate(cb, rb)
            result["b"] = "consumed"

        t = threading.Thread(target=_b)
        t.start()
        t.join(timeout=1.0)
        assert t.is_alive()
        a.rollback()  # A never committed its consumption (e.g. crashed pre-commit)
        t.join(timeout=10.0)
        assert result.get("b") == "consumed"
        b.commit()


def test_a_consumed_gate_cannot_be_unconsumed_or_regated_by_the_broker_role(db):
    with _broker_conn(db) as conn, conn.cursor() as cur:
        row_id = _started(cur, "910005", "ledger-a")
        consume_gate(cur, row_id)
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE exec_broker_access_log SET gate_consumed = false WHERE id = %s", (row_id,))
        conn.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE exec_broker_access_log SET gate_id = '910099' WHERE id = %s", (row_id,))
        conn.rollback()
        # finalizing the row (the broker's normal second write) still works
        cur.execute("UPDATE exec_broker_access_log SET status = 'ok', ts_end = now() WHERE id = %s", (row_id,))
        conn.commit()


def test_consume_requires_a_gate_id(db):
    with _broker_conn(db) as conn, conn.cursor() as cur:
        row_id = log_started(cur, VaultLogEntry(
            exec_ledger_id="ledger-x", caller="c", project_ref="p",
            guarded_sql="select 1", reason="", gate_id=None,
        ))
        conn.commit()  # the started row must survive the rollbacks below
        with pytest.raises(RuntimeError):
            consume_gate(cur, row_id)
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE exec_broker_access_log SET gate_consumed = true WHERE id = %s", (row_id,))
        conn.rollback()


def test_unconsumed_rows_never_collide(db):
    # Refused / errored attempts that never consumed may share a gate_id freely.
    with _broker_conn(db) as conn, conn.cursor() as cur:
        for ledger in ("l1", "l2", "l3"):
            _started(cur, "910006", ledger)
        conn.commit()
        consume_gate(cur, _started(cur, "910006", "l4"))
        conn.commit()


def test_committed_burn_is_durable_and_visible_to_another_session_before_execution(db):
    """Ruling d: the burn is COMMITTED before execution starts -- a separate
    session (another broker connection) already sees the gate consumed and
    cannot consume it, while the first row is still status='started'."""
    with _broker_conn(db) as a, a.cursor() as ca, _broker_conn(db) as b, b.cursor() as cb:
        ra = _started(ca, "910007", "ledger-a")
        consume_gate(ca, ra)
        a.commit()  # == server.py's pre-execution commit
        cb.execute("SELECT status, gate_consumed FROM exec_broker_access_log WHERE id = %s", (ra,))
        assert cb.fetchone() == {"status": "started", "gate_consumed": True}
        rb = _started(cb, "910007", "ledger-b")
        b.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            consume_gate(cb, rb)
        b.rollback()
        # ...and the execution outcome then lands on the SAME consumed row
        log_finalized(ca, ra, "error", "UPSTREAM_ERROR")
        a.commit()
        cb.execute("SELECT status, error_code, gate_consumed FROM exec_broker_access_log WHERE id = %s", (ra,))
        assert cb.fetchone() == {"status": "error", "error_code": "UPSTREAM_ERROR", "gate_consumed": True}


def test_replay_refusal_reason_is_recorded_on_the_audit_row(db):
    with _broker_conn(db) as conn, conn.cursor() as cur:
        consume_gate(cur, _started(cur, "910008", "ledger-a"))
        conn.commit()
        rb = _started(cur, "910008", "ledger-b")
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            consume_gate(cur, rb)
        conn.rollback()
        log_finalized(cur, rb, "error", "GATE_INVALID", "gate_already_consumed")
        conn.commit()
        cur.execute("SELECT error_code, error_reason, gate_consumed FROM exec_broker_access_log WHERE id = %s", (rb,))
        assert cur.fetchone() == {"error_code": "GATE_INVALID", "error_reason": "gate_already_consumed",
                                  "gate_consumed": False}
