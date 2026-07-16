"""Runner authority tests (EXEC-4): the runner role cannot flip execution_status
or write strategic_decisions, and can only touch work-items (not mint them)."""
from __future__ import annotations

import psycopg


def _priv(cur, role, obj, priv):
    cur.execute("select has_table_privilege(%s, %s, %s)", (role, obj, priv))
    return cur.fetchone()[0]


def test_runner_role_privileges(substrate):
    dsn, schema, role = substrate["dsn"], substrate["schema"], substrate["role"]
    items = f"{schema}.exec_work_items"
    ledger = f"{schema}.exec_delivery_ledger"
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        # Cannot write grants/decisions -> cannot flip execution_status (EXEC-4).
        assert _priv(cur, role, "public.strategic_decisions", "UPDATE") is False
        assert _priv(cur, role, "public.strategic_decisions", "INSERT") is False
        assert _priv(cur, role, "public.strategic_decisions", "DELETE") is False
        # May READ grants (to re-check them each cycle).
        assert _priv(cur, role, "public.strategic_decisions", "SELECT") is True
        # May claim + update work-items, but NOT mint them (no INSERT).
        assert _priv(cur, role, items, "SELECT") is True
        assert _priv(cur, role, items, "UPDATE") is True
        assert _priv(cur, role, items, "INSERT") is False
        assert _priv(cur, role, items, "DELETE") is False
        # May append delivery proofs.
        assert _priv(cur, role, ledger, "INSERT") is True
        assert _priv(cur, role, ledger, "SELECT") is True


def test_runner_role_live_denied_flipping_execution_status(substrate):
    """Best-effort LIVE proof via SET ROLE: assuming the runner role, an UPDATE of
    strategic_decisions.execution_status is denied. Skipped if the pooler user
    cannot SET ROLE (no membership) — the catalog test above is authoritative and
    non-mutating. We do NOT grant membership (that would mutate the pooler user)."""
    import pytest

    dsn, role = substrate["dsn"], substrate["role"]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(f"set role {role}")
        except psycopg.Error:
            conn.rollback()
            pytest.skip("SET ROLE not permitted for this DB user (no membership)")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    "update public.strategic_decisions set execution_status='granted' "
                    "where decision_ref = 'no-such-ref'")
        finally:
            conn.rollback()
