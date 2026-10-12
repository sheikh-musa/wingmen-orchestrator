"""Ephemeral wet-prove for migration 104 — provider_policy table (Musa op#29321/#29324).

Runs against the ephemeral PG17 harness (tests/conftest.py pg_dsn). NEVER touches
DATABASE_URL / any live silo. Exercises the SHIPPED artifact: the DDL is read FROM
migrations/104_provider_policy.sql (not re-typed), so this breaks if the file drifts.

Proves the safety properties the table must carry regardless of the resolver:
  1. the CHECK rejects a row that allows a non-Anthropic provider without text_only;
  2. the op#-cited seed rows exist with the right shape;
  3. RLS/REVOKE lock anon/authenticated out of this CONTROL table (orch-console
     #63805) — the test MIMICS Supabase's pg_default_acl grant to anon/authenticated
     first, so the migration's REVOKE is a true red->green (without it, anon keeps
     the default grant and could flip irsyad onto z.ai).
"""
from __future__ import annotations

import pathlib

import psycopg
import pytest

MIG = pathlib.Path(__file__).resolve().parent.parent.parent / "migrations" / "104_provider_policy.sql"


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


@pytest.fixture
def policy_db(fresh_db):
    """Clean schema + the Supabase-shaped roles + a mimicked pg_default_acl grant
    to anon/authenticated (so the migration's REVOKE has something to revoke),
    then the migration applied as its owner."""
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role", "console_readonly"):
            cur.execute(f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        # Mimic Supabase: new public tables are granted to anon/authenticated by default.
        cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated")
        cur.execute(_executable_sql(MIG))
    return fresh_db


def test_seed_rows_match_op_clearances(policy_db):
    with psycopg.connect(policy_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT allowed_providers, text_only FROM provider_policy WHERE client_key='irsyad'")
        assert cur.fetchone() == (["anthropic"], False)
        cur.execute("SELECT allowed_providers, text_only FROM provider_policy WHERE client_key='cosem-adcda'")
        assert cur.fetchone() == (["anthropic", "zai"], True)


def test_check_rejects_zai_without_text_only(policy_db):
    with psycopg.connect(policy_db, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO provider_policy(client_key, allowed_providers, text_only) "
                "VALUES ('bad', ARRAY['anthropic','zai'], false)"
            )


def test_anon_and_authenticated_locked_out(policy_db):
    # The REVOKE must strip the mimicked default grant — else the anon key could
    # rewrite irsyad's policy onto z.ai (orch-console #63805).
    with psycopg.connect(policy_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute("SELECT has_table_privilege(%s, 'public.provider_policy', %s)", (role, priv))
                assert cur.fetchone()[0] is False, f"{role} still has {priv} on provider_policy"


def test_console_readonly_reads_but_cannot_write(policy_db):
    with psycopg.connect(policy_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('console_readonly', 'public.provider_policy', 'SELECT')")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT has_table_privilege('console_readonly', 'public.provider_policy', 'UPDATE')")
        assert cur.fetchone()[0] is False


def test_service_role_has_full_access(policy_db):
    with psycopg.connect(policy_db, autocommit=True) as conn, conn.cursor() as cur:
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            cur.execute("SELECT has_table_privilege('service_role', 'public.provider_policy', %s)", (priv,))
            assert cur.fetchone()[0] is True, f"service_role missing {priv}"
