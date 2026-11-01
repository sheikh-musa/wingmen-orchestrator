"""hc#95 exec-broker exec_broker_access_log writer (contract §6)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

import pytest  # noqa: E402

from exec_broker.vault_log import (  # noqa: E402
    VaultLogEntry,
    consume_gate,
    log_finalized,
    log_started,
    record_executed,
)


class FakeCursor:
    def __init__(self, fetchone_result=None):
        self.fetchone_result = fetchone_result
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.fetchone_result


def test_log_started_inserts_with_sha256_and_returns_id():
    cur = FakeCursor(fetchone_result={"id": 42})
    entry = VaultLogEntry(
        exec_ledger_id="e1", caller="cc-irsyad-2", project_ref="goumlynecruxrlmzlntp",
        guarded_sql="select 1", reason="reason", gate_id=None,
    )
    row_id = log_started(cur, entry)
    assert row_id == 42

    sql, params = cur.calls[0]
    assert sql.strip().upper().startswith("INSERT INTO EXEC_BROKER_ACCESS_LOG")
    assert "RETURNING id" in sql
    column_list = sql.split("(", 1)[1].split(")", 1)[0]
    assert "id" not in [c.strip() for c in column_list.split(",")]
    assert params[3] == hashlib.sha256(b"select 1").hexdigest()


def test_log_started_never_passes_an_explicit_id():
    cur = FakeCursor(fetchone_result={"id": 1})
    entry = VaultLogEntry(
        exec_ledger_id="e1", caller="c", project_ref="p", guarded_sql="select 1", reason="", gate_id=None,
    )
    log_started(cur, entry)
    sql, params = cur.calls[0]
    assert "VALUES" in sql
    # 6 bound params: exec_ledger_id, caller, project_ref, sql_sha256, reason, gate_id -- no id
    assert len(params) == 6


def test_log_finalized_updates_same_row_never_inserts():
    cur = FakeCursor()
    log_finalized(cur, 42, "ok", None)
    sql, params = cur.calls[0]
    assert sql.strip().upper().startswith("UPDATE")
    assert "INSERT" not in sql.upper()
    assert 42 in params


def test_log_finalized_error_records_code():
    cur = FakeCursor()
    log_finalized(cur, 42, "error", "GATE_INVALID")
    _, params = cur.calls[0]
    assert "GATE_INVALID" in params
    assert "error" in params


# --- B2/--ref (bus #89814 §3.5): the ledger row records gate_ref ------------

def test_log_started_without_gate_ref_is_byte_identical_to_pre_amendment():
    cur = FakeCursor(fetchone_result={"id": 1})
    entry = VaultLogEntry(
        exec_ledger_id="e1", caller="c", project_ref="p", guarded_sql="select 1", reason="", gate_id=None,
    )
    log_started(cur, entry)
    sql, params = cur.calls[0]
    assert sql == (
        "INSERT INTO exec_broker_access_log "
        "(exec_ledger_id, caller, project_ref, sql_sha256, reason, gate_id, status, ts_start) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'started', now()) RETURNING id"
    )
    assert "gate_ref" not in sql
    assert len(params) == 6


def test_log_started_records_gate_ref_when_present():
    cur = FakeCursor(fetchone_result={"id": 7})
    entry = VaultLogEntry(
        exec_ledger_id="e1", caller="c", project_ref="p", guarded_sql="update t set a = 1",
        reason="", gate_id="56485", gate_ref="op#51",
    )
    assert log_started(cur, entry) == 7
    sql, params = cur.calls[0]
    column_list = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert "gate_ref" in column_list and "id" not in column_list
    assert len(params) == 7
    assert params[column_list.index("gate_ref")] == "op#51"
    assert params[column_list.index("gate_id")] == "56485"


# --- B3 single-use (bus #89814 §4.2-4.3) ------------------------------------

def test_consume_gate_is_a_conditional_update_of_the_same_row():
    cur = FakeCursor()
    cur.rowcount = 1
    consume_gate(cur, 42)
    sql, params = cur.calls[0]
    assert sql.strip().upper().startswith("UPDATE EXEC_BROKER_ACCESS_LOG SET GATE_CONSUMED = TRUE")
    assert "gate_id IS NOT NULL" in sql and "gate_consumed = false" in sql
    assert params == (42,)


def test_consume_gate_refuses_when_no_row_was_consumed():
    cur = FakeCursor()
    cur.rowcount = 0
    with pytest.raises(RuntimeError):
        consume_gate(cur, 42)


def test_log_finalized_without_reason_is_byte_identical_to_pre_b3():
    cur = FakeCursor()
    log_finalized(cur, 42, "error", "GATE_INVALID")
    sql, params = cur.calls[0]
    assert sql == "UPDATE exec_broker_access_log SET status = %s, error_code = %s, ts_end = now() WHERE id = %s"
    assert params == ("error", "GATE_INVALID", 42)


def test_log_finalized_records_the_reason_when_given():
    cur = FakeCursor()
    log_finalized(cur, 42, "error", "GATE_INVALID", "gate_already_consumed")
    sql, params = cur.calls[0]
    assert "error_reason = %s" in sql
    assert params == ("error", "GATE_INVALID", "gate_already_consumed", 42)
def test_record_executed_updates_the_same_row_with_prologue_and_executed_hash():
    cur = FakeCursor()
    record_executed(cur, 42, "SET TRANSACTION READ ONLY; ", "abc")
    sql, params = cur.calls[0]
    assert sql.startswith("UPDATE exec_broker_access_log SET executed_prologue = %s, executed_sql_sha256 = %s")
    assert "INSERT" not in sql.upper() and "sql_sha256 = %s, " not in sql.replace("executed_sql_sha256", "")
    assert params == ("SET TRANSACTION READ ONLY; ", "abc", 42)



# --- queue row 272 ------------------------------------------------------------

def test_log_started_records_boot_id_when_present():
    cur = FakeCursor(fetchone_result={"id": 9})
    log_started(cur, VaultLogEntry(exec_ledger_id="e", caller="c", project_ref="p", guarded_sql="select 1",
                                   reason="", gate_id=None, boot_id="11111111-1111-1111-1111-111111111111"))
    sql, params = cur.calls[0]
    cols = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert cols[-3:] == ["boot_id", "status", "ts_start"] and params[-1] == "11111111-1111-1111-1111-111111111111"
    assert "gate_ref" not in cols and len(params) == 7


def test_in_doubt_sql_never_touches_ts_end_or_finalizes():
    from exec_broker.vault_log import IN_DOUBT_STATUS, MARK_IN_DOUBT_SQL
    assert IN_DOUBT_STATUS == "in_doubt"
    sql = MARK_IN_DOUBT_SQL
    assert "SET status = 'in_doubt', in_doubt_at = now()" in sql
    set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
    assert "ts_end" not in set_clause and "'ok'" not in sql and "'error'" not in sql
    assert "WHERE status = 'started' AND ts_end IS NULL" in sql
    assert "boot_id IS DISTINCT FROM %(boot)s::uuid" in sql
