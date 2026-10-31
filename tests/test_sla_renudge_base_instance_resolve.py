"""renudge() must resolve an INSTANCE-addressed agent to its BASE lane.

fleet_lanes keys by base_agent_id (cc-substrate -> substrate-cleanup), but a
violation row can be addressed to a live numbered instance (cc-substrate-1).
An exact-only lookup missed, fell through to ("unreachable", False), and every
re-nudge of an instance lane silently became a console escalation instead of a
lane nudge — re-creating the bottleneck the SRE exists to remove.

Exact match must still win (no regression for base-addressed rows).
"""
from scripts import priority_sla_watchdog as s


LANES = {"cc-substrate": "substrate-cleanup", "cc-coffeemedia": "coffeemedia"}


def test_instance_agent_resolves_to_its_base_lane():
    mech, ok = s.renudge("cc-substrate-1", 2, LANES, dry=True)
    assert ok is True
    assert mech == "lane_nudge.sh:substrate-cleanup"


def test_exact_base_agent_still_resolves_directly():
    # No regression: a row addressed to the base id behaves exactly as before.
    mech, ok = s.renudge("cc-substrate", 1, LANES, dry=True)
    assert ok is True
    assert mech == "lane_nudge.sh:substrate-cleanup"


def test_exact_key_wins_over_base_strip():
    # If BOTH the instance and the base are real lanes, the exact entry wins.
    lanes = {"cc-coffeemedia": "coffeemedia", "cc-coffeemedia-1": "coffeemedia-new"}
    mech, ok = s.renudge("cc-coffeemedia-1", 1, lanes, dry=True)
    assert ok is True
    assert mech == "lane_nudge.sh:coffeemedia-new"


def test_unknown_agent_is_still_unreachable():
    # Fail-loud preserved: no exact key and no base key -> unreachable, never a guess.
    mech, ok = s.renudge("cto-desktop", 1, LANES, dry=True)
    assert ok is False
    assert mech == "unreachable"


def test_base_agent_id_strips_only_a_trailing_instance_number():
    assert s._base_agent_id("cc-substrate-1") == "cc-substrate"
    assert s._base_agent_id("cc-irsyad-6") == "cc-irsyad"
    # A base with no instance suffix is returned unchanged...
    assert s._base_agent_id("musa") == "musa"
    # ...and digits INSIDE the name are never stripped.
    assert s._base_agent_id("cc-substrate") == "cc-substrate"
