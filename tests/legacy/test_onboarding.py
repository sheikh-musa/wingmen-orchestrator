import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from bot_onboarding import validate_token, get_default_commands, configure_bot


class TestValidateToken:
    @pytest.mark.asyncio
    async def test_valid_token(self):
        with patch("bot_onboarding.httpx.AsyncClient") as mock:
            instance = AsyncMock()
            mock.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock.return_value.__aexit__ = AsyncMock()
            instance.get.return_value = MagicMock(json=lambda: {"ok": True, "result": {"username": "test_bot", "id": 123}})
            result = await validate_token("fake-token")
            assert result["username"] == "test_bot"

    @pytest.mark.asyncio
    async def test_invalid_token(self):
        with patch("bot_onboarding.httpx.AsyncClient") as mock:
            instance = AsyncMock()
            mock.return_value.__aenter__ = AsyncMock(return_value=instance)
            mock.return_value.__aexit__ = AsyncMock()
            instance.get.return_value = MagicMock(json=lambda: {"ok": False})
            result = await validate_token("bad-token")
            assert result is None


class TestDefaultCommands:
    def test_base_commands(self):
        cmds = get_default_commands([])
        assert len(cmds) == 2  # start + help

    def test_storefront_adds_menu_and_order(self):
        cmds = get_default_commands(["storefront"])
        cmd_names = [c["command"] for c in cmds]
        assert "menu" in cmd_names
        assert "order" in cmd_names

    def test_qurban_adds_command(self):
        cmds = get_default_commands(["qurban"])
        cmd_names = [c["command"] for c in cmds]
        assert "qurban" in cmd_names
