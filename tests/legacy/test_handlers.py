import os
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from legacy.permissions import can_do

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



class TestHandlerPermissions:
    """Verify handlers are gated by correct permissions."""

    def test_customer_can_order(self):
        assert can_do("customer", ["storefront"], "place_order")

    def test_customer_cannot_edit_site(self):
        assert not can_do("customer", ["storefront"], "site_edit")

    def test_owner_can_manage_team(self):
        assert can_do("owner", ["storefront"], "manage_team")

    def test_staff_cannot_manage_team(self):
        assert not can_do("staff", ["storefront"], "manage_team")

    def test_customer_can_book_qurban(self):
        assert can_do("customer", ["qurban"], "book_qurban")
