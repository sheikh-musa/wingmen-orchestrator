"""Telegram Mini App launch surface for the shared storefront bot."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def build_shop_url(slug: str, web_base: str) -> str:
    """HTTPS URL of the merchant's Mini App storefront."""
    return f"{web_base.rstrip('/')}/shop/{slug}"


def build_miniapp_keyboard(slug: str, web_base: str) -> InlineKeyboardMarkup:
    """A one-button keyboard that opens the merchant's Mini App in-app."""
    url = build_shop_url(slug, web_base)
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="🛍️ Open Shop", web_app=WebAppInfo(url=url))]]
    )
