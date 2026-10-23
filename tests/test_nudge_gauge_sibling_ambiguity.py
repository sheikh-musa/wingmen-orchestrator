"""Regression test for the gauge-attribution bug found right after PR#439
(cc-cosem-platform-3's #71710/#71711): cc_session_costs.cc_identity is written as the bare
base for every sibling, so a multi-sibling base's 'freshest gauge row' cannot be attributed
to any one specific instance. _gauge_for_lane must suppress the gauge (fall through to the
session-specific pane check) whenever more than one live session shares a base."""
import psycopg
import pytest

from scripts import lane_proactive_recycle_nudge as nudge
from scripts import sre_lane_recycle as slr


@pytest.fixture
def gauge_db(pg_dsn):
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""
            CREATE TABLE agent_status (
                agent_id        text PRIMARY KEY,
                base_agent_id   text,
                tmux_session    text,
                last_heartbeat  timestamptz
            )
        """)
        cur.execute("""
            CREATE TABLE cc_session_costs (
                id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                cc_identity          text,
                latest_context_tokens bigint,
                created_at           timestamptz,
                ended_at             timestamptz
            )
        """)
    return pg_dsn


def test_gauge_suppressed_when_multiple_live_siblings_share_a_base(gauge_db):
    with psycopg.connect(gauge_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, last_heartbeat) "
            "VALUES (%s, %s, %s, now()), (%s, %s, %s, now())",
            ["cc-cosem-platform-3", "cc-cosem-platform", "cosem-platform-author",
             "cc-cosem-platform-4", "cc-cosem-platform", "cosem-platform-adhoc"])
        # the ONLY row that exists is under the bare base -- matches production reality
        cur.execute(
            "INSERT INTO cc_session_costs (cc_identity, latest_context_tokens, created_at) "
            "VALUES (%s, %s, now())",
            ["cc-cosem-platform", 900_000])
        conn.commit()

    with psycopg.connect(gauge_db) as conn, conn.cursor() as cur:
        tokens, age_s = nudge._gauge_for_lane(conn, cur, "cc-cosem-platform")

    assert (tokens, age_s) == (None, None), (
        "gauge must be suppressed (unattributable) when >1 live sibling shares this base, "
        "not silently borrowed from whichever sibling last wrote a cost row")


def test_gauge_used_normally_for_a_single_live_instance_base(gauge_db):
    with psycopg.connect(gauge_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, last_heartbeat) "
            "VALUES (%s, %s, %s, now())",
            ["cc-finance-1", "cc-finance", "finance"])
        cur.execute(
            "INSERT INTO cc_session_costs (cc_identity, latest_context_tokens, created_at) "
            "VALUES (%s, %s, now())",
            ["cc-finance", 500_000])
        conn.commit()

    with psycopg.connect(gauge_db) as conn, conn.cursor() as cur:
        tokens, age_s = nudge._gauge_for_lane(conn, cur, "cc-finance")

    assert tokens == 500_000
    assert age_s is not None
