"""Frictionless storefront org onboarding under the shared platform bot.

register_storefront_org writes/updates a clients row (NO telegram_bot_token —
the shared bot owns the token) and returns the customer deep-link. The legacy
per-merchant flow in bot_onboarding.py stays DORMANT.
"""
from __future__ import annotations

from storefront.slug import validate_slug


def build_deep_link(bot_username: str, slug: str) -> str:
    """t.me deep-link that lands the customer in `slug`'s shop."""
    handle = bot_username.lstrip("@")
    return f"https://t.me/{handle}?start={slug}"


def register_storefront_org(
    supabase,
    *,
    bot_username: str,
    name: str,
    ihsanos_org_id: str,
    slug: str,
    capabilities: list[str] | None = None,
) -> str:
    """Register a merchant org behind the shared bot. Returns the deep-link.

    Idempotent on storefront_slug (upsert). Raises ValueError on a bad slug.
    """
    if not validate_slug(slug):
        raise ValueError(f"invalid slug: {slug!r}")

    supabase.table("clients").upsert(
        {
            "name": name,
            "ihsanos_org_id": ihsanos_org_id,
            "storefront_slug": slug,
            "platform": "ihsanos",
            "capabilities": capabilities or ["storefront"],
            "active": True,
        },
        on_conflict="storefront_slug",
    ).execute()

    return build_deep_link(bot_username, slug)
