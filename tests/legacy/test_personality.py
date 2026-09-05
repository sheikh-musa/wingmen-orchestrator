from legacy.personality import build_system_prompt
from legacy.bot_manager import ClientBot
from legacy.bot_user_resolver import BotUser
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



class TestPersonality:
    def _make_bot(self, **kwargs):
        defaults = dict(client_id=1, token="t", token_hash="h", bot_username="test_bot",
                       bot_display_name="Test Shop", personality=None, welcome_message=None,
                       capabilities=["storefront"], repo_name="test", platform="ihsanos", ihsanos_org_id="org1")
        return ClientBot(**{**defaults, **kwargs})

    def _make_user(self, **kwargs):
        defaults = dict(id=1, client_id=1, telegram_chat_id="123", name="Ahmad",
                       role="customer", permissions=[], status="active", is_active=True)
        return BotUser(**{**defaults, **kwargs})

    def test_customer_prompt_includes_ordering(self):
        prompt = build_system_prompt(self._make_bot(), self._make_user())
        assert "customer" in prompt.lower()
        assert "online store" in prompt.lower()

    def test_owner_prompt_includes_full_access(self):
        prompt = build_system_prompt(self._make_bot(), self._make_user(role="owner"))
        assert "full access" in prompt.lower()

    def test_custom_personality_used(self):
        prompt = build_system_prompt(
            self._make_bot(personality="Ahlan wa sahlan! We serve the best Yemeni food."),
            self._make_user()
        )
        assert "Yemeni food" in prompt

    def test_qurban_capability_mentioned(self):
        prompt = build_system_prompt(
            self._make_bot(capabilities=["qurban"]),
            self._make_user()
        )
        assert "qurban" in prompt.lower()
