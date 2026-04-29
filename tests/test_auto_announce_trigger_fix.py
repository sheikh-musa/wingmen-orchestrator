"""Tests for AUTO-ANNOUNCE-TRIGGER-FIX-001.

Live-DB integration tests verifying the announce + autoclose triggers honor
Section A semantics post-fix.
"""
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
def test_announce_requires_response_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name='strategic_decisions' "
                "AND column_name='announce_requires_response'"
            )
            r = cur.fetchone()
    assert r is not None, "announce_requires_response column missing"
    assert r[0] == "boolean"
    assert r[1] == "NO", "should be NOT NULL"
    assert r[2] == "false", f"default should be false, got {r[2]!r}"


@pytestmark_integration
def test_announce_default_false_on_challenge_window_decision():
    """Inserting a strategic_decision with challenge_status='challenge_window' and
    announce_requires_response default → spawned agent_messages.requires_response
    must be false (Section A: challenge_window mechanism IS the response gate)."""
    test_ref = f"TEST-AUTOANNCE-FIX-{uuid.uuid4().hex[:8]}"
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
                msg_id = cur.fetchone()[0]
                assert msg_id is not None, "announce trigger didn't fire"
                cur.execute(
                    "SELECT requires_response, message_type FROM agent_messages WHERE id=%s",
                    (msg_id,)
                )
                rr, mt = cur.fetchone()
                assert rr is False, f"requires_response should default false, got {rr}"
                assert mt == "review_request", f"message_type wrong: {mt}"
            finally:
                c.rollback()


@pytestmark_integration
def test_announce_explicit_true_honored():
    """When announce_requires_response=true is explicitly set,
    spawned message gets requires_response=true."""
    test_ref = f"TEST-AUTOANNCE-EXPLICIT-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain,
                       source, challenge_status, challengeable_until,
                       announce_to_agent, announce_requires_response, is_test)
                    VALUES (%s, 'test', 'test', 'test', 'architecture',
                            'claude_ai_session', 'challenge_window',
                            now() + interval '24 hours',
                            'cc-orchestrator', true, true)
                    RETURNING announced_by_msg_id
                """, (test_ref,))
                msg_id = cur.fetchone()[0]
                cur.execute(
                    "SELECT requires_response FROM agent_messages WHERE id=%s",
                    (msg_id,)
                )
                rr = cur.fetchone()[0]
                assert rr is True, f"explicit announce_requires_response=true ignored: got {rr}"
            finally:
                c.rollback()


@pytestmark_integration
def test_announce_accepted_status_still_decision_type():
    """challenge_status='accepted' announces still fire as message_type='decision',
    requires_response=false (existing behavior preserved)."""
    test_ref = f"TEST-AUTOANNCE-ACCEPTED-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain,
                       source, challenge_status, announce_to_agent, is_test)
                    VALUES (%s, 'test', 'test', 'test', 'architecture',
                            'claude_ai_session', 'accepted',
                            'cc-orchestrator', true)
                    RETURNING announced_by_msg_id
                """, (test_ref,))
                msg_id = cur.fetchone()[0]
                cur.execute(
                    "SELECT requires_response, message_type FROM agent_messages WHERE id=%s",
                    (msg_id,)
                )
                rr, mt = cur.fetchone()
                assert rr is False
                assert mt == "decision"
            finally:
                c.rollback()


@pytestmark_integration
def test_autoclose_skips_when_original_requires_response_false():
    """autoclose trigger MUST NOT set responded_at when original announce had
    requires_response=false. Section A: read_at closes for non-dialogue."""
    test_ref = f"TEST-AUTOCLOSE-SKIP-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                # Insert decision in challenge_window state — fires announce w/ rr=false
                cur.execute("""
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain,
                       source, challenge_status, challengeable_until,
                       announce_to_agent, is_test)
                    VALUES (%s, 'test', 'test', 'test', 'architecture',
                            'claude_ai_session', 'challenge_window',
                            now() + interval '24 hours',
                            'cc-orchestrator', true)
                    RETURNING id, announced_by_msg_id
                """, (test_ref,))
                dec_id, msg_id = cur.fetchone()
                # Confirm pre-flip: responded_at IS NULL
                cur.execute("SELECT responded_at FROM agent_messages WHERE id=%s", (msg_id,))
                assert cur.fetchone()[0] is None
                # Flip execution_status to 'implemented' — fires autoclose
                cur.execute("""
                    UPDATE strategic_decisions
                       SET execution_status='implemented'
                     WHERE id=%s
                """, (dec_id,))
                # Confirm post-flip: responded_at STILL NULL because rr was false
                cur.execute("SELECT responded_at FROM agent_messages WHERE id=%s", (msg_id,))
                rspd = cur.fetchone()[0]
                assert rspd is None, \
                    f"autoclose set responded_at on rr=false message: {rspd} — Section A violation"
            finally:
                c.rollback()


@pytestmark_integration
def test_autoclose_fires_when_original_requires_response_true():
    """Inverse: when original announce explicitly had requires_response=true,
    autoclose DOES set responded_at on implementation."""
    test_ref = f"TEST-AUTOCLOSE-FIRE-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO strategic_decisions
                      (decision_ref, title, decision, reasoning, domain,
                       source, challenge_status, challengeable_until,
                       announce_to_agent, announce_requires_response, is_test)
                    VALUES (%s, 'test', 'test', 'test', 'architecture',
                            'claude_ai_session', 'challenge_window',
                            now() + interval '24 hours',
                            'cc-orchestrator', true, true)
                    RETURNING id, announced_by_msg_id
                """, (test_ref,))
                dec_id, msg_id = cur.fetchone()
                cur.execute("""
                    UPDATE strategic_decisions
                       SET execution_status='implemented'
                     WHERE id=%s
                """, (dec_id,))
                cur.execute("SELECT responded_at FROM agent_messages WHERE id=%s", (msg_id,))
                rspd = cur.fetchone()[0]
                assert rspd is not None, \
                    "autoclose didn't set responded_at on rr=true message — should have"
            finally:
                c.rollback()
