"""Tests for heartbeat.py — health status writes to Supabase."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import mock_supabase_chain


class TestWriteHeartbeat:
    @pytest.mark.asyncio
    async def test_write_heartbeat_upserts(self):
        from heartbeat import write_heartbeat

        sb = mock_supabase_chain([])
        await write_heartbeat(sb, service="orchestrator", active_jobs=2)

        sb.upsert.assert_called_once()
        upsert_arg = sb.upsert.call_args[0][0]
        assert upsert_arg["service"] == "orchestrator"
        assert upsert_arg["status"] == "healthy"
        assert "last_ping" in upsert_arg
        assert upsert_arg["metadata"]["active_jobs"] == 2

    @pytest.mark.asyncio
    async def test_write_heartbeat_swallows_exception(self):
        from heartbeat import write_heartbeat

        sb = mock_supabase_chain([])
        sb.execute = AsyncMock(side_effect=RuntimeError("db down"))

        await write_heartbeat(sb)


class TestWriteOrchestratorHeartbeat:
    @pytest.mark.asyncio
    async def test_write_orchestrator_heartbeat(self):
        from heartbeat import write_orchestrator_heartbeat

        sb = MagicMock()
        sb.table.return_value = sb
        sb.select.return_value = sb
        sb.eq.return_value = sb
        sb.in_.return_value = sb
        sb.gte.return_value = sb
        sb.upsert.return_value = sb

        jobs_result = MagicMock(data=[], count=2)
        bugs_result = MagicMock(data=[], count=5)
        today_result = MagicMock(data=[], count=1)
        upsert_result = MagicMock(data=[])

        sb.execute = AsyncMock(side_effect=[jobs_result, bugs_result, today_result, upsert_result])

        await write_orchestrator_heartbeat(sb)

        sb.upsert.assert_called_once()
        upsert_arg = sb.upsert.call_args[0][0]
        assert upsert_arg["service"] == "orchestrator"
        assert upsert_arg["metadata"]["active_jobs"] == 2
        assert upsert_arg["metadata"]["pending_bugs"] == 5
        assert upsert_arg["metadata"]["bugs_today"] == 1
