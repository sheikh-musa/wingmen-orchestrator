import os
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from legacy.bot_user_resolver import resolve_user, try_claim_invite, clear_cache, BotUser

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def _mock_supabase_chain(final_data):
    """Build a MagicMock that mimics supabase chained query builder."""
    mock = MagicMock()
    mock.table.return_value = mock
    mock.select.return_value = mock
    mock.eq.return_value = mock
    mock.not_.return_value = mock
    mock.is_.return_value = mock
    mock.in_.return_value = mock
    mock.maybeSingle.return_value = mock
    mock.insert.return_value = mock
    mock.update.return_value = mock
    mock.gte.return_value = mock
    mock.execute = AsyncMock(return_value=MagicMock(data=final_data))
    return mock


class TestResolveUser:
    @pytest.mark.asyncio
    async def test_returns_existing_user(self):
        clear_cache()
        supabase = _mock_supabase_chain({
            "id": 1, "client_id": 10, "telegram_chat_id": "123",
            "name": "Ahmad", "role": "owner", "permissions": [],
            "status": "active", "is_active": True,
        })
        user = await resolve_user(supabase, 10, "123")
        assert user.role == "owner"
        assert user.name == "Ahmad"

    @pytest.mark.asyncio
    async def test_auto_registers_customer(self):
        clear_cache()

        # We need execute to return None first (no existing user),
        # then return insert data on second call.
        mock = MagicMock()
        mock.table.return_value = mock
        mock.select.return_value = mock
        mock.eq.return_value = mock
        mock.maybeSingle.return_value = mock
        mock.insert.return_value = mock

        call_count = 0
        async def side_effect_execute():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: lookup returns no data
                return MagicMock(data=None)
            else:
                # Second call: insert returns new user
                return MagicMock(data=[{
                    "id": 2, "client_id": 10, "telegram_chat_id": "456",
                    "name": "Customer", "role": "customer", "permissions": [],
                    "status": "active", "is_active": True,
                }])

        mock.execute = AsyncMock(side_effect=side_effect_execute)

        user = await resolve_user(mock, 10, "456")
        assert user.role == "customer"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_returns_same_user(self):
        clear_cache()
        supabase = _mock_supabase_chain({
            "id": 1, "client_id": 10, "telegram_chat_id": "123",
            "name": "Ahmad", "role": "owner", "permissions": [],
            "status": "active", "is_active": True,
        })
        user1 = await resolve_user(supabase, 10, "123")
        # Second call should use cache (execute won't be called again)
        supabase.execute.reset_mock()
        user2 = await resolve_user(supabase, 10, "123")
        assert user1 is user2
        supabase.execute.assert_not_awaited()


class TestClaimInvite:
    @pytest.mark.asyncio
    async def test_expired_code_returns_none(self):
        clear_cache()
        expired = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        supabase = _mock_supabase_chain({
            "id": 1, "client_id": 10, "role": "manager",
            "invite_code": "ABC123", "invite_expires_at": expired,
            "permissions": [],
        })
        result = await try_claim_invite(supabase, 10, "ABC123", "789", None, "Fatimah")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_matching_code_returns_none(self):
        clear_cache()
        supabase = _mock_supabase_chain(None)
        result = await try_claim_invite(supabase, 10, "INVALID", "789", None, "Fatimah")
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_invite_returns_user(self):
        clear_cache()
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        mock = MagicMock()
        mock.table.return_value = mock
        mock.select.return_value = mock
        mock.eq.return_value = mock
        mock.maybeSingle.return_value = mock
        mock.update.return_value = mock

        call_count = 0
        async def side_effect_execute():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: find invite
                return MagicMock(data={
                    "id": 5, "client_id": 10, "role": "staff",
                    "invite_code": "VALID1", "invite_expires_at": future,
                    "permissions": ["view_orders"],
                })
            else:
                # Second call: update
                return MagicMock(data=[{
                    "id": 5, "client_id": 10, "telegram_chat_id": "789",
                    "name": "Fatimah", "role": "staff", "permissions": ["view_orders"],
                    "status": "active", "is_active": True,
                }])

        mock.execute = AsyncMock(side_effect=side_effect_execute)

        result = await try_claim_invite(mock, 10, "VALID1", "789", "fatimah_tg", "Fatimah")
        assert result is not None
        assert result.role == "staff"
        assert result.name == "Fatimah"
