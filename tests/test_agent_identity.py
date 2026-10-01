"""scripts/lib/agent_identity.py — the shared fail-closed "who am I" resolver
(bus #47221, extracted from bus_send.py's resolve_from_agent so asks_triage.py
/ asks_open.py can reuse the same logic instead of re-deriving it). Full
behavioral coverage already lives in test_bus_send.py's identity tests, which
exercise this module indirectly via bus_send.resolve_from_agent (a direct
re-export) — this file just pins the canonical import path."""
import importlib

import pytest

ai = importlib.import_module("scripts.lib.agent_identity")


def test_resolve_agent_id_prefers_cc_base_agent_id():
    env = {"CC_BASE_AGENT_ID": "cc-oeh", "AGENT_ID": "should-not-win"}
    assert ai.resolve_agent_id(env) == "cc-oeh"


def test_resolve_agent_id_refuses_rather_than_guesses():
    with pytest.raises(ai.IdentityError):
        ai.resolve_agent_id({})


def test_bus_send_reexports_the_same_function():
    bs = importlib.import_module("scripts.bus_send")
    assert bs.resolve_from_agent is ai.resolve_agent_id
    assert bs.IdentityError is ai.IdentityError
