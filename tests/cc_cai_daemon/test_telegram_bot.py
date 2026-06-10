"""Tests for cc_cai_daemon.telegram_bot — inbound callback handler."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")

from cc_cai_daemon.telegram_bot import (
    parse_callback_data, handle_button_callback, handle_free_text_reply,
)
from cc_cai_daemon.audit import AuditLogger


class TestParseCallbackData:
    def test_approve_format(self):
        assert parse_callback_data("approve:123") == ("approve", 123)

    def test_defer_format(self):
        assert parse_callback_data("defer:456") == ("defer", 456)

    def test_delegate_format(self):
        assert parse_callback_data("delegate:789") == ("delegate", 789)

    def test_unknown_action_returns_none(self):
        assert parse_callback_data("unknown:1") is None

    def test_no_colon_returns_none(self):
        assert parse_callback_data("approve") is None

    def test_non_numeric_id_returns_none(self):
        assert parse_callback_data("approve:abc") is None

    def test_non_string_input_returns_none(self):
        assert parse_callback_data(None) is None  # type: ignore
        assert parse_callback_data(123) is None  # type: ignore


@pytest.fixture
def synthetic_msg():
    """Insert a synthetic agent_messages row for callback testing; cleanup at teardown."""
    # sub_tag CHECK constraint requires prefix `{from_agent}-`; using
    # from_agent='cc-orchestrator' lets us use a cc-orchestrator-test-{uuid} tag
    from_agent = "cc-orchestrator"
    sub_tag = f"{from_agent}-test-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages "
            "(thread_id, from_agent, to_agent, message_type, subject, body, "
            " requires_response, is_test, priority, sub_tag) "
            "VALUES (gen_random_uuid(), %s, 'cai', 'review_request', "
            "        'test escalation subject', 'test body', true, true, 'P2', %s) "
            "RETURNING id, thread_id",
            (from_agent, sub_tag),
        )
        msg_id, thread_id = cur.fetchone()
    yield msg_id
    # Delete everything on the synthetic thread (source + any button responses
    # the handler posted back), so test traffic never lingers in the real inbox.
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM cc_cai_audit_log WHERE agent_message_id IN "
            "  (SELECT id FROM agent_messages WHERE thread_id = %s)",
            (thread_id,),
        )
        cur.execute("DELETE FROM agent_messages WHERE thread_id = %s", (thread_id,))


def _audit():
    return AuditLogger(dsn=_DSN, session_id=f"test-{uuid.uuid4().hex[:8]}")


def test_button_approve_writes_response_and_marks_read(synthetic_msg):
    audit = _audit()
    ok = handle_button_callback(_DSN, audit, "approve", synthetic_msg)
    assert ok is True
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT responded_at IS NOT NULL FROM agent_messages WHERE id = %s",
            (synthetic_msg,),
        )
        assert cur.fetchone()[0] is True


def test_button_defer_does_not_mark_responded(synthetic_msg):
    audit = _audit()
    ok = handle_button_callback(_DSN, audit, "defer", synthetic_msg)
    assert ok is True
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT responded_at FROM agent_messages WHERE id = %s",
            (synthetic_msg,),
        )
        assert cur.fetchone()[0] is None


def test_button_delegate_marks_read_but_not_responded(synthetic_msg):
    audit = _audit()
    ok = handle_button_callback(_DSN, audit, "delegate", synthetic_msg)
    assert ok is True
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT read_at IS NOT NULL, responded_at IS NOT NULL "
            "  FROM agent_messages WHERE id = %s",
            (synthetic_msg,),
        )
        read_set, responded_set = cur.fetchone()
        assert read_set is True
        assert responded_set is False


def test_button_response_inherits_is_test_from_source(synthetic_msg):
    # synthetic_msg is is_test=true; the response the handler posts back to the
    # thread must also be is_test=true so test traffic never pollutes the real
    # inbox / SLA views / boot_briefing.
    audit = _audit()
    ok = handle_button_callback(_DSN, audit, "approve", synthetic_msg)
    assert ok is True
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT is_test FROM agent_messages "
            "  WHERE thread_id = (SELECT thread_id FROM agent_messages WHERE id = %s) "
            "    AND from_agent = 'musa' AND sub_tag = 'musa-button'",
            (synthetic_msg,),
        )
        assert cur.fetchone()[0] is True


def test_button_unknown_msg_id_returns_false():
    audit = _audit()
    ok = handle_button_callback(_DSN, audit, "approve", 9999999)
    assert ok is False
