"""Unit tests for scripts/onboard_client_channel.py's validation logic.

Pure-logic tests only (stub DB cursors) -- no live DATABASE_URL touch, so
these run under the static/no-DB test lane same as
tests/migrations/test_ingest_channels_enabled_false.py.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCH_DIR / "scripts"))

import onboard_client_channel as tool  # noqa: E402


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self._result = None

    def execute(self, sql, params=None):
        self._result = self._rows

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result or []


def test_check_token_dedication_refuses_client_on_shared_token():
    cur = FakeCursor([("coffeemedia", "internal"), ("angullia", "client")])
    with pytest.raises(SystemExit):
        tool.check_token_dedication(cur, "WINGMENDEVBOT_TOKEN", "client", "zz-new")


def test_check_token_dedication_refuses_internal_sharing_with_client():
    cur = FakeCursor([("angullia", "client")])
    with pytest.raises(SystemExit):
        tool.check_token_dedication(cur, "WINGMENDEVBOT_TOKEN", "internal", "zz-new")


def test_check_token_dedication_allows_fresh_dedicated_token():
    cur = FakeCursor([])
    tool.check_token_dedication(cur, "ZZ_FRESH_BOT_TOKEN", "client", "zz-new")  # no raise


def test_check_token_dedication_allows_internal_sharing_with_internal():
    cur = FakeCursor([("coffeemedia", "internal")])
    tool.check_token_dedication(cur, "WINGMENDEVBOT_TOKEN", "internal", "zz-new")  # no raise


def test_check_channel_key_free_refuses_existing():
    cur = FakeCursor([(1,)])
    with pytest.raises(SystemExit):
        tool.check_channel_key_free(cur, "coffeemedia")


def test_check_channel_key_free_allows_new():
    cur = FakeCursor([])
    tool.check_channel_key_free(cur, "zz-brand-new")  # no raise


def test_host_consistency_refuses_hub_when_sibling_pinned():
    cur = FakeCursor([("coffeemedia", False)])
    with mock.patch.object(tool, "pinned_channels", return_value=["coffeemedia"]):
        with pytest.raises(SystemExit):
            tool.check_host_token_consistency(cur, "WINGMENDEVBOT_TOKEN", "hub", "zz-new")


def test_host_consistency_refuses_mini_when_sibling_hub_enabled():
    cur = FakeCursor([("some-hub-channel", True)])
    with mock.patch.object(tool, "pinned_channels", return_value=[]):
        with pytest.raises(SystemExit):
            tool.check_host_token_consistency(cur, "SOME_TOKEN", "mini", "zz-new")


def test_host_consistency_allows_matching_mini_pin():
    cur = FakeCursor([("coffeemedia", False)])
    with mock.patch.object(tool, "pinned_channels", return_value=["coffeemedia"]):
        tool.check_host_token_consistency(cur, "WINGMENDEVBOT_TOKEN", "mini", "zz-new")  # no raise


def test_host_consistency_allows_fresh_token_no_siblings():
    cur = FakeCursor([])
    with mock.patch.object(tool, "pinned_channels", return_value=[]):
        tool.check_host_token_consistency(cur, "ZZ_FRESH_TOKEN", "hub", "zz-new")  # no raise


def make_args(**overrides):
    base = dict(
        channel_key="zz", chat_ids=[1], token_env_key="ZZ_TOKEN", tag=None,
        mode="agent-session", phase=None, audience="internal", owner_lane=None,
        store="none", powers="reply_text", agent_reviewer=None, host="hub",
        override_to_direct=False, override_gate_citation=None,
        inject_target="zz", inject_prefix=None, responder_ref=None,
        sensitive_data="true",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_phase_defaults_supervised_for_client():
    args = make_args(audience="client", phase=None)
    assert tool.resolve_phase(args) == "supervised"


def test_resolve_phase_defaults_direct_for_internal():
    args = make_args(audience="internal", phase=None)
    assert tool.resolve_phase(args) == "direct"


def test_resolve_phase_blocks_direct_with_multiple_chat_ids():
    args = make_args(audience="internal", phase="direct", chat_ids=[1, 2])
    with pytest.raises(SystemExit):
        tool.resolve_phase(args)


def test_resolve_phase_blocks_client_direct_without_override():
    args = make_args(audience="client", phase="direct", chat_ids=[1], override_to_direct=False)
    with pytest.raises(SystemExit):
        tool.resolve_phase(args)


def test_resolve_phase_blocks_override_without_citation():
    args = make_args(
        audience="client", phase="direct", chat_ids=[1],
        override_to_direct=True, override_gate_citation=None,
    )
    with pytest.raises(SystemExit):
        tool.resolve_phase(args)


def test_resolve_phase_allows_override_with_citation():
    args = make_args(
        audience="client", phase="direct", chat_ids=[1],
        override_to_direct=True, override_gate_citation="#12345",
    )
    assert tool.resolve_phase(args) == "direct"


def test_resolve_phase_allows_single_chat_direct_internal():
    args = make_args(audience="internal", phase="direct", chat_ids=[1])
    assert tool.resolve_phase(args) == "direct"


def test_validate_store_accepts_none():
    tool.validate_store("none")  # no raise
    tool.validate_store("NONE")  # no raise


def test_validate_store_accepts_known_ref():
    tool.validate_store(f"substrate `{tool.SUBSTRATE_SILO}`")  # no raise


def test_validate_store_refuses_unknown_ref():
    with pytest.raises(SystemExit):
        tool.validate_store("some made up store with no backticked ref")


def test_validate_mode_target_refuses_inject_target_on_log_and_route():
    with pytest.raises(SystemExit):
        tool.validate_mode_target("log-and-route", "somelane")


def test_validate_mode_target_requires_inject_target_on_agent_session():
    with pytest.raises(SystemExit):
        tool.validate_mode_target("agent-session", None)


def test_validate_mode_target_allows_valid_combos():
    tool.validate_mode_target("log-and-route", None)
    tool.validate_mode_target("agent-session", "somelane")


def test_build_group_routing_defaults_reviewer_for_supervised():
    args = make_args(agent_reviewer=None)
    routing = tool.build_group_routing(args, "supervised")
    assert routing["agent_reviewer"] == "orch-console"
    assert routing["agent_phase"] == "supervised"
    assert routing["powers"] == ["reply_text"]


def test_build_group_routing_no_reviewer_for_direct():
    args = make_args(agent_reviewer=None)
    routing = tool.build_group_routing(args, "direct")
    assert "agent_reviewer" not in routing


def test_build_group_routing_respects_explicit_reviewer():
    args = make_args(agent_reviewer="custom-reviewer")
    routing = tool.build_group_routing(args, "direct")
    assert routing["agent_reviewer"] == "custom-reviewer"


def test_build_group_routing_records_override_citation():
    args = make_args(override_to_direct=True, override_gate_citation="#999")
    routing = tool.build_group_routing(args, "direct")
    assert routing["direct_override_grant"] == "#999"


def test_render_migration_shape_matches_112_column_order():
    args = make_args(audience="client", chat_ids=[-555])
    name, sql_text = tool.render_migration(args, 200, "supervised")
    assert name == "200_zz_onboarding.sql"
    assert "-- ledger: silo=" + tool.SUBSTRATE_SILO in sql_text
    assert tool._INSERT_COLUMNS.replace("\n", " ").replace("  ", " ") in sql_text.replace("\n", " ").replace("  ", " ")
    assert "enabled, poll_offset" in sql_text
    assert ", false, NULL," in sql_text  # enabled always false, never a flag to flip it
