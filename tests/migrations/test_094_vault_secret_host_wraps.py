"""Ephemeral wet-prove for migration 094 — vault_secret_host_wraps (bus
#53277/#53351/#53588, orch-console P1, Musa op#26219 hub GLM move).

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo. Proves the DDL (065 then 094) applies
cleanly, is idempotent, the FK to vault_secrets(name) is enforced, and the table
is genuinely unreadable by anon/authenticated (RLS deny-all + REVOKE, same
has_table_privilege check apply_migration.py's own `-- assert: no_table_privilege`
lines run for real against the live silo).
"""
from __future__ import annotations

import pathlib

import psycopg
import pytest

MIG_065 = (pathlib.Path(__file__).resolve().parent.parent.parent
           / "migrations" / "065_secrets_vault.sql")
MIG_094 = (pathlib.Path(__file__).resolve().parent.parent.parent
           / "migrations" / "094_vault_secret_host_wraps.sql")


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def _split_statements(sql: str):
    out, buf, in_dollar, in_str = [], [], False, False
    i = 0
    while i < len(sql):
        if not in_str and sql[i:i + 2] == "$$":
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = sql[i]
        if not in_dollar and ch == "'":
            if in_str and sql[i:i + 2] == "''":
                buf.append("''")
                i += 2
                continue
            in_str = not in_str
            buf.append(ch)
            i += 1
            continue
        if ch == ";" and not in_dollar and not in_str:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _apply(cur, path: pathlib.Path) -> None:
    for stmt in _split_statements(_executable_sql(path)):
        cur.execute(stmt)


@pytest.fixture
def vault_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(f'DROP ROLE IF EXISTS {role}')
            cur.execute(f'CREATE ROLE {role}')
        _apply(cur, MIG_065)
        _apply(cur, MIG_094)
    return fresh_db


def test_094_table_created_with_fk_to_vault_secrets(vault_db):
    with psycopg.connect(vault_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vault_secrets (name, ciphertext, wrapped_dek, kek_host, created_by_agent) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("test-secret", b"\x00" * 28, b"\x00" * 28, "mini", "test"),
        )
        cur.execute(
            "INSERT INTO vault_secret_host_wraps (secret_name, kek_host, wrapped_dek, created_by_agent) "
            "VALUES (%s, %s, %s, %s)",
            ("test-secret", "hub-vps", b"\x00" * 28, "test"),
        )
        cur.execute(
            "SELECT kek_host FROM vault_secret_host_wraps WHERE secret_name = %s",
            ("test-secret",),
        )
        assert cur.fetchone()[0] == "hub-vps"


def test_094_fk_rejects_wrap_for_nonexistent_secret(vault_db):
    with psycopg.connect(vault_db, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute(
                "INSERT INTO vault_secret_host_wraps (secret_name, kek_host, wrapped_dek, created_by_agent) "
                "VALUES (%s, %s, %s, %s)",
                ("no-such-secret", "hub-vps", b"\x00" * 28, "test"),
            )


def test_094_cascade_deletes_wraps_when_secret_deleted(vault_db):
    with psycopg.connect(vault_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vault_secrets (name, ciphertext, wrapped_dek, kek_host, created_by_agent) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("test-secret-2", b"\x00" * 28, b"\x00" * 28, "mini", "test"),
        )
        cur.execute(
            "INSERT INTO vault_secret_host_wraps (secret_name, kek_host, wrapped_dek, created_by_agent) "
            "VALUES (%s, %s, %s, %s)",
            ("test-secret-2", "hub-vps", b"\x00" * 28, "test"),
        )
        cur.execute("DELETE FROM vault_secrets WHERE name = %s", ("test-secret-2",))
        cur.execute(
            "SELECT count(*) FROM vault_secret_host_wraps WHERE secret_name = %s",
            ("test-secret-2",),
        )
        assert cur.fetchone()[0] == 0


def test_094_primary_key_prevents_duplicate_host_wrap(vault_db):
    with psycopg.connect(vault_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vault_secrets (name, ciphertext, wrapped_dek, kek_host, created_by_agent) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("test-secret-3", b"\x00" * 28, b"\x00" * 28, "mini", "test"),
        )
        cur.execute(
            "INSERT INTO vault_secret_host_wraps (secret_name, kek_host, wrapped_dek, created_by_agent) "
            "VALUES (%s, %s, %s, %s)",
            ("test-secret-3", "hub-vps", b"\x00" * 28, "test"),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO vault_secret_host_wraps (secret_name, kek_host, wrapped_dek, created_by_agent) "
                "VALUES (%s, %s, %s, %s)",
                ("test-secret-3", "hub-vps", b"\x01" * 28, "test"),
            )


def test_094_anon_and_authenticated_have_no_table_privilege(vault_db):
    """Same check apply_migration.py's `-- assert: no_table_privilege` lines
    run for real against the live silo post-apply (CAI-RESP-1397 #5) — proves
    it here too so a regression is caught before it ever reaches a gate."""
    with psycopg.connect(vault_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated"):
            cur.execute(
                "SELECT has_table_privilege(%s, 'public.vault_secret_host_wraps', 'SELECT')",
                (role,),
            )
            assert cur.fetchone()[0] is False, f"{role} still has SELECT on vault_secret_host_wraps"


def test_094_is_idempotent(vault_db):
    with psycopg.connect(vault_db, autocommit=True) as conn, conn.cursor() as cur:
        _apply(cur, MIG_094)  # re-apply must not raise / must not duplicate the policy
        cur.execute(
            "SELECT count(*) FROM pg_policies WHERE tablename = %s",
            ("vault_secret_host_wraps",),
        )
        assert cur.fetchone()[0] == 1
