"""Real-PG proof for the exec_prod ledger shipper + migrations/drafts/exec_prod_ledger.sql,
against the repo's sanctioned ephemeral Postgres fixture (pg_dsn) -- never a real
substrate. Proves the draft applies (twice), the real ON CONFLICT makes re-shipping
a no-op, and the CHECKs hold."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import psycopg
import pytest

from tests.conftest import assert_dsn_is_not_production

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("exec_prod_ledger_shipper_pg", _ROOT / "scripts" / "exec_prod_ledger_shipper.py")
shipper = importlib.util.module_from_spec(_spec)
sys.modules["exec_prod_ledger_shipper_pg"] = shipper
_spec.loader.exec_module(shipper)
_DRAFT = _ROOT / "migrations" / "drafts" / "exec_prod_ledger.sql"


@pytest.fixture
def dsn(pg_dsn):
    assert_dsn_is_not_production(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        for role in ("authenticated", "anon", "service_role"):
            c.execute(f"DO $$ BEGIN CREATE ROLE {role}; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        c.execute("DROP TABLE IF EXISTS exec_prod_ledger")
        c.execute(_DRAFT.read_text())
        c.execute(_DRAFT.read_text())   # idempotent re-apply
    yield pg_dsn
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS exec_prod_ledger")


def _line(exec_id, end=False, reason="r"):
    return json.dumps({"exec_id": exec_id, "ts_start": "2026-11-01T10:00:00+00:00",
                       "ts_end": "2026-11-01T10:00:01+00:00" if end else None, "actor": "a",
                       "project_ref": "syntheticref", "sql_sha256": "ab" * 32, "reason": reason,
                       "gate_id": None, "gate_ref": None, "transport": "broker",
                       "outcome": "ok" if end else None}) + "\n"


def test_real_ship_is_idempotent_and_derives_line_kind(dsn, tmp_path):
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(_line("e1") + _line("e1", end=True) + _line("e2", reason="y" * 900))
    kw = dict(ledger=ledger, cursor_path=tmp_path / "c.json", host="gzb",
              conn_factory=lambda: psycopg.connect(dsn))
    assert shipper.ship(**kw).shipped_lines == 3
    (tmp_path / "c.json").unlink()
    assert shipper.ship(**kw).shipped_lines == 3        # re-shipped from byte 0 ...
    with psycopg.connect(dsn) as c:
        rows = c.execute("SELECT exec_id, line_kind, char_length(reason) FROM exec_prod_ledger "
                         "ORDER BY exec_id, line_kind").fetchall()
    assert rows == [("e1", "end", 1), ("e1", "start", 1), ("e2", "start", 200)]   # ... no duplicates


def test_checks_reject_inconsistent_rows(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO exec_prod_ledger (host, exec_id, line_kind, ts_start, ts_end) "
                      "VALUES ('h', 'x', 'start', now(), now())")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO exec_prod_ledger (host, exec_id, line_kind, ts_start, reason) "
                      "VALUES ('h', 'y', 'start', now(), repeat('z', 201))")
