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


from agents.router import build_router_prompt, parse_router_response


def test_build_router_prompt_includes_repos():
    repos = ["ihsandms", "dookana"]
    history = [
        {"role": "user", "content": "check the pages"},
        {"role": "assistant", "content": "Which repo?"},
    ]
    prompt = build_router_prompt("fix the homepage", repos, history)
    assert "ihsandms" in prompt
    assert "dookana" in prompt
    assert "fix the homepage" in prompt
    assert "check the pages" in prompt


def test_parse_router_response_valid_json():
    raw = '{"intent": "audit", "repo": "ihsandms", "detail": "check pages"}'
    result = parse_router_response(raw)
    assert result["intent"] == "audit"
    assert result["repo"] == "ihsandms"


def test_parse_router_response_extracts_json_from_text():
    raw = 'Here is my analysis:\n{"intent": "chat", "repo": "dookana", "detail": "brainstorm"}\nDone.'
    result = parse_router_response(raw)
    assert result["intent"] == "chat"


def test_parse_router_response_fallback_on_garbage():
    raw = "I'm not sure what you mean"
    result = parse_router_response(raw)
    assert result["intent"] == "chat"
    assert result["repo"] is None


def test_parse_router_response_fallback_on_invalid_intent():
    raw = '{"intent": "destroy", "repo": "ihsandms", "detail": "nuke it"}'
    result = parse_router_response(raw)
    assert result["intent"] == "chat"


from agents.brainstorm import build_brainstorm_prompt


def test_brainstorm_prompt_admin():
    user = {"name": "Musa", "repos": ["ihsandms", "dookana"], "role": "admin"}
    prompt = build_brainstorm_prompt(
        user=user,
        repo_context="--- PROJECT RULES ---\nUse Tailwind",
        history=[{"role": "user", "content": "build a new page"}],
        user_msg="build a new page",
    )
    assert "CTO" in prompt or "architect" in prompt
    assert "ACTION:BUILD" in prompt
    assert "ACTION:DATA" in prompt
    assert "ihsandms" in prompt
    assert "crawl" not in prompt.lower()
    assert "npx vercel" not in prompt


def test_brainstorm_prompt_client():
    user = {"name": "Ahmad", "repos": ["dookana"], "role": "client"}
    prompt = build_brainstorm_prompt(
        user=user,
        repo_context="",
        history=[],
        user_msg="update price",
    )
    assert "advisor" in prompt.lower() or "partner" in prompt.lower()
    assert "Ahmad" in prompt
    assert "ACTION:DATA" in prompt
    assert "git" not in prompt


from agents.fixer import build_fixer_prompt


def test_fixer_prompt_includes_issue_details():
    issue = {
        "page": "/",
        "severity": "high",
        "description": "Duplicate cards both linking to /my",
        "fix_confidence": "high",
        "file_path": "app/page.tsx",
        "suggested_fix": "Remove the duplicate My Portal card",
    }
    prompt = build_fixer_prompt(
        issue=issue,
        repo_path="/Users/sheikhmusa/wingmen/projects/ihsandms",
    )
    assert "app/page.tsx" in prompt
    assert "Duplicate cards" in prompt
    assert "Remove the duplicate" in prompt
    assert "/Users/sheikhmusa/wingmen/projects/ihsandms" in prompt
    assert "crawl" not in prompt.lower()
    assert "ACTION:BUILD" not in prompt
    assert "git push" not in prompt


def test_fixer_prompt_minimal_context():
    issue = {
        "description": "Fix typo",
        "file_path": "app/admin/page.tsx",
        "suggested_fix": "Change 'Donr' to 'Donor'",
    }
    prompt = build_fixer_prompt(
        issue=issue,
        repo_path="/tmp/test",
    )
    assert len(prompt) < 1500
