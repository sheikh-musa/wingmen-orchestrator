"""Silent-lane handlers — mark_read + ack_fyi only.

Per CAI-RESP-185 Q4: Phase 1 silent-lane authority is strictly the narrow
pair of `mark_read_fyi` and `ack_fyi`. Do NOT widen this lane until ≥1 week
operational evidence AND INV-5 audit review showing zero misclassifications.

Per CAI-RESP-185 INV-5 (HARD SHIP CONDITION / amanah ordering): every handler
in this module MUST write the audit row BEFORE the supabase mutation fires.
If the audit write fails (returns None), the handler MUST return False and the
side effect MUST NOT execute. The audit log IS the ground-truth record of
what the daemon did — the mutation is only valid if the audit row landed first.
"""
from __future__ import annotations

from cc_cai_daemon.audit import AuditLogger


def handle_mark_read_fyi(
    supabase, audit: AuditLogger, msg: dict, reason: str
) -> bool:
    """Mark a routine FYI as read+responded — cai's silent-noted reply.

    Returns True if both audit + supabase update succeeded; False if the
    audit write failed (no side effect fires in that case).
    """
    # 1. Audit FIRST per INV-5
    audit_id = audit.log_silent_action(
        agent_message_id=msg["id"],
        action="supabase_update_read_at",
        classification="mark_read_fyi",
        reason=reason,
    )
    if audit_id is None:
        return False  # audit failed — do not proceed
    # 2. Side effect
    supabase.table("agent_messages").update(
        {"read_at": "now()", "responded_at": "now()"}
    ).eq("id", msg["id"]).execute()
    return True


def handle_ack_fyi(
    supabase,
    audit: AuditLogger,
    msg: dict,
    reason: str,
    ack_text: str = "noted, no action needed",
) -> bool:
    """Post a canned ack reply back into the thread, then mark original read.

    Returns True if both audit + both supabase writes succeeded; False if the
    audit write failed (neither insert nor update fires in that case).
    """
    # 1. Audit FIRST per INV-5
    audit_id = audit.log_silent_action(
        agent_message_id=msg["id"],
        action="supabase_insert_ack_reply",
        classification="ack_fyi",
        reason=reason,
    )
    if audit_id is None:
        return False  # audit failed — do not proceed
    # 2. Insert ack reply row
    supabase.table("agent_messages").insert(
        {
            "thread_id": msg["thread_id"],
            "from_agent": "cai",
            "to_agent": msg["from_agent"],
            "message_type": "agreed",
            "priority": "P3",
            "subject": f"ACK: {msg['subject'][:80]}",
            "body": ack_text,
            "requires_response": False,
            "sub_tag": "cc-cai-daemon",
        }
    ).execute()
    # 3. Mark original as read + responded
    supabase.table("agent_messages").update(
        {"read_at": "now()", "responded_at": "now()"}
    ).eq("id", msg["id"]).execute()
    return True
