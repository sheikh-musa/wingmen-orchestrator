"""The irsyad-autoscaler ships INERT (detect+log only). The decisions it LOGS have to be right
BEFORE anything is armed — the wet-prove (design §6) is only meaningful if the §5 interlocks
already behave. So this exercises the PURE `decide()` against synthetic observations, proving:
the MAX_LANES cap, the SPIN_THRESHOLD, the PROTECTED allow-list (coord / bare client agent /
poller / money-path / singleton never selected), the idle-proof (unprovable => never killed),
the demand-pending holdoff, and the FAIL-SAFE (unreadable signal => decide NOTHING).

No DB, no tmux — every external fact is injected, the same discipline as test_lane_winddown.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.irsyad_autoscaler import (  # noqa: E402
    MAX_LANES, SPIN_THRESHOLD, Decision, LaneObs, decide, is_auto_killable)


def worker(name, idle=True, base=None, poller=False, money=False, live=True):
    """A live distinct-identity irsyad worker lane, idle by default (kill-eligible)."""
    base = base or "cc-irsyad-worker"
    return LaneObs(lane=name, base_agent_id=base, agent_id=f"{base}-1", live=live,
                   idle=idle, owns_bot_channel=poller, money_path=money, idle_reason="test")


def _decide(depth, lanes, readable=True, source="absent", option2=0):
    return decide(depth, readable, source, option2, lanes)


# ── the allow-list (PROTECTED set expressed as auto-killable membership) ───────────────
def test_allowlist_only_admits_distinct_identity_workers():
    assert is_auto_killable("cc-irsyad-bankimport", False, False) is True
    # bare client agent, coord, a singleton, a non-irsyad lane: all protected by construction
    assert is_auto_killable("cc-irsyad", False, False) is False          # bare client agent
    assert is_auto_killable("cc-irsyad-coord", False, False) is False    # coord
    assert is_auto_killable("cc-finance", False, False) is False         # unrelated family
    assert is_auto_killable("cai", False, False) is False                # singleton
    # a worker that owns a client channel, or is money-path, drops OUT of the allow-list
    assert is_auto_killable("cc-irsyad-student", True, False) is False   # client-poller
    assert is_auto_killable("cc-irsyad-student", False, True) is False   # money-path


# ── spin: threshold + cap ──────────────────────────────────────────────────────────────
def test_spins_when_demand_meets_threshold_and_under_cap():
    d = _decide(depth=SPIN_THRESHOLD, lanes=[], source="coord_dispatch_queue")
    assert d.would_spin is True
    assert d.spin_target and "pool 0 -> 1" in d.spin_target
    assert d.ambiguous is False


def test_no_spin_below_threshold():
    d = _decide(depth=0, lanes=[])
    assert d.would_spin is False


def test_cap_blocks_spin_at_max_lanes():
    """demand is high, but the pool is already at MAX_LANES — never spin past the cap."""
    pool = [worker(f"irsyad-w{i}", idle=False) for i in range(MAX_LANES)]
    d = _decide(depth=99, lanes=pool, source="coord_dispatch_queue")
    assert d.pool_size == MAX_LANES
    assert d.would_spin is False
    assert f"< MAX_LANES={MAX_LANES}: False" in d.interlocks["max_lanes_cap"]


def test_only_live_auto_killable_lanes_count_toward_the_cap():
    """A protected lane (coord) and a dead lane must not inflate pool_size and block a spin."""
    lanes = [worker("coord", base="cc-irsyad-coord"),        # protected
             worker("dead", live=False),                     # not live
             worker("w1", idle=False)]                       # 1 real pool lane
    d = _decide(depth=5, lanes=lanes, source="coord_dispatch_queue")
    assert d.pool_size == 1
    assert d.would_spin is True   # 1 < MAX_LANES(2)


# ── kill: idle-proof, protected set, demand-pending holdoff ────────────────────────────
def test_idle_worker_is_a_would_kill_candidate_when_no_demand():
    d = _decide(depth=0, lanes=[worker("w1", idle=True)])
    assert [c["lane"] for c in d.would_kill] == ["w1"]


def test_unprovably_idle_worker_is_never_killed():
    """idle=None means the idle-proof could not be established — fail-safe, never a candidate."""
    d = _decide(depth=0, lanes=[worker("w1", idle=None)])
    assert d.would_kill == []


def test_busy_worker_is_never_killed():
    d = _decide(depth=0, lanes=[worker("w1", idle=False)])
    assert d.would_kill == []


def test_protected_lanes_never_enter_the_would_kill_set():
    lanes = [worker("coord", base="cc-irsyad-coord", idle=True),   # coord
             worker("client", base="cc-irsyad", idle=True),        # bare client agent
             worker("poller", idle=True, poller=True),             # client-poller
             worker("money", idle=True, money=True)]               # money-path
    d = _decide(depth=0, lanes=lanes)
    assert d.would_kill == []
    assert d.pool_size == 0


def test_demand_pending_holds_off_any_wind_down():
    """An idle worker is NOT proposed for kill while demand is pending — no shrink under load."""
    d = _decide(depth=SPIN_THRESHOLD, lanes=[worker("w1", idle=True)], source="coord_dispatch_queue")
    assert d.would_kill == []
    assert "no shrink while work waits" in d.interlocks["demand_pending_holdoff"]
    assert d.interlocks["idle_proof"] == "not evaluated — demand pending, holdoff active"


# ── fail-safe ───────────────────────────────────────────────────────────────────────────
def test_unreadable_signal_decides_nothing():
    """Genuine read error on an existing coord queue => decide NOTHING (no spin, no kill),
    even with idle workers present and demand nominally high."""
    d = decide(coord_queue_depth=None, coord_queue_readable=False,
               coord_queue_source="coord_dispatch_queue (read error: OperationalError)",
               option2_count=7, lanes=[worker("w1", idle=True)])
    assert d.ambiguous is True
    assert d.would_spin is False
    assert d.would_kill == []
    assert d.demand is None
    assert "TRIGGERED" in d.interlocks["failsafe"]


def test_missing_coord_table_is_depth_zero_not_ambiguous():
    """A MISSING coord queue is defensively depth 0 (coord has not built it) — readable, not
    a fail-safe. option2 is still logged for the wet-prove to compare."""
    d = _decide(depth=0, lanes=[], readable=True, source="absent", option2=3)
    assert d.ambiguous is False
    assert d.coord_queue_source == "absent"
    assert d.option2_count == 3
    assert d.would_spin is False


def test_both_demand_counts_are_carried_for_the_wet_prove():
    d = _decide(depth=2, lanes=[], source="coord_dispatch_queue", option2=5)
    assert d.coord_queue_depth == 2 and d.option2_count == 5


# ── INERT invariant + shape ──────────────────────────────────────────────────────────────
def test_decision_is_pure_logging_shape_only():
    """`decide` returns a Decision and has no actuation surface — it cannot spin or kill,
    only describe. (The INERT guarantee lives in the module: no lanes.sh/winddown actuator.)"""
    d = _decide(depth=1, lanes=[], source="coord_dispatch_queue")
    assert isinstance(d, Decision)
    for key in ("failsafe", "spin_threshold", "max_lanes_cap", "idle_proof",
                "protected_allowlist", "demand_pending_holdoff"):
        assert key in d.interlocks, key


def test_module_has_no_actuation_calls():
    """Hard INERT proof: the scaler source must never actuate — no lanes.sh spin/kill, no
    lane_winddown --kill, no tmux kill-session. (lane_winddown is imported READ-ONLY for its
    idle-proof predicate; that is may_wind_down, never a kill.)"""
    src = (_ROOT / "scripts" / "irsyad_autoscaler.py").read_text()
    for forbidden in ("lanes.sh up", "lanes.sh down", "kill-session", "--kill", "kill_session"):
        assert forbidden not in src, f"INERT violation: found {forbidden!r}"
