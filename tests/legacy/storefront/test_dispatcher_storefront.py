"""Behavioral tests for the shared-bot path in message_dispatcher.

Covers the platform-bot slug resolution helpers that drive /start <slug>:
known slug -> welcome + web_app Mini App button; unknown slug -> graceful
fallback with NO button; and the read-only contract (the slug path must
never mutate clients).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from legacy.message_dispatcher import _handle_storefront_start, _resolve_storefront_slug
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Records the read chain and forbids any mutation call."""

    def __init__(self, data):
        self._data = data

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    async def execute(self):
        return _FakeResult(self._data)

    def _forbidden(self, *_a, **_k):
        raise AssertionError("storefront slug path must be read-only")

    insert = update = upsert = delete = _forbidden


class _FakeSupabase:
    def __init__(self, data):
        self._data = data

    def table(self, _name):
        return _FakeQuery(self._data)


class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


async def test_unknown_slug_sends_fallback_without_button():
    bot = _FakeBot()
    sb = _FakeSupabase([])  # no matching row
    await _handle_storefront_start(bot, sb, chat_id="99", slug="nope")
    assert len(bot.sent) == 1
    msg = bot.sent[0]
    assert "doesn't look right" in msg["text"]
    assert msg.get("reply_markup") is None


async def test_known_slug_sends_welcome_and_miniapp_button():
    bot = _FakeBot()
    sb = _FakeSupabase(
        [{"storefront_slug": "aunty-mariam", "name": "Aunty Mariam Kitchen",
          "welcome_message": "Salam, welcome!"}]
    )
    await _handle_storefront_start(bot, sb, chat_id="99", slug="aunty-mariam")
    assert len(bot.sent) == 1
    msg = bot.sent[0]
    assert msg["text"].startswith("Salam, welcome!")
    kb = msg["reply_markup"]
    button = kb.inline_keyboard[0][0]
    assert button.web_app.url == "https://ihsanos.com/shop/aunty-mariam"


async def test_known_slug_without_welcome_uses_default():
    bot = _FakeBot()
    sb = _FakeSupabase(
        [{"storefront_slug": "shop_42", "name": "Corner Bakes",
          "welcome_message": None}]
    )
    await _handle_storefront_start(bot, sb, chat_id="99", slug="shop_42")
    assert bot.sent[0]["text"].startswith("Welcome to Corner Bakes!")


async def test_resolve_returns_none_when_no_row():
    sb = _FakeSupabase([])
    assert await _resolve_storefront_slug(sb, "missing") is None


async def test_resolve_returns_row_when_present():
    row = {"storefront_slug": "shop_42", "name": "Corner Bakes",
           "welcome_message": None}
    sb = _FakeSupabase([row])
    assert await _resolve_storefront_slug(sb, "shop_42") == row
