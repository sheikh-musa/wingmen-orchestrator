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
