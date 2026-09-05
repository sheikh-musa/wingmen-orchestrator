"""CADENCE-008 A: poll the cc-ihsanos agent inbox (both unread classes)."""
from __future__ import annotations

TO_AGENT = "cc-ihsanos"


def inbox_query(limit: int = 50) -> tuple[str, tuple]:
    sql = (
        "SELECT id, thread_id, from_agent, to_agent, message_type, subject, "
        "body, requires_response, priority, created_at, sub_tag "
        "FROM agent_messages "
        "WHERE to_agent = %s AND read_at IS NULL AND is_test = false "
        "AND skipped_at IS NULL "
        "ORDER BY priority ASC, created_at ASC LIMIT %s"
    )
    return sql, (TO_AGENT, limit)
