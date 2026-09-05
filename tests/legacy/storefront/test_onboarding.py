"""Frictionless org onboarding for the shared platform bot.

No BotFather token paste (that's the dormant per-merchant path). Onboarding
writes a clients row carrying ihsanos_org_id + storefront_slug, then emits
the customer deep-link t.me/<bot_username>?start=<slug>.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from legacy.storefront.onboarding import build_deep_link, register_storefront_org

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def test_build_deep_link():
    assert (
        build_deep_link("dookanabot", "aunty-mariam")
        == "https://t.me/dookanabot?start=aunty-mariam"
    )


def test_build_deep_link_strips_at_sign():
    assert (
        build_deep_link("@dookanabot", "shop_42")
        == "https://t.me/dookanabot?start=shop_42"
    )


def test_register_rejects_invalid_slug():
    with pytest.raises(ValueError, match="invalid slug"):
        register_storefront_org(
            _FakeSupabase(), bot_username="dookanabot",
            name="X", ihsanos_org_id="org_1", slug="Bad Slug",
        )


def test_register_writes_clients_row_and_returns_link():
    sb = _FakeSupabase()
    link = register_storefront_org(
        sb, bot_username="dookanabot",
        name="Aunty Mariam Kitchen", ihsanos_org_id="org_1",
        slug="aunty-mariam", capabilities=["storefront"],
    )
    assert link == "https://t.me/dookanabot?start=aunty-mariam"
    assert sb.upserted["storefront_slug"] == "aunty-mariam"
    assert sb.upserted["ihsanos_org_id"] == "org_1"
    assert sb.upserted["name"] == "Aunty Mariam Kitchen"
    assert sb.upserted["capabilities"] == ["storefront"]


class _FakeSupabase:
    """Minimal stand-in for the sync upsert path used by register_storefront_org."""

    def __init__(self):
        self.upserted: dict = {}

    def table(self, _name):
        return self

    def upsert(self, row, **_kw):
        self.upserted = row
        return self

    def execute(self):
        return self
