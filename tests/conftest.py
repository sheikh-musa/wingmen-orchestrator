"""Shared fixtures for the Wingmen orchestrator test suite."""

import os

import pytest
from unittest.mock import AsyncMock, MagicMock


def mock_supabase_chain(final_data=None, *, count=None):
    """Build a MagicMock that mimics supabase chained query builder.

    supabase.table(...).select(...).eq(...) etc. are all sync (return self),
    only .execute() is async.
    """
    if final_data is None:
        final_data = []

    mock = MagicMock()
    mock.table.return_value = mock
    mock.select.return_value = mock
    mock.eq.return_value = mock
    mock.is_.return_value = mock
    mock.in_.return_value = mock
    mock.insert.return_value = mock
    mock.update.return_value = mock
    mock.gte.return_value = mock
    mock.lt.return_value = mock
    mock.order.return_value = mock
    mock.limit.return_value = mock
    mock.maybeSingle.return_value = mock
    mock.upsert.return_value = mock
    mock.delete.return_value = mock
    mock.not_ = mock

    result_mock = MagicMock(data=final_data, count=count)
    mock.execute = AsyncMock(return_value=result_mock)
    return mock


@pytest.fixture
def mock_supabase():
    return mock_supabase_chain()


@pytest.fixture
def sample_job():
    return {
        "id": 42,
        "repo_name": "ihsandms",
        "description": "Add login page",
        "status": "queued",
        "priority": 1,
        "fail_count": 0,
        "client_id": None,
        "triggered_by": None,
        "created_at": "2026-04-14T00:00:00Z",
        "updated_at": "2026-04-14T00:00:00Z",
        "result_summary": None,
    }


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    env_vars = {
        "ANTHROPIC_API_KEY": "test-key",
        "MUSA_TELEGRAM_ID": "123456",
        "TELEGRAM_BOT_TOKEN": "test-bot-token",
        "VERCEL_TOKEN": "test-vercel-token",
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_KEY": "test-service-key",
        "CTO_GROUP_ID": "cto-group-999",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
