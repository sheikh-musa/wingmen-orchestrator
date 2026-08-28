"""Tests for singleton_liveness — process/tmux death detection for PROTECTED singletons.

TDD for the gap Nazim caught (35131/35135): cai was killed-not-relaunched and sat DEAD for
~30h because (a) no unread → wedge never fired, and (b) protected_agents were exempt from
death-detection. These pin the SAFETY-CRITICAL behavior: a recycle (tmux kept) is NOT dead,
a kill (no tmux) past a generous grace IS dead, and 'protected' does not mean 'unchecked'.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nervous_system import singleton_liveness as sl  # noqa: E402


# ---- classify_dead(): the core alive/grace/dead decision -----------------------
def test_tmux_present_is_alive_regardless_of_hb():
    # A live body (tmux session exists) — even with an oldish hb reading (long inference,
    # boot lag) — is ALIVE. tmux presence is the ground truth.
    assert sl.classify_dead(tmux_present=True, hb_age_s=99999, threshold_s=1200) == "alive"

def test_no_tmux_but_fresh_hb_is_grace_not_dead():
    # No tmux yet but heartbeat is recent → mid-transition (booting / brief tmux blip).
    # Generous grace (Nazim #5): don't page a body that was alive moments ago.
    assert sl.classify_dead(tmux_present=False, hb_age_s=60, threshold_s=1200) == "grace"

def test_no_tmux_and_stale_hb_is_dead():
    # No tmux AND heartbeat stale past the threshold → genuinely DEAD (the cai-30h case).
    assert sl.classify_dead(tmux_present=False, hb_age_s=1800, threshold_s=1200) == "dead"

def test_dead_boundary_at_threshold():
    assert sl.classify_dead(tmux_present=False, hb_age_s=1200, threshold_s=1200) == "dead"
    assert sl.classify_dead(tmux_present=False, hb_age_s=1199, threshold_s=1200) == "grace"

def test_recycle_in_place_keeps_tmux_so_never_false_dead():
    # An in-place /clear recycle preserves the tmux session (verified on 2 Nazim recycles),
    # and its hb may briefly lag during boot — must read ALIVE, never dead.
    assert sl.classify_dead(tmux_present=True, hb_age_s=300, threshold_s=1200) == "alive"


# ---- checked-set: protected singletons, same-host, NOT self, NOT cross-host ----
def test_checked_agents_excludes_self_and_crosshost_hub():
    # Given the protected set, the monitor checks same-host non-self singletons only.
    # self (cc-fleet-health) can't check its own liveness if dead; the hub is cross-host (VPS).
    protected = ["cai", "cc-orchestrator", "nazim-console", "orch-console", "cc-fleet-health"]
    checked = sl.checked_agents(protected,
                                sessions={"cai": "cai", "orch-console": "nazim"},
                                self_agent="cc-fleet-health")
    assert "cai" in checked and "orch-console" in checked
    assert "cc-fleet-health" not in checked, "never death-check SELF"
    assert "cc-orchestrator" not in checked, "hub is cross-host — no session mapping, skip"

def test_checked_agents_maps_to_tmux_session():
    checked = sl.checked_agents(["cai"], sessions={"cai": "cai"}, self_agent="cc-fleet-health")
    assert checked == {"cai": "cai"}
