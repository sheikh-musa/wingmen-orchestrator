"""Static check: migrations/drafts/hc95_broker_watch_readonly.sql grants ONLY
SELECT, on EXACTLY exec_broker_heartbeat / exec_broker_access_log /
exec_prod_ledger, to broker_watch_readonly (console ruling bus #89906)."""
from __future__ import annotations

import re
from pathlib import Path

_DRAFTS = Path(__file__).resolve().parent.parent / "migrations" / "drafts"
_SQL = (_DRAFTS / "hc95_broker_watch_readonly.sql").read_text()
_TABLES = {"exec_broker_heartbeat", "exec_broker_access_log", "exec_prod_ledger"}


def _code() -> str:
    """The SQL with -- comments removed (statements only)."""
    return "\n".join(line.split("--", 1)[0] for line in _SQL.splitlines())


def _grants() -> list:
    return re.findall(r"(?is)\bGRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+([a-z_]+)\s*;", _code())


def test_grants_select_only_on_exactly_the_three_tables():
    table_grants = [(p.strip().upper(), on.strip(), to) for p, on, to in _grants()
                    if not on.strip().upper().startswith("SCHEMA")]
    assert all(to == "broker_watch_readonly" for _, _, to in table_grants)
    assert all(priv == "SELECT" for priv, _, _ in table_grants), table_grants
    assert {on.replace("public.", "") for _, on, _ in table_grants} == _TABLES
    assert len(table_grants) == 3


def test_only_other_grant_is_usage_on_schema_public():
    others = [(p.strip().upper(), on.strip().upper()) for p, on, _ in _grants()
              if on.strip().upper().startswith("SCHEMA")]
    assert others == [("USAGE", "SCHEMA PUBLIC")]


def test_no_dml_all_sequence_or_schema_wide_grants_and_no_password():
    code = _code().upper()
    for bad in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALL PRIVILEGES", "GRANT ALL",
                "SEQUENCE", "ALL TABLES", "PASSWORD", "REFERENCES", "TRIGGER"):
        assert bad not in code, bad
    assert "BYPASSRLS" in code and "NOBYPASSRLS" in code and "NOINHERIT" in code


def test_role_creation_is_idempotent():
    assert re.search(r"IF NOT EXISTS \(SELECT 1 FROM pg_roles WHERE rolname = 'broker_watch_readonly'\)", _SQL)


def test_every_rls_enabled_table_gets_a_select_policy():
    """A grant without a policy on an RLS table is deny-all: every one of the
    three tables whose draft ENABLEs RLS must get a FOR SELECT policy here."""
    drafts = {
        "exec_broker_heartbeat": "hc95_exec_broker_heartbeat.sql",
        "exec_broker_access_log": "hc95_exec_broker_access_log.sql",
        "exec_prod_ledger": "exec_prod_ledger.sql",
    }
    code = _code()
    for table, fname in drafts.items():
        path = _DRAFTS / fname
        if not path.exists():
            continue   # e.g. exec_prod_ledger lands in a sibling PR; asserted there
        rls_on = re.search(rf"(?i)ALTER TABLE\s+{table}\s+ENABLE ROW LEVEL SECURITY", path.read_text())
        has_policy = re.search(
            rf"(?is)CREATE POLICY\s+\w+\s+ON\s+public\.{table}\s+FOR SELECT TO broker_watch_readonly\s+USING \(true\)",
            code)
        assert bool(rls_on) == bool(has_policy), table
