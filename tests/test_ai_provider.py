"""Tests for ai_provider module."""

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json

# Set test env before importing
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from ai_provider import _get_provider, call_ai, extract_json


class TestProviderRouting:
    """Test model hint -> provider routing."""

    def test_claude_always_returns_claude(self):
        assert _get_provider("claude") == "claude"

    def test_auto_defaults_to_claude(self):
        with patch.dict(os.environ, {"AI_DEFAULT_PROVIDER": "claude"}, clear=False):
            assert _get_provider("auto") == "claude"

    def test_auto_uses_local_when_ollama_available(self):
        with patch.dict(os.environ, {"AI_DEFAULT_PROVIDER": "local", "OLLAMA_BASE_URL": "http://localhost:11434"}, clear=False):
            assert _get_provider("auto") == "ollama"

    def test_local_falls_back_to_claude_without_ollama(self):
        env = os.environ.copy()
        env.pop("OLLAMA_BASE_URL", None)
        with patch.dict(os.environ, env, clear=True):
            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            assert _get_provider("local") == "claude"

    def test_fast_defaults_to_claude(self):
        with patch.dict(os.environ, {"AI_FAST_PROVIDER": "claude"}, clear=False):
            assert _get_provider("fast") == "claude"

    def test_unknown_model_defaults_to_claude(self):
        assert _get_provider("unknown") == "claude"


class TestExtractJson:
    """Test JSON extraction from AI responses."""

    def test_direct_json(self):
        assert extract_json('{"key": "value"}') == {"key": "value"}

    def test_json_in_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}

    def test_json_in_plain_code_block(self):
        text = '```\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}

    def test_json_embedded_in_text(self):
        text = 'Here is the result: {"key": "value"} and some more text'
        assert extract_json(text) == {"key": "value"}

    def test_json_array(self):
        assert extract_json('[1, 2, 3]') == [1, 2, 3]

    def test_no_json(self):
        assert extract_json("no json here") is None

    def test_invalid_json(self):
        assert extract_json("{broken json") is None


class TestCallAi:
    """Test the main call_ai function."""

    @pytest.mark.asyncio
    async def test_routes_to_claude_by_default(self):
        with patch("ai_provider._call_claude", new_callable=AsyncMock, return_value="response") as mock:
            result = await call_ai("test prompt")
            assert result == "response"
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_json_mode_appends_to_system(self):
        with patch("ai_provider._call_claude", new_callable=AsyncMock, return_value="{}") as mock:
            await call_ai("test", system="base", json_mode=True)
            call_args = mock.call_args
            assert "valid JSON only" in call_args.kwargs.get("system", call_args[1].get("system", ""))

    @pytest.mark.asyncio
    async def test_images_force_vision_provider(self):
        with patch("ai_provider._call_claude", new_callable=AsyncMock, return_value="response") as mock:
            await call_ai("test", images=["http://example.com/img.png"])
            mock.assert_called_once()
            call_args = mock.call_args
            assert call_args.kwargs.get("images") or call_args[1].get("images")

    @pytest.mark.asyncio
    async def test_ollama_fallback_on_failure(self):
        with patch.dict(os.environ, {"AI_DEFAULT_PROVIDER": "local", "OLLAMA_BASE_URL": "http://localhost:11434"}):
            with patch("ai_provider._call_ollama", new_callable=AsyncMock, side_effect=Exception("connection refused")):
                with patch("ai_provider._call_claude", new_callable=AsyncMock, return_value="fallback") as claude_mock:
                    result = await call_ai("test", model="local")
                    assert result == "fallback"
                    claude_mock.assert_called_once()
