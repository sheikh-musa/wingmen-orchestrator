"""Live-DB integration tests for the is_test=true announce skip
(20260507_skip_test_row_announces.sql)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


@pytestmark_integration
def test_is_test_true_skips_agent_messages_insert():
    """Inserting strategic_decisions with is_test=true MUST NOT spawn
    an agent_messages row via the auto-announce trigger."""
    test_ref = f"TEST-SKIP-IS-TEST-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain,
                       source, challenge_status, challengeable_until,
                       announce_to_agent, is_test)
                    VALUES (%s, 'test', 'test', 'test', 'architecture',
                            'claude_ai_session', 'challenge_window',
                            now() + interval '24 hours',
                            'cc-orchestrator', true)
                    RETURNING announced_by_msg_id
                """, (test_ref,))
                spawned = cur.fetchone()[0]
                assert spawned is None, \
                    f"is_test=true row spawned msg #{spawned} — guard broken"
            finally:
                c.rollback()


@pytestmark_integration
def test_is_test_true_skips_tier3_misroute_audit():
    """is_test=true rows must skip the bridge_tier3_misroute audit insert too —
    the audit is part of the same announce path."""
    test_ref = f"TEST-SKIP-AUDIT-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                # Pre-count
                cur.execute(
                    "SELECT count(*) FROM notification_log "
                    "WHERE source='bridge_tier3_misroute' AND decision_ref=%s",
                    (test_ref,)
                )
                pre = cur.fetchone()[0]
                # Insert is_test=true WITHOUT announce_to_agent (would normally
                # trigger the Tier-3 audit firing)
                cur.execute("""
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain,
                       source, challenge_status, challengeable_until, is_test)
                    VALUES (%s, 'test', 'test', 'test', 'architecture',
                            'claude_ai_session', 'challenge_window',
                            now() + interval '24 hours', true)
                """, (test_ref,))
                cur.execute(
                    "SELECT count(*) FROM notification_log "
                    "WHERE source='bridge_tier3_misroute' AND decision_ref=%s",
                    (test_ref,)
                )
                post = cur.fetchone()[0]
                assert post == pre, \
                    f"is_test=true row generated {post - pre} bridge_tier3_misroute entries"
            finally:
                c.rollback()


@pytestmark_integration
def test_is_test_false_still_announces():
    """Backwards-compat: is_test=false (the default) MUST continue to spawn
    an agent_messages row as before."""
    test_ref = f"TEST-IS-TEST-FALSE-OK-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain,
                       source, challenge_status, challengeable_until,
                       announce_to_agent, is_test)
                    VALUES (%s, 'test', 'test', 'test', 'architecture',
                            'claude_ai_session', 'challenge_window',
                            now() + interval '24 hours',
                            'cc-orchestrator', true)
                    RETURNING announced_by_msg_id
                """, (test_ref,))
                # ^ The above sets is_test=true to verify skip; for false case:
                cur.execute("ROLLBACK")
                cur.execute("BEGIN")
                cur.execute("""
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain,
                       source, challenge_status, challengeable_until,
                       announce_to_agent, is_test)
                    VALUES (%s, 'test', 'test', 'test', 'architecture',
                            'claude_ai_session', 'challenge_window',
                            now() + interval '24 hours',
                            'cc-orchestrator', false)
                    RETURNING announced_by_msg_id
                """, (test_ref + "-real",))
                spawned = cur.fetchone()[0]
                assert spawned is not None, \
                    "is_test=false row failed to spawn agent_messages — backwards compat broken"
            finally:
                c.rollback()
