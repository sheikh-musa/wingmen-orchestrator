"""Tests for the uptime monitor module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from legacy.uptime_monitor import check_target, poll_uptime
from tests.conftest import mock_supabase_chain

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



# ---------------------------------------------------------------------------
# check_target
# ---------------------------------------------------------------------------

class TestCheckTarget:

    @pytest.mark.asyncio
    async def test_healthy_site(self):
        mock_resp = MagicMock(status_code=200)
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await check_target(client, "https://example.com")

        assert result["up"] is True
        assert result["status_code"] == 200
        assert result["error"] is None
        assert isinstance(result["response_time_ms"], int)

    @pytest.mark.asyncio
    async def test_server_error(self):
        mock_resp = MagicMock(status_code=500)
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await check_target(client, "https://example.com")

        assert result["up"] is False
        assert result["status_code"] == 500

    @pytest.mark.asyncio
    async def test_connection_error(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        result = await check_target(client, "https://example.com")

        assert result["up"] is False
        assert result["status_code"] is None
        assert "Connection refused" in result["error"]


# ---------------------------------------------------------------------------
# poll_uptime
# ---------------------------------------------------------------------------

def _make_supabase(notification_data=None):
    """Build a mock supabase that tracks calls per table."""
    if notification_data is None:
        notification_data = []

    mock = MagicMock()
    mock.table.return_value = mock
    mock.select.return_value = mock
    mock.eq.return_value = mock
    mock.limit.return_value = mock
    mock.insert.return_value = mock
    mock.upsert.return_value = mock
    mock.delete.return_value = mock

    result_mock = MagicMock(data=notification_data)
    mock.execute = AsyncMock(return_value=result_mock)
    return mock


class TestPollUptime:

    @pytest.mark.asyncio
    @patch("uptime_monitor.check_target")
    async def test_all_healthy_no_alert(self, mock_check):
        mock_check.return_value = {
            "up": True, "status_code": 200, "error": None, "response_time_ms": 50,
        }
        supabase = _make_supabase()
        bot = AsyncMock()

        await poll_uptime(supabase, bot, "123456")

        bot.send_message.assert_not_called()
        supabase.upsert.assert_called_once()
        upsert_args = supabase.upsert.call_args[0][0]
        assert upsert_args["status"] == "healthy"

    @pytest.mark.asyncio
    @patch("uptime_monitor.check_target")
    async def test_one_down_sends_alert(self, mock_check):
        async def side_effect(client, url):
            if "ihsanos" in url:
                return {"up": False, "status_code": 500, "error": None, "response_time_ms": 100}
            return {"up": True, "status_code": 200, "error": None, "response_time_ms": 50}

        mock_check.side_effect = side_effect
        supabase = _make_supabase(notification_data=[])
        bot = AsyncMock()

        await poll_uptime(supabase, bot, "123456")

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert "ihsanOS" in call_kwargs["text"]
        assert "DOWN" in call_kwargs["text"].upper()
        supabase.insert.assert_called_once()

    @pytest.mark.asyncio
    @patch("uptime_monitor.check_target")
    async def test_dedup_prevents_repeat_alert(self, mock_check):
        mock_check.return_value = {
            "up": False, "status_code": 503, "error": None, "response_time_ms": 200,
        }
        supabase = _make_supabase(notification_data=[{"id": 1}])
        bot = AsyncMock()

        await poll_uptime(supabase, bot, "123456")

        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    @patch("uptime_monitor.check_target")
    async def test_recovery_clears_dedup(self, mock_check):
        mock_check.return_value = {
            "up": True, "status_code": 200, "error": None, "response_time_ms": 40,
        }
        supabase = _make_supabase()
        bot = AsyncMock()

        await poll_uptime(supabase, bot, "123456")

        supabase.delete.assert_called()
