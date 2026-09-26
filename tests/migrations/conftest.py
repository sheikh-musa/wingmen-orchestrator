import psycopg
import pytest

# The ephemeral throwaway PG17 `pg_dsn` session fixture (initdb/pg_ctl, socket-only,
# C-locale, free-port) now lives in tests/conftest.py so both the migration wet-proofs
# and the PII-containment floor proofs share one harness (backlog#68, Nazim #43375).
# This conftest keeps only the migration-specific clean-schema fixture.


@pytest.fixture
def fresh_db(pg_dsn):
    """A clean public schema per test."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        # PG15+ does not re-grant USAGE on a recreated public schema; prod's
        # public schema grants it, so restore the default or non-superuser
        # roles see "relation does not exist" instead of the table.
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        cur.execute('DROP ROLE IF EXISTS "cc-ihsanos"')
        cur.execute('DROP ROLE IF EXISTS "musa"')
    return pg_dsn
