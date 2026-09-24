"""Tests for hub_reach — resolve the hub's reach/remedy from orch_lease.holder_host.

Root cause (Nazim 39292/39435, 2026-09-12): hub remedy text across watchdogs hardcoded
the DECOMMISSIONED wingmen-core host (91.107.235.77) while the live hub is on gzbai. A
responder pointed there hits a dead box (auto-recovery no-op). These pin the resolver:
holder_host drives (host, reach-path, remedy), NEVER a hardwired host; unknown -> safe.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.lib import hub_reach as hr  # noqa: E402

WINGMEN_CORE_IP = "91.107.235.77"
GZB_IP = "192.168.1.114"


def test_gzbai_is_known_but_honest_about_no_provisioned_reach():
    # op#42907/op#20655: the old gzb-vpn.sh/wingmen-core relay this remedy used to
    # describe is DEAD, and no replacement interactive reach to gzb was built. The
    # remedy must say so plainly, never describe the dead hop as if it still works,
    # and never fall back to naming the decommissioned wingmen-core host either.
    r = hr.hub_reach_for_holder("gzbai")
    assert r["known"] is True
    assert r["host"] == "gzbai"
    assert r["reach"] is None
    # naming wingmen-core as the dead relay's decommissioned former hop (context) is fine;
    # naming it as a live action target (its IP) is the regression this guards against.
    assert "decommissioned" in r["remedy"]
    assert WINGMEN_CORE_IP not in r["remedy"]
    assert "no automated interactive" in r["remedy"].lower()


def test_wingmen_core_resolves_to_direct_vps():
    r = hr.hub_reach_for_holder("wingmen-core")
    assert r["known"] is True
    assert r["host"] == "wingmen-core"
    assert WINGMEN_CORE_IP in r["remedy"]
    # a wingmen-core hub must NOT be described via the gzb LAN
    assert GZB_IP not in r["remedy"]


def test_unknown_holder_is_safe_and_names_no_host():
    r = hr.hub_reach_for_holder(None)
    assert r["known"] is False
    # never assert/name a stale host; tell the reader to resolve from orch_lease
    assert WINGMEN_CORE_IP not in r["remedy"]
    assert GZB_IP not in r["remedy"]
    assert "orch_lease" in r["remedy"]


def test_unrecognized_host_is_treated_as_unknown_not_a_guess():
    r = hr.hub_reach_for_holder("some-new-host-99")
    assert r["known"] is False
    assert WINGMEN_CORE_IP not in r["remedy"]
    assert GZB_IP not in r["remedy"]


def test_no_resolved_remedy_ever_hardcodes_the_dead_host_for_a_live_gzb_hub():
    # the exact regression: the gzb hub's remedy carrying the wingmen-core IP.
    for holder in ("gzbai", "gzb"):
        assert WINGMEN_CORE_IP not in hr.hub_reach_for_holder(holder)["remedy"]


def test_host_match_guard_for_a_reset_target():
    # reset_hub_remote.sh's safety guard uses this: is `target_host` the current holder?
    assert hr.is_reach_host_current("wingmen-core", holder_host="wingmen-core") is True
    assert hr.is_reach_host_current("wingmen-core", holder_host="gzbai") is False
    # unknown holder -> fail-closed (do NOT reset when we cannot confirm the target)
    assert hr.is_reach_host_current("wingmen-core", holder_host=None) is False
