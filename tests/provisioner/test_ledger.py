"""provisioner_access_log — the substrate-side audit ledger (plan §6/§9).

Mirrors exec_broker's vault_log two-phase SAME-ROW pattern (INSERT started ->
UPDATE ok/error, id via RETURNING, never hand-written) and adds the
one-artifact-per-gate rule: a gate that already drove a 'started' or 'ok'
provision/revert is spent — only rows that ended in 'error' leave the gate
retryable. The partial unique index in the DDL enforces the 'ok' half at the
DB level, belt-and-braces under the code check.
"""
import re
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import ledger  # noqa: E402
from provisioner.registry import Refuse  # noqa: E402

_DRAFT = ORCH_DIR / "migrations" / "drafts" / "ceayj_provisioner_access_log.sql"


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def _entry(**overrides):
    base = dict(verb="provision", client_key="acme", silo_ref="r" * 20,
                bundle_sha256="a" * 64, gate_id="78463", peer_uid="501",
                reason="client onboarding")
    base.update(overrides)
    return ledger.AccessLogEntry(**base)


def test_log_started_inserts_once_and_returns_id():
    cur = FakeCursor(rows=[{"id": 42}])
    entry = _entry()
    row_id = ledger.log_started(cur, entry)
    assert row_id == 42  # read back via RETURNING, never computed locally
    assert len(cur.calls) == 1
    sql, params = cur.calls[0]
    assert sql.startswith("INSERT INTO provisioner_access_log")
    assert "RETURNING id" in sql
    assert "status" in sql and "'started'" in sql
    for field in ("verb", "client_key", "silo_ref", "bundle_sha256", "gate_id",
                  "peer_uid", "reason"):
        assert getattr(entry, field) in params


def test_log_finalized_updates_the_same_row_never_inserts():
    cur = FakeCursor()
    ledger.log_finalized(cur, 42, "ok")
    ledger.log_finalized(cur, 43, "error", error_code="GATE_INVALID")
    assert len(cur.calls) == 2
    for sql, params in cur.calls:
        assert sql.startswith("UPDATE provisioner_access_log")
        assert "WHERE id = %s" in sql
        assert "INSERT" not in sql
    assert cur.calls[0][1][0] == "ok"
    assert cur.calls[1][1][0] == "error"
    with pytest.raises(Refuse):  # only ok/error are finalizable
        ledger.log_finalized(FakeCursor(), 42, "started")


def test_gate_already_used_spends_on_started_or_ok():
    # ok -> spent
    cur = FakeCursor(rows=[{"one": 1}])
    assert ledger.gate_already_used(cur, "78463", "provision") is True
    sql, params = cur.calls[0]
    assert "provisioner_access_log" in sql and "gate_id" in sql
    assert params == ("78463", "provision")
    # nothing -> fresh
    assert ledger.gate_already_used(FakeCursor(), "78463", "provision") is False


def test_gate_already_used_predicate_treats_started_and_ok_as_spent():
    # a FakeCursor can't prove the predicate, so the query text is asserted:
    # 'error' rows must NOT appear in the spent set (a failed attempt never
    # spent the gate — retry is allowed); 'started' must (in-flight or
    # crashed mid-apply: operator reconciles, the gate stays spent)
    cur = FakeCursor()
    ledger.gate_already_used(cur, "78463", "provision")
    sql = cur.calls[0][0]
    assert "IN ('started', 'ok')" in sql


def test_gate_already_used_excludes_this_attempts_own_row():
    # the server logs started BEFORE the spent-check (audit-first), so the
    # check must exclude the row it just inserted — by id — or every call
    # would see its own 'started' row and refuse itself
    cur = FakeCursor()
    ledger.gate_already_used(cur, "78463", "provision", exclude_row_id=7)
    sql, params = cur.calls[0]
    assert "id != %s" in sql
    assert params == ("78463", "provision", 7)


def test_draft_ddl_covers_every_column_the_ledger_writes():
    draft = _DRAFT.read_text()
    for col in ("verb", "client_key", "silo_ref", "bundle_sha256", "gate_id",
                "peer_uid", "reason", "status", "error_code"):
        assert re.search(rf"^\s+{col}\s+\w+", draft, re.MULTILINE), f"draft lacks column {col}"
    # one-artifact-per-gate, DB-enforced half: partial unique index on gate
    assert re.search(r"UNIQUE INDEX.*ON (?:public\.)?provisioner_access_log \(gate_id, verb\).*WHERE status = 'ok'",
                     draft, re.DOTALL)
    # the substrate lockdown convention (CAI-RESP-1397 #5): asserts or
    # apply_migration.py refuses the REVOKE outright
    assert "assert: no_table_privilege anon public.provisioner_access_log SELECT" in draft
    assert "assert: no_table_privilege authenticated public.provisioner_access_log DELETE" in draft
    assert "ledger: silo=" in draft  # apply routing header, same as 103
    assert "DROP TABLE" in draft and "DROP ROLE" in draft  # REVERT twin present
