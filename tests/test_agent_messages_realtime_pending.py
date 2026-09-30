"""Realtime doorbell passes the bus row id into wake_agent (Fable audit 2026-09-30 B-1 (iii),
op#23531 PR A): that is what turns a busy/debounced/rc=3 outcome into a PENDING-RETRY marker
(and applies the per-row delivery ceiling to the realtime path too)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from nervous_system import agent_messages_realtime as rt  # noqa: E402


def _supabase_with_row(msg):
    supabase = MagicMock()
    limit = MagicMock()
    limit.execute = AsyncMock(return_value=MagicMock(data=[msg]))
    supabase.table.return_value.select.return_value.eq.return_value.limit.return_value = limit
    return supabase


@pytest.mark.asyncio
async def test_auto_wake_passes_row_id_for_pending_retry(monkeypatch):
    msg = {"id": 4242, "from_agent": "orch-console", "to_agent": "cc-quality",
           "message_type": "update", "subject": "s", "body": "b", "requires_response": True,
           "priority": "P1", "created_at": "2026-09-30T08:00:00Z", "is_test": False,
           "forwarded_to_telegram_at": None, "skipped_at": None, "read_at": None}
    calls = []
    monkeypatch.setattr(rt.agent_wake, "auto_wake_enabled", lambda: True)
    monkeypatch.setattr(rt.agent_wake, "wake_agent",
                        lambda agent, reason="", **kw: calls.append((agent, reason, kw)) or
                        {"woke": False, "why": "busy (mid-turn)"})
    with patch.object(rt.agent_messages_poll, "_is_routable", return_value=False):
        await rt._route_single_message(_supabase_with_row(msg), None, None, 4242, wake_only=True)
    assert calls and calls[0][0] == "cc-quality"
    assert calls[0][2].get("row_id") == 4242
