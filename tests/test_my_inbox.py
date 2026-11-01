"""Pure-logic tests for scripts/lib/my_inbox.py's my_ids() (bus #53620, #51166
regression class — a lane's inbox read must always cover both its base and
instance id, never just one)."""
import importlib.util
import os
import sys

import pytest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "lib", "my_inbox.py")
_spec = importlib.util.spec_from_file_location("my_inbox", _PATH)
my_inbox = importlib.util.module_from_spec(_spec)
sys.modules["my_inbox"] = my_inbox
_spec.loader.exec_module(my_inbox)


def test_my_ids_returns_both_base_and_instance_when_distinct(monkeypatch):
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setenv("CC_AGENT_ID", "cc-substrate-1")
    assert my_inbox.my_ids() == ["cc-substrate", "cc-substrate-1"]


def test_my_ids_returns_just_base_when_instance_equals_base(monkeypatch):
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setenv("CC_AGENT_ID", "cc-substrate")
    assert my_inbox.my_ids() == ["cc-substrate"]


def test_my_ids_returns_just_base_when_no_instance_set(monkeypatch):
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.delenv("CC_AGENT_ID", raising=False)
    assert my_inbox.my_ids() == ["cc-substrate"]


def test_my_ids_falls_back_to_agent_id_when_no_base(monkeypatch):
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.setenv("AGENT_ID", "cai")
    monkeypatch.delenv("CC_AGENT_ID", raising=False)
    assert my_inbox.my_ids() == ["cai"]


def test_my_ids_fails_loud_with_no_identity_at_all(monkeypatch):
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.delenv("CC_AGENT_ID", raising=False)
    with pytest.raises(SystemExit):
        my_inbox.my_ids()


# ── the bus DSN name (orch-console #90166, op#892) ──────────────────────────
# A LANE's scrubbed env carries the bus DSN only as BUS_DATABASE_URL; this file
# is what MAP.md and the SessionStart notice tell a lane to read its mail with,
# so a DATABASE_URL-only lookup made the sanctioned reader unusable inside a lane.

def test_bus_dsn_prefers_the_lane_name(monkeypatch):
    monkeypatch.setenv("BUS_DATABASE_URL", "bus-name")
    monkeypatch.setenv("DATABASE_URL", "bare-name")
    assert my_inbox._bus_dsn() == "bus-name"


def test_bus_dsn_falls_back_to_the_bare_name_for_orchestrator_side_invocation(monkeypatch):
    monkeypatch.delenv("BUS_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "bare-name")
    assert my_inbox._bus_dsn() == "bare-name"


def test_bus_dsn_is_none_when_neither_name_is_present(monkeypatch):
    monkeypatch.delenv("BUS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert my_inbox._bus_dsn() is None
