"""Menu-parked detector state machine + the ALERT-ONLY tier-sweep (Nazim #40469).

The invariant under test: a menu-parked pane is detected on the menu signal ALONE (not the
wedge Signal-A gate), only after BOTH grace floors, and NO arm tier ever nudges it (a
watchdog answering a menu = an authorization slip)."""
import json
import os
import time

from nervous_system import lane_wedge_watchdog as W


# ── pure state machine ────────────────────────────────────────────────────────
def test_menu_transient_monitors_then_parks_then_clears():
    t = 1000.0
    e = None
    v, e = W.menu_evaluate(e, True, t);        assert v == W.V_MENU_MONITORING and e["menu_polls"] == 1
    v, e = W.menu_evaluate(e, True, t + 60);   assert v == W.V_MENU_MONITORING and e["menu_polls"] == 2
    v, e = W.menu_evaluate(e, True, t + 120);  assert v == W.V_MENU_MONITORING  # 3 polls but < grace secs
    v, e = W.menu_evaluate(e, True, t + 601);  assert v == W.V_MENU_PARKED       # >=3 polls AND >=600s
    v, e = W.menu_evaluate(e, False, t + 700); assert v == W.V_MENU_CLEAR and "menu_first_seen" not in e


def test_menu_polls_without_walltime_do_not_park():
    # A burst of fast scans must not fire early — the wall-clock floor is independent.
    t = 0.0
    e = None
    for i in range(6):
        v, e = W.menu_evaluate(e, True, t + i)   # 6 polls but only ~5s elapsed
    assert v == W.V_MENU_MONITORING


def test_menu_episode_resets_after_a_clear():
    t = 0.0
    e = None
    for dt in (0, 60, 601):
        v, e = W.menu_evaluate(e, True, t + dt)
    assert v == W.V_MENU_PARKED
    v, e = W.menu_evaluate(e, False, t + 650)     # menu answered/gone
    assert v == W.V_MENU_CLEAR
    v, e = W.menu_evaluate(e, True, t + 700)       # a NEW menu starts a fresh episode
    assert v == W.V_MENU_MONITORING and e["menu_polls"] == 1


# ── tier-sweep: no arm tier may nudge a menu pane ─────────────────────────────
def test_no_arm_tier_nudges_a_menu_pane(monkeypatch, tmp_path):
    """Drive a confirmed wedge whose composer is a MENU through every arm tier
    (detect / nudge / escalate) with the lease forced held, and assert do_nudge is
    NEVER called. This is the watchdog half of the alert-only invariant (the other
    half is the lane_nudge send-keys refusal in test_menu_parked_guard.py)."""
    os.environ["LANE_WEDGE_ALERT_STDOUT"] = "1"
    monkeypatch.setattr(W.fleet_health_lease, "gate", lambda: (True, "test-armed"))
    calls = []
    monkeypatch.setattr(W, "do_nudge", lambda o: (calls.append(o.agent), (True, "stub"))[1])

    def mk_menu():
        return W.AgentObs("cc-x", "lane", "x",
                          W.BusSignal(3, 1800.0, 1800.0, actionable=1),
                          W.ComposerSignal(W.COMP_MENU))

    for mode in (W.MODE_DETECT, W.MODE_NUDGE, W.MODE_ESCALATE):
        sf = tmp_path / f"s_{mode}.json"
        monkeypatch.setattr(W, "STATE_FILE", sf)
        # pre-seed the episode one poll below the stability floor so this scan confirms it
        sf.write_text(json.dumps({
            "agents": {"cc-x": {"sig": f"3|{W.COMP_MENU}", "first_seen": 0,
                                "poll_count": W.WEDGE_MIN_POLLS - 1, "last_seen": 0}},
            "wedge_history": {}, "deadman": {"last_beat": time.time()}}))
        W.run(mode=mode, alert=True, injected=[mk_menu()], lane_dirs={}, persist=False)

    assert calls == [], f"AUTHORIZATION SLIP: a menu pane was nudged at some tier: {calls}"
