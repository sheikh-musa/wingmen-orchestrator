"""Ephemeral wet-prove for migration 116 — provider_policy_lane_override table
(orch-console #74584/#74588, bus #74586 spec).

Runs against the ephemeral PG17 harness (tests/conftest.py pg_dsn). NEVER touches
DATABASE_URL / any live silo. Exercises the SHIPPED artifact: the DDL is read FROM
migrations/116_provider_policy_lane_override.sql (not re-typed), so this breaks if
the file drifts.

Proves the safety properties the table must carry:
  1. the CHECK rejects a row that allows a non-Anthropic provider without text_only
     (same invariant shape as provider_policy, 104);
  2. the migrated HARD_EXCLUDE_LANES seed rows (cosem-adcda-photos, cc-fleet-health)
     exist with the right shape;
  3. RLS/REVOKE lock anon/authenticated out of this CONTROL table, same as 104.

The "an override may only narrow, never widen" invariant is NOT a DB CHECK (it
depends on joining against provider_policy's current row) — that is proven in
tests/test_provider_policy.py against assert_glm_allowed, not here.
"""
from __future__ import annotations

import pathlib

import psycopg
import pytest

MIG = pathlib.Path(__file__).resolve().parent.parent.parent / "migrations" / "116_provider_policy_lane_override.sql"


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


@pytest.fixture
def override_db(fresh_db):
    """Clean schema + the Supabase-shaped roles + a mimicked pg_default_acl grant
    to anon/authenticated (so the migration's REVOKE has something to revoke),
    then the migration applied as its owner."""
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role", "console_readonly"):
            cur.execute(f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated")
        cur.execute(_executable_sql(MIG))
    return fresh_db


def test_seed_rows_match_hard_exclude_lanes(override_db):
    with psycopg.connect(override_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT allowed_providers, text_only FROM provider_policy_lane_override "
                    "WHERE lane='cosem-adcda-photos'")
        assert cur.fetchone() == (["anthropic"], False)
        cur.execute("SELECT allowed_providers, text_only FROM provider_policy_lane_override "
                    "WHERE lane='cc-fleet-health'")
        assert cur.fetchone() == (["anthropic"], False)


def test_check_rejects_zai_without_text_only(override_db):
    with psycopg.connect(override_db, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO provider_policy_lane_override(lane, allowed_providers, text_only) "
                "VALUES ('bad-lane', ARRAY['anthropic','zai'], false)"
            )


def test_anon_and_authenticated_locked_out(override_db):
    with psycopg.connect(override_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute("SELECT has_table_privilege(%s, 'public.provider_policy_lane_override', %s)",
                            (role, priv))
                assert cur.fetchone()[0] is False, f"{role} still has {priv} on provider_policy_lane_override"


def test_console_readonly_reads_but_cannot_write(override_db):
    with psycopg.connect(override_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('console_readonly', 'public.provider_policy_lane_override', 'SELECT')")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT has_table_privilege('console_readonly', 'public.provider_policy_lane_override', 'UPDATE')")
        assert cur.fetchone()[0] is False


def test_service_role_has_full_access(override_db):
    with psycopg.connect(override_db, autocommit=True) as conn, conn.cursor() as cur:
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            cur.execute("SELECT has_table_privilege('service_role', 'public.provider_policy_lane_override', %s)",
                        (priv,))
            assert cur.fetchone()[0] is True, f"service_role missing {priv}"


def test_migration_is_rerun_safe(override_db):
    # ON CONFLICT DO NOTHING + IF NOT EXISTS / DROP POLICY IF EXISTS — applying
    # twice must not error and must not duplicate the seed rows.
    with psycopg.connect(override_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(_executable_sql(MIG))
        cur.execute("SELECT count(*) FROM provider_policy_lane_override")
        assert cur.fetchone()[0] == 2
