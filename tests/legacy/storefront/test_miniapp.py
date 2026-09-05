"""web_app launch button that opens the ihsanos Mini App inside Telegram.

Telegram requires the web_app URL to be HTTPS. The shop lives at
<storefront_web_base>/shop/<slug> (cc-ihsanos owns the /shop/{slug} route).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from legacy.storefront.miniapp import build_miniapp_keyboard, build_shop_url
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def test_build_shop_url_default_base():
    assert (
        build_shop_url("aunty-mariam", "https://ihsanos.com")
        == "https://ihsanos.com/shop/aunty-mariam"
    )


def test_build_shop_url_strips_trailing_slash():
    assert (
        build_shop_url("shop_42", "https://preview.ihsanos.com/")
        == "https://preview.ihsanos.com/shop/shop_42"
    )


def test_keyboard_has_single_web_app_button():
    kb = build_miniapp_keyboard("aunty-mariam", "https://ihsanos.com")
    assert len(kb.inline_keyboard) == 1
    row = kb.inline_keyboard[0]
    assert len(row) == 1
    button = row[0]
    assert button.web_app is not None
    assert button.web_app.url == "https://ihsanos.com/shop/aunty-mariam"
    assert "Shop" in button.text
