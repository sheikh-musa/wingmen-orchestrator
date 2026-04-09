import os
import pytest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from invite_manager import generate_invite_code, build_deep_link, create_invite


class TestInviteCode:
    def test_length_8(self):
        code = generate_invite_code()
        assert len(code) == 8

    def test_alphanumeric(self):
        code = generate_invite_code()
        assert code.isalnum()

    def test_unique(self):
        codes = {generate_invite_code() for _ in range(100)}
        assert len(codes) == 100  # all unique


class TestDeepLink:
    def test_format(self):
        link = build_deep_link("hadramawt_bot", "ABC12345")
        assert link == "https://t.me/hadramawt_bot?start=invite_ABC12345"
