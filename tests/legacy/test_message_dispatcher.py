import os
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("MUSA_TELEGRAM_ID", "123456")

from legacy.message_dispatcher import _command_to_intent, FLOW_HANDLERS, FLOW_TO_INTENT

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



class TestCommandMapping:
    def test_order_command(self):
        assert _command_to_intent("/order") == "place_order"

    def test_menu_command(self):
        assert _command_to_intent("/menu") == "place_order"

    def test_qurban_command(self):
        assert _command_to_intent("/qurban") == "book_qurban"

    def test_bug_command(self):
        assert _command_to_intent("/bug") == "bug_report"

    def test_help_command(self):
        assert _command_to_intent("/help") == "help"

    def test_team_command(self):
        assert _command_to_intent("/team") == "manage_team"

    def test_edit_command(self):
        assert _command_to_intent("/edit") == "site_edit"

    def test_track_command(self):
        assert _command_to_intent("/track") == "track_order"

    def test_non_command(self):
        assert _command_to_intent("hello") is None

    def test_unknown_command(self):
        assert _command_to_intent("/random") is None

    def test_command_with_args(self):
        assert _command_to_intent("/order 2 briyani") == "place_order"

    def test_command_case_insensitive(self):
        assert _command_to_intent("/ORDER") == "place_order"


class TestFlowMapping:
    def test_all_flow_handlers_exist(self):
        for intent, handler in FLOW_HANDLERS.items():
            assert hasattr(handler, "handle"), f"Handler for {intent} missing handle()"

    def test_flow_to_intent_covers_all_flows(self):
        expected_flows = {"ordering", "qurban_booking", "site_edit", "team_manage"}
        assert set(FLOW_TO_INTENT.keys()) == expected_flows

    def test_flow_intents_map_to_handlers(self):
        for flow, intent in FLOW_TO_INTENT.items():
            assert intent in FLOW_HANDLERS, f"Flow {flow} maps to {intent} but no handler exists"

    def test_handler_count(self):
        assert len(FLOW_HANDLERS) == 5

    def test_expected_intents_covered(self):
        expected = {"place_order", "book_qurban", "site_edit", "manage_team", "help"}
        assert set(FLOW_HANDLERS.keys()) == expected
