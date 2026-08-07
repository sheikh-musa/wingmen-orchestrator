"""Tests for the front_door module (Chief of Staff, Step 2 — single-door framing).

Step 2 makes nazim-console the canonical door and has the CoS relay cross-domain
work to the hub and the hub's answer back into ONE thread. This module holds the
SAFE FOUNDATION: the FRONT_DOOR_MODE flag (default per-channel = today's
behavior, zero change) plus the CAI-502-compliant relay/attribution primitives:
  - a CoS->hub relay is authority ONLY when the underlying inbound operator_message
    is verified (relayed 'operator said X' is not authority until verified);
  - a relayed hub/cai answer ALWAYS keeps its source attribution (+ decision_ref).
Nothing here touches the live ingest path — arming the relay is the next step.
"""

import pytest

from nervous_system import front_door


# --------------------------------------------------------------------------- #
# Mode flag — default is today's behavior
# --------------------------------------------------------------------------- #

def test_mode_defaults_to_per_channel(monkeypatch):
    monkeypatch.delenv("FRONT_DOOR_MODE", raising=False)
    assert front_door.mode() == front_door.MODE_PER_CHANNEL
    assert front_door.is_unified() is False


def test_mode_reads_env(monkeypatch):
    monkeypatch.setenv("FRONT_DOOR_MODE", "unified")
    assert front_door.mode() == front_door.MODE_UNIFIED
    assert front_door.is_unified() is True


def test_invalid_mode_falls_back_to_per_channel(monkeypatch):
    # An unrecognized value must NOT silently enable unified — safe default.
    monkeypatch.setenv("FRONT_DOOR_MODE", "banana")
    assert front_door.mode() == front_door.MODE_PER_CHANNEL
    assert front_door.is_unified() is False


# --------------------------------------------------------------------------- #
# CoS -> hub relay: authority requires a verified inbound (CAI-502)
# --------------------------------------------------------------------------- #

def test_verified_relay_is_authority():
    payload = front_door.build_relay_to_hub(
        operator_message_id=6142, text="ship the storefront ratings",
        domain="storefront", verified=True)
    assert payload["authority"] == "verified"
    assert payload["operator_message_id"] == 6142
    assert payload["text"] == "ship the storefront ratings"
    assert payload["domain"] == "storefront"
    assert payload["provenance"] == "relayed-by-cos"
    assert payload["kind"] == "cos-operator-relay"


def test_unverified_relay_is_context_only_not_authority():
    payload = front_door.build_relay_to_hub(
        operator_message_id=6142, text="ship it", domain="storefront", verified=False)
    assert payload["authority"] == "context-only"


def test_relay_without_durable_row_cannot_be_authority():
    # No underlying operator_message id -> can NEVER be authority, even if the
    # caller claims verified (CAI-502: authority rides the durable inbound row).
    payload = front_door.build_relay_to_hub(
        operator_message_id=None, text="ship it", domain="storefront", verified=True)
    assert payload["authority"] == "context-only"


# --------------------------------------------------------------------------- #
# Relay-back keeps source attribution (CAI-502)
# --------------------------------------------------------------------------- #

def test_relay_back_attributes_the_source():
    out = front_door.format_relay_back("Ratings are live.", source_agent="hub", domain="storefront")
    assert "hub" in out.lower()
    assert "Ratings are live." in out


def test_relay_back_includes_decision_ref_when_given():
    out = front_door.format_relay_back(
        "Flip is held pending the name scrub.", source_agent="cai",
        decision_ref="CAI-RESP-505")
    assert "CAI-RESP-505" in out
    assert "cai" in out.lower()


def test_relay_back_never_drops_the_answer_text():
    answer = "The migration applied to both silos; 22/22 deny-proofs pass."
    out = front_door.format_relay_back(answer, source_agent="hub")
    assert answer in out
