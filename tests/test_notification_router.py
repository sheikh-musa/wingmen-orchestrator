"""Tests for notification_router.py — chat ID routing logic."""

from notification_router import get_chat_id


class TestGetChatId:
    def test_cto_returns_group_id(self):
        result = get_chat_id("cto")
        assert result == "cto-group-999"

    def test_cto_falls_back_to_musa(self, monkeypatch):
        monkeypatch.delenv("CTO_GROUP_ID", raising=False)
        result = get_chat_id("cto")
        assert result == "123456"

    def test_ops_returns_ops_group(self, monkeypatch):
        monkeypatch.setenv("OPS_GROUP_ID", "ops-group-777")
        result = get_chat_id("ops")
        assert result == "ops-group-777"

    def test_ops_falls_back_to_musa(self, monkeypatch):
        monkeypatch.delenv("OPS_GROUP_ID", raising=False)
        result = get_chat_id("ops")
        assert result == "123456"

    def test_unknown_category_returns_musa(self):
        result = get_chat_id("random")
        assert result == "123456"
