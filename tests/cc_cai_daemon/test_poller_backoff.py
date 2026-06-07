"""Poller backoff filter — regression test for re-escalation flood.

Phase 1 cutover (2026-06-07) shipped a bug: poller filtered only
read_at IS NULL / skipped_at IS NULL, but escalation does not mark the
message read. So every 5-min cycle re-polled the same unread inbox and
re-pushed Telegram alerts. 30 unread × 7 cycles = 210 operator alerts
before the daemon was killed.

Fix: poller excludes messages with an escalation audit row in the last
ESCALATION_BACKOFF_HOURS. Operator still gets re-reminded eventually if
they never tap a button (default 24h), but no per-cycle re-push.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")


@pytestmark_integration
def test_recently_escalated_message_excluded_from_poll():
    """Insert a cai message + a fresh escalation audit row; poller must
    NOT return it. Without the fix this returns the row and the daemon
    re-pushes Telegram."""
    from cc_cai_daemon.poller import fetch_unread_for_cai

    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        try:
            cur.execute(
                """INSERT INTO agent_messages
                   (from_agent, to_agent, message_type, subject, body,
                    priority, requires_response, sub_tag, is_test)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                ("cc-orchestrator", "cai", "question",
                 "test-backoff-recent", "body",
                 "P2", True, "cc-orchestrator-test-backoff-recent", False),
            )
            msg_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO cc_cai_audit_log
                   (session_id, event_type, agent_message_id,
                    classification, escalated_to_operator)
                   VALUES (%s, %s, %s, %s, %s)""",
                ("test-backoff", "escalation", msg_id, "escalate", True),
            )

            rows = fetch_unread_for_cai(_DSN)
            row_ids = {r["id"] for r in rows}
            assert msg_id not in row_ids, (
                f"recently-escalated msg #{msg_id} must not appear in poll batch — "
                f"this is the loop-bug regression"
            )
        finally:
            cur.execute(
                "DELETE FROM cc_cai_audit_log WHERE session_id='test-backoff'"
            )
            cur.execute(
                "DELETE FROM agent_messages WHERE sub_tag LIKE "
                "'cc-orchestrator-test-backoff-%'"
            )


@pytestmark_integration
def test_old_escalation_does_not_block_poll():
    """Escalation logged >24h ago must allow re-poll (the reminder cycle)."""
    from cc_cai_daemon.poller import fetch_unread_for_cai

    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        try:
            cur.execute(
                """INSERT INTO agent_messages
                   (from_agent, to_agent, message_type, subject, body,
                    priority, requires_response, sub_tag, is_test)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                ("cc-orchestrator", "cai", "question",
                 "test-backoff-old", "body",
                 "P2", True, "cc-orchestrator-test-backoff-old", False),
            )
            msg_id = cur.fetchone()[0]
            # backdated 26h ago — past the 24h backoff window
            cur.execute(
                """INSERT INTO cc_cai_audit_log
                   (session_id, event_type, agent_message_id,
                    classification, escalated_to_operator, logged_at)
                   VALUES (%s, %s, %s, %s, %s, now() - interval '26 hours')""",
                ("test-backoff", "escalation", msg_id, "escalate", True),
            )

            rows = fetch_unread_for_cai(_DSN)
            row_ids = {r["id"] for r in rows}
            assert msg_id in row_ids, (
                f"escalation >24h old should not block poll — reminder cycle broken"
            )
        finally:
            cur.execute(
                "DELETE FROM cc_cai_audit_log WHERE session_id='test-backoff'"
            )
            cur.execute(
                "DELETE FROM agent_messages WHERE sub_tag LIKE "
                "'cc-orchestrator-test-backoff-%'"
            )
