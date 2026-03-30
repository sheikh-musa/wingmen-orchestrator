"""Tests for agent dispatch and shared helpers."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import json

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.asyncio
async def test_call_claude_returns_stdout():
    """_call_claude returns stripped stdout from Claude CLI."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"  Hello world  ", b"")

    with patch("cto_bot.asyncio.create_subprocess_exec", return_value=mock_proc):
        from cto_bot import _call_claude
        result = await _call_claude("test prompt", tools="Read", timeout=30)
        assert result == "Hello world"


@pytest.mark.asyncio
async def test_call_claude_returns_empty_on_no_output():
    """_call_claude returns empty string when Claude produces no output."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"some error")

    with patch("cto_bot.asyncio.create_subprocess_exec", return_value=mock_proc):
        from cto_bot import _call_claude
        result = await _call_claude("test prompt", tools="Read", timeout=30)
        assert result == ""


@pytest.mark.asyncio
async def test_call_claude_timeout_kills_process():
    """_call_claude kills the process on timeout and returns empty string."""
    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = asyncio.TimeoutError()
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch("cto_bot.asyncio.create_subprocess_exec", return_value=mock_proc):
        from cto_bot import _call_claude
        result = await _call_claude("test prompt", tools="Read", timeout=1)
        assert result == ""
        mock_proc.kill.assert_called_once()
