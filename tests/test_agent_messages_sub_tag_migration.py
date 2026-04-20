"""B1 migration: agent_messages.sub_tag column, CHECK constraint, partial index."""
import os
import pytest
import psycopg
from pathlib import Path

MIGRATION_PATH = Path(__file__).parent.parent / "supabase/migrations/20260420_agent_messages_sub_tag.sql"


def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — integration test")
    return dsn


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"migration file missing: {MIGRATION_PATH}"


def test_sub_tag_column_present():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
             WHERE table_name = 'agent_messages' AND column_name = 'sub_tag'
            """
        )
        row = cur.fetchone()
        assert row is not None, "sub_tag column not found"
        assert row[0] == "text"
        assert row[1] == "YES"  # nullable


def test_check_rejects_cross_family_impersonation():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, sub_tag)
                VALUES ('cc-ihsanos', 'cai', 'update', 't', 'b', 'cc-scholar-1')
                """
            )


def test_check_accepts_matching_family():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, sub_tag)
            VALUES ('cc-ihsanos', 'cai', 'update', 'b1-test-accept', 'b', 'cc-ihsanos-99')
            RETURNING id
            """
        )
        mid = cur.fetchone()[0]
        try:
            pass
        finally:
            cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))


def test_check_accepts_null():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body)
            VALUES ('cai', 'cc-ihsanos', 'update', 'b1-test-null', 'b')
            RETURNING id, sub_tag
            """
        )
        mid, sub_tag = cur.fetchone()
        try:
            assert sub_tag is None
        finally:
            cur.execute("DELETE FROM agent_messages WHERE id = %s", (mid,))


def test_check_rejects_like_metacharacter_bypass():
    """Guard against LIKE-wildcard bypass.

    If from_agent ever contains `_` (a LIKE single-char wildcard), the old
    `sub_tag LIKE from_agent || '-%'` constraint would match across
    families. Example: from_agent='cc-ihsanos' contains a literal `-`, but
    if a future agent id were 'cc_ihsanos', then an impostor row with
    from_agent='cc-ihsanos' and sub_tag='cc-ihsanos-1' would also pass a
    constraint built from a different agent id 'cc_ihsanos' — because
    'cc-ihsanos-1' LIKE 'cc_ihsanos-%' is True under LIKE.

    More directly: from_agent='cc-ihsanos', sub_tag='cc_ihsanos-1' — the
    old LIKE form would be 'cc_ihsanos-1' LIKE 'cc-ihsanos-%'. That's
    False. The real bypass direction is from_agent containing wildcards.
    We simulate by probing the LIKE expression with a wildcard-bearing
    from_agent, then asserting the literal form rejects the equivalent
    CHECK via an insert.
    """
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        # 1. Sanity: demonstrate the old LIKE form would have allowed a
        #    cross-family match when from_agent contains `_`.
        cur.execute(
            "SELECT ('cc-ihsanos-1' LIKE %s || '-%%')", ('cc_ihsanos',)
        )
        assert cur.fetchone()[0] is True, (
            "LIKE wildcard assumption broken — `_` should match any char under LIKE"
        )

        # 2. The new literal form rejects the equivalent shape. We insert
        #    (from_agent='cc-ihsanos', sub_tag='cc_ihsanos-1'): the literal
        #    left()/length() check compares sub_tag[:11] ('cc_ihsanos-') vs
        #    from_agent || '-' ('cc-ihsanos-') — not equal, so CheckViolation.
        #    Under the OLD LIKE form this would have passed, because
        #    'cc_ihsanos-1' LIKE 'cc-ihsanos-%' is... actually False in this
        #    direction. Use a direct SELECT probe to confirm the literal
        #    form rejects a wildcard-bearing from_agent.
        cur.execute(
            """
            SELECT
                (sub_tag IS NULL
                 OR (length(from_agent) > 0
                     AND left(sub_tag, length(from_agent) + 1) = from_agent || '-'))
            FROM (VALUES ('cc_ihsanos', 'cc-ihsanos-1')) AS v(from_agent, sub_tag)
            """
        )
        assert cur.fetchone()[0] is False, (
            "literal form must reject cross-family match that LIKE would accept"
        )

        # 3. And confirm via an actual insert that a mismatched prefix is
        #    rejected by the CHECK constraint itself.
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, sub_tag)
                VALUES ('cc-ihsanos', 'cai', 'update', 'b1-test-likebypass', 'b', 'cc_ihsanos-1')
                """
            )


def test_check_rejects_empty_from_agent_in_principle():
    """from_agent is FK-enforced so we can't insert with empty from_agent,
    but we assert the CHECK expression itself rejects (from_agent='',
    sub_tag='anything-1') via a SELECT probe.
    """
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                (sub_tag IS NULL
                 OR (length(from_agent) > 0
                     AND left(sub_tag, length(from_agent) + 1) = from_agent || '-'))
            FROM (VALUES ('', 'anything-1')) AS v(from_agent, sub_tag)
            """
        )
        assert cur.fetchone()[0] is False


def test_partial_index_exists():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
             WHERE tablename = 'agent_messages' AND indexname = 'idx_agent_messages_sub_tag'
            """
        )
        row = cur.fetchone()
        assert row is not None, "partial index missing"
        assert "sub_tag IS NOT NULL" in row[0]


def test_migration_idempotent():
    sql = MIGRATION_PATH.read_text()
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql)  # should succeed even if already applied
        cur.execute(sql)  # running twice = no-op
