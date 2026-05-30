"""CADENCE-003 INV-3: ai_provider._call_cli_route subprocess env must NOT
include ANTHROPIC_API_KEY. Otherwise claude CLI sees the API key and prefers
the API path (against depleted credit balance) over the Max OAuth in
~/.claude/.credentials.json.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import ai_provider


@pytest.mark.asyncio
async def test_cli_route_env_scrubs_anthropic_api_key(monkeypatch):
    """When ANTHROPIC_API_KEY is set in the parent process env, the cli_route
    subprocess must NOT receive it. Verifies INV-3."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-be-scrubbed")

    captured_env: dict[str, str] | None = None

    async def fake_create_subprocess_exec(*args, env=None, **kwargs):
        nonlocal captured_env
        captured_env = env
        fake = MagicMock()
        fake.communicate = AsyncMock(return_value=(b"ok\n", b""))
        fake.returncode = 0
        return fake

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec), \
         patch.object(ai_provider, "_yield_if_interactive_active", new=AsyncMock()):
        result = await ai_provider._call_cli_route("hello", max_tokens=10)

    assert result == "ok"
    assert captured_env is not None, "subprocess env must be explicitly passed"
    assert "ANTHROPIC_API_KEY" not in captured_env, \
        "ANTHROPIC_API_KEY must be scrubbed before claude -p invocation"


@pytest.mark.asyncio
async def test_cli_route_env_preserves_other_vars(monkeypatch):
    """Scrubbing must be surgical — only ANTHROPIC_API_KEY removed, all else preserved.
    Otherwise we'd break PATH, HOME, CLAUDE_BIN, etc."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("FAKE_PASSTHROUGH_VAR_ABC123", "preserved-value")
    monkeypatch.setenv("CLAUDE_BIN", "/Users/sheikhmusa/.local/bin/claude")

    captured_env: dict[str, str] | None = None

    async def fake_create_subprocess_exec(*args, env=None, **kwargs):
        nonlocal captured_env
        captured_env = env
        fake = MagicMock()
        fake.communicate = AsyncMock(return_value=(b"ok\n", b""))
        fake.returncode = 0
        return fake

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec), \
         patch.object(ai_provider, "_yield_if_interactive_active", new=AsyncMock()):
        await ai_provider._call_cli_route("hello", max_tokens=10)

    assert captured_env is not None
    assert "ANTHROPIC_API_KEY" not in captured_env
    assert captured_env.get("FAKE_PASSTHROUGH_VAR_ABC123") == "preserved-value"
    # PATH should still be in env (system var that's always set)
    assert "PATH" in captured_env, "system PATH must survive scrub"
