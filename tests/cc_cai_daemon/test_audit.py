"""Audit logger tests. Per CAI-RESP-185, every classification + tool call
+ escalation MUST be logged before any side effect."""
from __future__ import annotations
import os, sys, uuid
from pathlib import Path
from unittest.mock import MagicMock

import psycopg, pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")

from cc_cai_daemon.audit import AuditLogger


@pytest.fixture
def audit():
    sess = f"test-{uuid.uuid4().hex[:8]}"
    yield AuditLogger(dsn=_DSN, session_id=sess)
    # cleanup
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute("DELETE FROM cc_cai_audit_log WHERE session_id = %s", (sess,))


def test_log_classification_writes_row(audit):
    audit.log_classification(
        agent_message_id=42, classification="mark_read_fyi",
        reason="P3 update with requires_response=false",
        confidence=0.95,
    )
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT classification, confidence FROM cc_cai_audit_log "
            "WHERE session_id=%s ORDER BY id DESC LIMIT 1", (audit.session_id,))
        r = cur.fetchone()
    assert r[0] == "mark_read_fyi"
    assert float(r[1]) == 0.95


def test_log_tool_call_captures_tool_name(audit):
    audit.log_tool_call(
        tool_name="supabase_update_read_at",
        tool_input={"agent_message_id": 7},
        tool_output={"ok": True},
    )
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT event_type, tool_name FROM cc_cai_audit_log "
            "WHERE session_id=%s ORDER BY id DESC LIMIT 1", (audit.session_id,))
        r = cur.fetchone()
    assert r[0] == "tool_call"
    assert r[1] == "supabase_update_read_at"


def test_log_escalation_marks_flag_and_telegram_id(audit):
    audit.log_escalation(
        agent_message_id=99, reason="riba/finance trigger",
        telegram_message_id=12345,
    )
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT escalated_to_operator, telegram_message_id, event_type "
            "FROM cc_cai_audit_log WHERE session_id=%s ORDER BY id DESC LIMIT 1",
            (audit.session_id,))
        r = cur.fetchone()
    assert r[0] is True
    assert r[1] == 12345
    assert r[2] == "escalation"


def test_log_kill_switch_trip(audit):
    audit.log_kill_switch_trip(
        new_state="pure_escalation_mode",
        reason="confidence_drop_3_consecutive_under_0.5",
    )
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT event_type, kill_switch_state FROM cc_cai_audit_log "
            "WHERE session_id=%s ORDER BY id DESC LIMIT 1", (audit.session_id,))
        r = cur.fetchone()
    assert r[0] == "kill_switch_trip"
    assert r[1] == "pure_escalation_mode"
