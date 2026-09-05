import os
import pytest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from legacy.bot_manager import BotManager, compute_token_hash, ClientBot

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def _mock_supabase_chain(final_data):
    """Build a MagicMock that mimics supabase chained query builder.

    supabase.table(...).select(...).eq(...) etc. are all sync (return self),
    only .execute() is async.
    """
    mock = MagicMock()
    # Make every chained method return the same mock (builder pattern)
    mock.table.return_value = mock
    mock.select.return_value = mock
    mock.eq.return_value = mock
    mock.is_.return_value = mock
    mock.in_.return_value = mock
    mock.maybeSingle.return_value = mock
    mock.insert.return_value = mock
    mock.update.return_value = mock
    mock.gte.return_value = mock
    # not_ is accessed as an attribute (not called), so set it directly
    mock.not_ = mock
    # Only execute is async
    mock.execute = AsyncMock(return_value=MagicMock(data=final_data))
    return mock


class TestTokenHash:
    def test_deterministic(self):
        assert compute_token_hash("abc") == compute_token_hash("abc")

    def test_different_tokens_different_hashes(self):
        assert compute_token_hash("token1") != compute_token_hash("token2")

    def test_length_16(self):
        assert len(compute_token_hash("any-token")) == 16


class TestBotManager:
    def test_get_by_hash_returns_none_when_empty(self):
        mgr = BotManager()
        assert mgr.get_by_hash("nonexistent") is None

    @pytest.mark.asyncio
    async def test_load_all_populates_maps(self):
        mgr = BotManager()
        supabase = _mock_supabase_chain([{
            "id": 1, "telegram_bot_token": "test-token", "name": "Test",
            "bot_username": "test_bot", "bot_display_name": "Test Bot",
            "personality": None, "welcome_message": None,
            "capabilities": ["support"], "repo_name": "test-repo",
            "platform": "custom", "ihsanos_org_id": None,
        }])
        count = await mgr.load_all(supabase)
        assert count == 1
        assert mgr.get_by_client_id(1) is not None

    @pytest.mark.asyncio
    async def test_load_all_with_empty_result(self):
        mgr = BotManager()
        supabase = _mock_supabase_chain([])
        count = await mgr.load_all(supabase)
        assert count == 0
        assert len(mgr.bots) == 0

    def test_get_by_client_id_returns_none_when_empty(self):
        mgr = BotManager()
        assert mgr.get_by_client_id(999) is None
