"""Conversation Cleanup — purges expired bot conversations.

Runs periodically from the orchestrator scheduler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("wingmen.conversation_cleanup")


async def cleanup_expired_conversations(supabase) -> int:
    """Delete expired bot_conversations. Returns count deleted."""
    now = datetime.now(timezone.utc).isoformat()

    result = await supabase.table("bot_conversations") \
        .delete() \
        .lt("expires_at", now) \
        .execute()

    count = len(result.data or [])
    if count:
        logger.info(f"Cleaned up {count} expired conversations")
    return count
