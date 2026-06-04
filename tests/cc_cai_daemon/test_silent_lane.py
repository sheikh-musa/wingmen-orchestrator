"""Silent-lane handler tests — CAI-RESP-185 Q4 + INV-5 amanah ordering.

Per CAI-RESP-185 Q4: Phase 1 silent-lane is strictly mark_read_fyi + ack_fyi.
Per INV-5 (HARD SHIP CONDITION): the audit row MUST land BEFORE the
side-effect supabase mutation fires. If the audit write fails (returns None),
the handler MUST return False and the mutation MUST NOT execute.

These are pure-unit ordering tests — MagicMock everywhere, no DB roundtrip.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cc_cai_daemon.silent_lane import (  # noqa: E402
    handle_ack_fyi,
    handle_mark_read_fyi,
)


# ---------- helpers ----------

def _msg(**overrides) -> dict:
    base = {
        "id": 101,
        "thread_id": 7,
        "from_agent": "cc-orchestrator",
        "subject": "soak telemetry summary",
        "body": "soak run complete; no anomalies",
    }
    base.update(overrides)
    return base


def _make_supabase_with_order(call_order: list) -> MagicMock:
    """Build a supabase mock whose update/insert chains append to call_order."""
    supabase = MagicMock()

    # update chain: supabase.table(...).update(...).eq(...).execute()
    def table_factory(*args, **kwargs):
        table = MagicMock()

        def update(*a, **k):
            call_order.append("supabase_update")
            chain = MagicMock()
            chain.eq.return_value.execute.return_value = None
            return chain

        def insert(*a, **k):
            call_order.append("supabase_insert")
            chain = MagicMock()
            chain.execute.return_value = None
            return chain

        table.update.side_effect = update
        table.insert.side_effect = insert
        return table

    supabase.table.side_effect = table_factory
    return supabase


def _make_audit_with_order(call_order: list, return_value=1) -> MagicMock:
    audit = MagicMock()

    def log_silent_action(**kwargs):
        call_order.append("audit")
        return return_value

    audit.log_silent_action.side_effect = log_silent_action
    return audit


# ---------- mark_read_fyi tests ----------

def test_mark_read_audit_first_supabase_second():
    """INV-5: audit MUST be called before supabase mutation."""
    call_order: list = []
    supabase = _make_supabase_with_order(call_order)
    audit = _make_audit_with_order(call_order, return_value=42)

    result = handle_mark_read_fyi(supabase, audit, _msg(), reason="routine soak FYI")

    assert result is True
    assert call_order == ["audit", "supabase_update"], (
        f"audit MUST fire before supabase mutation; got {call_order}"
    )


def test_mark_read_audit_failure_blocks_side_effect():
    """If audit returns None, the supabase mutation must NOT fire."""
    supabase = MagicMock()
    audit = MagicMock()
    audit.log_silent_action.return_value = None

    result = handle_mark_read_fyi(supabase, audit, _msg(), reason="routine FYI")

    assert result is False
    audit.log_silent_action.assert_called_once()
    supabase.table.assert_not_called()


def test_mark_read_happy_path_returns_true():
    """Audit returns int → handler runs the update chain once and returns True."""
    supabase = MagicMock()
    audit = MagicMock()
    audit.log_silent_action.return_value = 1

    result = handle_mark_read_fyi(supabase, audit, _msg(id=555), reason="ok")

    assert result is True
    supabase.table.assert_called_once_with("agent_messages")
    update_chain = supabase.table.return_value.update
    update_chain.assert_called_once_with(
        {"read_at": "now()", "responded_at": "now()"}
    )
    update_chain.return_value.eq.assert_called_once_with("id", 555)
    update_chain.return_value.eq.return_value.execute.assert_called_once()


# ---------- ack_fyi tests ----------

def test_ack_fyi_audit_first_insert_second_update_third():
    """INV-5 + spec ordering: audit → insert ack reply → update original."""
    call_order: list = []
    supabase = _make_supabase_with_order(call_order)
    audit = _make_audit_with_order(call_order, return_value=99)

    result = handle_ack_fyi(supabase, audit, _msg(), reason="ack-class FYI")

    assert result is True
    assert call_order == ["audit", "supabase_insert", "supabase_update"], (
        f"expected audit → insert → update; got {call_order}"
    )


def test_ack_fyi_audit_failure_blocks_both_writes():
    """If audit returns None, neither the insert nor the update may fire."""
    supabase = MagicMock()
    audit = MagicMock()
    audit.log_silent_action.return_value = None

    result = handle_ack_fyi(supabase, audit, _msg(), reason="ack class")

    assert result is False
    audit.log_silent_action.assert_called_once()
    supabase.table.assert_not_called()


def test_ack_fyi_inserts_correct_reply_row():
    """Verify exact field shape of the inserted ack reply row."""
    supabase = MagicMock()
    audit = MagicMock()
    audit.log_silent_action.return_value = 1

    msg = _msg(
        id=222,
        thread_id=88,
        from_agent="cc-orchestrator",
        subject="status ping",
    )

    result = handle_ack_fyi(supabase, audit, msg, reason="ack FYI")

    assert result is True
    # supabase.table("agent_messages") called twice: once for insert, once for update
    table_calls = [c.args for c in supabase.table.call_args_list]
    assert ("agent_messages",) in table_calls

    # find the insert call
    insert_mock = supabase.table.return_value.insert
    insert_mock.assert_called_once()
    inserted = insert_mock.call_args.args[0]

    assert inserted["thread_id"] == 88
    assert inserted["from_agent"] == "cai"
    assert inserted["to_agent"] == "cc-orchestrator"
    assert inserted["message_type"] == "agreed"
    assert inserted["priority"] == "P3"
    assert inserted["subject"] == "ACK: status ping"
    assert inserted["body"] == "noted, no action needed"
    assert inserted["requires_response"] is False
    assert inserted["sub_tag"] == "cc-cai-daemon"


def test_ack_fyi_subject_truncated_to_80():
    """Long subjects are truncated to 80 chars after the 'ACK: ' prefix."""
    supabase = MagicMock()
    audit = MagicMock()
    audit.log_silent_action.return_value = 1

    long_subject = "x" * 200
    msg = _msg(subject=long_subject)

    result = handle_ack_fyi(supabase, audit, msg, reason="ack")

    assert result is True
    inserted = supabase.table.return_value.insert.call_args.args[0]
    assert inserted["subject"] == "ACK: " + ("x" * 80)
    # original subject body characters past 80 are dropped
    assert len(inserted["subject"]) == len("ACK: ") + 80


def test_ack_fyi_custom_ack_text():
    """Custom ack_text override appears in the body field."""
    supabase = MagicMock()
    audit = MagicMock()
    audit.log_silent_action.return_value = 1

    result = handle_ack_fyi(
        supabase, audit, _msg(), reason="ack", ack_text="acknowledged"
    )

    assert result is True
    inserted = supabase.table.return_value.insert.call_args.args[0]
    assert inserted["body"] == "acknowledged"
