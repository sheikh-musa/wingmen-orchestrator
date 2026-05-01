"""Tests for ai_provider module — post-CAI-PROCESS-MAX-FIRST-001 routing."""

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json

# Set test env before importing
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from ai_provider import _get_provider, call_ai, extract_json, _yield_if_interactive_active


class TestProviderRouting:
    """Test model hint -> provider routing.

    Post CAI-PROCESS-MAX-FIRST-001: cli_route is the default for "auto",
    "claude", "fast", "local-without-ollama", and unknowns. Direct API only
    when caller explicitly opts in via "claude_api".
    """

    def test_claude_routes_to_cli_by_default(self):
        """FLIPPED per MAX-FIRST: 'claude' hint now means cli_route, not direct API."""
        assert _get_provider("claude") == "cli_route"

    def test_explicit_claude_api_routes_to_direct(self):
        """Explicit 'claude_api' is the opt-in for direct API (carve-out hot paths)."""
        assert _get_provider("claude_api") == "claude"

    def test_auto_defaults_to_cli_route(self):
        with patch.dict(os.environ, {"AI_DEFAULT_PROVIDER": "claude"}, clear=False):
            # Operator-set 'claude' default doesn't escape MAX-FIRST
            assert _get_provider("auto") == "cli_route"

    def test_auto_explicit_cli_route_default(self):
        with patch.dict(os.environ, {"AI_DEFAULT_PROVIDER": "cli_route"}, clear=False):
            assert _get_provider("auto") == "cli_route"

    def test_auto_uses_local_when_ollama_available(self):
        with patch.dict(os.environ, {"AI_DEFAULT_PROVIDER": "local",
                                      "OLLAMA_BASE_URL": "http://localhost:11434"}, clear=False):
            assert _get_provider("auto") == "ollama"

    def test_local_falls_back_to_cli_route_without_ollama(self):
        env = os.environ.copy()
        env.pop("OLLAMA_BASE_URL", None)
        with patch.dict(os.environ, env, clear=True):
            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            assert _get_provider("local") == "cli_route"

    def test_fast_defaults_to_cli_route(self):
        with patch.dict(os.environ, {"AI_FAST_PROVIDER": "cli_route"}, clear=False):
            assert _get_provider("fast") == "cli_route"

    def test_unknown_model_defaults_to_cli_route(self):
        assert _get_provider("unknown") == "cli_route"


