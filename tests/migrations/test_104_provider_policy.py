"""Ephemeral wet-prove for migration 104 — provider_policy table (Musa op#29321/#29324).

Runs against the ephemeral PG17 harness (tests/conftest.py pg_dsn). NEVER touches
DATABASE_URL / any live silo. Exercises the SHIPPED artifact: the DDL is read FROM
migrations/104_provider_policy.sql (not re-typed), so this breaks if the file drifts.

Proves the two safety properties the table must carry regardless of the resolver:
  1. the CHECK constraint REJECTS a row that allows a non-Anthropic provider
     without text_only=true (Musa's hard condition);
  2. the op#-cited seed rows exist with the right shape (irsyad anthropic-only;
     cosem-adcda GLM text-only).
"""
from __future__ import annotations

import pathlib

import psycopg
import pytest

MIG = pathlib.Path(__file__).resolve().parent.parent.parent / "migrations" / "104_provider_policy.sql"


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


def test_migration_applies_and_seeds(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(_executable_sql(MIG))
        cur.execute("SELECT allowed_providers, text_only FROM provider_policy WHERE client_key='irsyad'")
        assert cur.fetchone() == (["anthropic"], False)
        cur.execute("SELECT allowed_providers, text_only FROM provider_policy WHERE client_key='cosem-adcda'")
        assert cur.fetchone() == (["anthropic", "zai"], True)


def test_check_rejects_zai_without_text_only(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(_executable_sql(MIG))
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO provider_policy(client_key, allowed_providers, text_only) "
                "VALUES ('bad', ARRAY['anthropic','zai'], false)"
            )
