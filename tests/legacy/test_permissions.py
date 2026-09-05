from legacy.permissions import get_allowed_intents, can_do
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



class TestPermissions:
    def test_owner_gets_everything(self):
        allowed = get_allowed_intents("owner", ["storefront", "qurban", "bug_report"])
        assert "site_edit" in allowed
        assert "manage_team" in allowed
        assert "place_order" in allowed
        assert "book_qurban" in allowed
        assert "bug_report" in allowed

    def test_customer_can_order(self):
        assert can_do("customer", ["storefront"], "place_order") is True

    def test_customer_cannot_edit_site(self):
        assert can_do("customer", ["storefront"], "site_edit") is False

    def test_manager_cannot_manage_team(self):
        assert can_do("manager", ["storefront"], "manage_team") is False

    def test_staff_limited(self):
        allowed = get_allowed_intents("staff", ["storefront", "qurban"])
        assert "place_order" not in allowed  # staff can't place orders
        assert "view_orders" in allowed

    def test_empty_capabilities(self):
        allowed = get_allowed_intents("customer", [])
        assert allowed == {"chat", "help"}

    def test_unknown_role_gets_minimum(self):
        allowed = get_allowed_intents("unknown_role", ["storefront"])
        assert allowed == {"chat", "help"}