class TestExtractJson:
    """Test JSON extraction from AI responses (unchanged by MAX-FIRST)."""

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
    """Test the main call_ai function — post-MAX-FIRST routing."""

    @pytest.mark.asyncio
    async def test_routes_to_cli_by_default(self):
        """Default routing flipped: text-only goes through cli_route, not direct API."""
        with patch("ai_provider._call_cli_route", new_callable=AsyncMock,
                   return_value="response") as mock:
            result = await call_ai("test prompt")
            assert result == "response"
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_json_mode_appends_to_system(self):
        with patch("ai_provider._call_cli_route", new_callable=AsyncMock,
                   return_value="{}") as mock:
            await call_ai("test", system="base", json_mode=True)
            call_args = mock.call_args
            assert "valid JSON only" in call_args.kwargs.get("system", call_args[1].get("system", ""))

    @pytest.mark.asyncio
    async def test_images_force_direct_api(self):
        """Carve-Out 3 (vision_multimodal) — images force direct API,
        bypass cli_route which doesn't expose vision."""
        with patch("ai_provider._call_claude", new_callable=AsyncMock,
                   return_value="response") as claude_mock, \
             patch("ai_provider._call_cli_route", new_callable=AsyncMock) as cli_mock:
            await call_ai("test", images=["http://example.com/img.png"])
            claude_mock.assert_called_once()
            cli_mock.assert_not_called()
            call_args = claude_mock.call_args
            assert call_args.kwargs.get("images") or call_args[1].get("images")

    @pytest.mark.asyncio
    async def test_explicit_claude_api_routes_direct(self):
        """model='claude_api' opt-in routes to direct API (carve-outs 1/2/5)."""
        with patch("ai_provider._call_claude", new_callable=AsyncMock,
                   return_value="response") as claude_mock, \
             patch("ai_provider._call_cli_route", new_callable=AsyncMock) as cli_mock:
            await call_ai("test", model="claude_api")
            claude_mock.assert_called_once()
            cli_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_ollama_fallback_to_cli_route_on_failure(self):
        """Ollama failure falls back to cli_route (NOT direct API per MAX-FIRST)."""
        with patch.dict(os.environ, {"AI_DEFAULT_PROVIDER": "local",
                                      "OLLAMA_BASE_URL": "http://localhost:11434"}):
            with patch("ai_provider._call_ollama", new_callable=AsyncMock,
                       side_effect=Exception("connection refused")), \
                 patch("ai_provider._call_cli_route", new_callable=AsyncMock,
                       return_value="fallback") as cli_mock, \
                 patch("ai_provider._call_claude", new_callable=AsyncMock) as claude_mock:
                result = await call_ai("test", model="local")
                assert result == "fallback"
                cli_mock.assert_called_once()
                claude_mock.assert_not_called()  # MAX-FIRST: no silent direct API fallback

    @pytest.mark.asyncio
    async def test_cli_route_failure_does_not_silently_fall_back(self):
        """If cli_route fails, exception propagates — caller decides whether to
        opt into direct API via claude_api. Silent fallback would defeat the
        Audit 5 signal."""
        with patch("ai_provider._call_cli_route", new_callable=AsyncMock,
                   side_effect=RuntimeError("claude -p timeout")):
            with pytest.raises(RuntimeError, match="timeout"):
                await call_ai("test")


class TestYieldIfInteractiveActive:
    """Test the yield mechanism (Mitigation 2 of CAI-PROCESS-MAX-FIRST-001 (d))."""

    @pytest.mark.asyncio
    async def test_no_marker_returns_immediately(self, tmp_path):
        marker = tmp_path / "absent_cc_active"
        with patch("ai_provider._CC_ACTIVE_MARKER", marker):
            await _yield_if_interactive_active()  # Must not raise or hang

    @pytest.mark.asyncio
    async def test_stale_marker_returns_immediately(self, tmp_path):
        """Marker mtime older than threshold → proceed."""
        import time
        marker = tmp_path / "stale_cc_active"
        marker.write_text("ok")
        # Force mtime 10 minutes ago
        ten_min_ago = time.time() - 600
        os.utime(marker, (ten_min_ago, ten_min_ago))
        with patch("ai_provider._CC_ACTIVE_MARKER", marker), \
             patch.dict(os.environ, {"AI_CLI_YIELD_THRESHOLD_SECONDS": "300"}, clear=False):
            await _yield_if_interactive_active()

    @pytest.mark.asyncio
    async def test_fresh_marker_yields_then_proceeds_when_stale(self, tmp_path, monkeypatch):
        """Marker initially fresh (yield); after one poll cycle simulate
        marker becoming stale → return."""
        import time
        marker = tmp_path / "cc_active"
        marker.write_text("ok")
        # Fresh
        os.utime(marker, (time.time(), time.time()))

        sleeps = []

        async def fast_sleep(s):
            sleeps.append(s)
            # On the 2nd call, simulate marker going stale
            if len(sleeps) == 1:
                stale = time.time() - 600
                os.utime(marker, (stale, stale))

        with patch("ai_provider._CC_ACTIVE_MARKER", marker), \
             patch("ai_provider.asyncio.sleep", side_effect=fast_sleep), \
             patch.dict(os.environ, {"AI_CLI_YIELD_THRESHOLD_SECONDS": "300",
                                      "AI_CLI_YIELD_HARD_CAP_SECONDS": "1800"},
                        clear=False):
            await _yield_if_interactive_active()
        assert len(sleeps) >= 1, "should have yielded at least once"
