import os
import pytest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from conversation import ConversationState, start_conversation, update_conversation, clear_conversation, get_conversation, _cache


def _mock_supabase_chain(final_data):
    """Build a MagicMock that mimics supabase chained query builder."""
    mock = MagicMock()
    mock.table.return_value = mock
    mock.select.return_value = mock
    mock.eq.return_value = mock
    mock.maybeSingle.return_value = mock
    mock.insert.return_value = mock
    mock.update.return_value = mock
    mock.delete.return_value = mock
    mock.execute = AsyncMock(return_value=MagicMock(data=final_data))
    return mock


class TestConversationState:
    @pytest.mark.asyncio
    async def test_start_creates_state(self):
        _cache.clear()

        mock = MagicMock()
        mock.table.return_value = mock
        mock.select.return_value = mock
        mock.eq.return_value = mock
        mock.maybeSingle.return_value = mock
        mock.insert.return_value = mock
        mock.update.return_value = mock
        mock.delete.return_value = mock

        call_count = 0
        async def side_effect_execute():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: delete (clear existing)
                return MagicMock(data=[])
            else:
                # Second call: insert
                return MagicMock(data=[{"id": 1}])

        mock.execute = AsyncMock(side_effect=side_effect_execute)

        state = await start_conversation(mock, 10, "123", "ordering", "show_menu", {"test": True})
        assert state.flow == "ordering"
        assert state.step == "show_menu"
        assert state.data["test"] is True

    @pytest.mark.asyncio
    async def test_update_changes_step(self):
        _cache.clear()
        state = ConversationState(client_id=10, telegram_chat_id="123", flow="ordering", step="show_menu", db_id=1)
        supabase = _mock_supabase_chain([])

        updated = await update_conversation(supabase, state, "collect_items", {"items": [1, 2]})
        assert updated.step == "collect_items"
        assert updated.data["items"] == [1, 2]

    @pytest.mark.asyncio
    async def test_clear_removes_from_cache(self):
        _cache[(10, "123")] = ConversationState(client_id=10, telegram_chat_id="123", flow="test", step="a")
        supabase = _mock_supabase_chain([])

        await clear_conversation(supabase, 10, "123")
        assert (10, "123") not in _cache
