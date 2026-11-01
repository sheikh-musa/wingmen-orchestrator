"""hc#95 exec-broker exec_broker_access_log writer (contract §6)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.vault_log import VaultLogEntry, log_finalized, log_started, record_executed  # noqa: E402


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


def test_record_executed_updates_the_same_row_with_prologue_and_executed_hash():
    cur = FakeCursor()
    record_executed(cur, 42, "SET TRANSACTION READ ONLY; ", "abc")
    sql, params = cur.calls[0]
    assert sql.startswith("UPDATE exec_broker_access_log SET executed_prologue = %s, executed_sql_sha256 = %s")
    assert "INSERT" not in sql.upper() and "sql_sha256 = %s, " not in sql.replace("executed_sql_sha256", "")
    assert params == ("SET TRANSACTION READ ONLY; ", "abc", 42)
