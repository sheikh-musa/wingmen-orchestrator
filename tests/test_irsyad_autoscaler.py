"""The irsyad-autoscaler ships INERT/SUPERVISED-propose (never actuates). The decisions it LOGS
have to be right before anything is armed — so this exercises the PURE `decide()` against
synthetic observations, proving: the MAX_LANES cap (counts live numbered cc-irsyad bodies), the
SPIN_THRESHOLD, the kill allow-list (only SPUN irsyad-worker-<N> lanes; coord / bare client /
standing tabung / poller / money-path never killed), the idle-proof (unprovable => never killed),
the demand-pending holdoff, and the FAIL-SAFE (unreadable signal => decide NOTHING).

Identity model (real, post-#40672): a SHARED base 'cc-irsyad' with a distinct numbered sub-tag
per body (agent_status.agent_id = cc-irsyad-<N>). Pool/cap membership keys on the sub-tag, NOT
base_agent_id. Kill-eligibility is the stricter subset: only spun 'irsyad-worker-<N>' sessions.

No DB, no tmux — every external fact is injected, the same discipline as test_lane_winddown.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.irsyad_autoscaler import (  # noqa: E402
    MAX_LANES, SPIN_THRESHOLD, Decision, LaneObs, decide, is_auto_killable,
    is_elastic_worker, is_pool_member, should_emit_proposal)


def worker(n, idle=True, poller=False, money=False, live=True):
    """A live SPUN elastic pool worker: shared base 'cc-irsyad', numbered sub-tag cc-irsyad-<n>,
    tmux session irsyad-worker-<n>. Kill-eligible when idle (unless poller/money)."""
    return LaneObs(lane=f"irsyad-worker-{n}", base_agent_id="cc-irsyad", agent_id=f"cc-irsyad-{n}",
                   live=live, idle=idle, owns_bot_channel=poller, money_path=money, idle_reason="test")


def standing(agent_id, session, idle=True, live=True):
    """A standing (non-elastic) cc-irsyad lane — e.g. tabung (cc-irsyad-1 / irsyad-tabung-jumaat):
    counts toward the CAP if numbered, but is NEVER a kill candidate (not an irsyad-worker session)."""
    return LaneObs(lane=session, base_agent_id="cc-irsyad", agent_id=agent_id, live=live,
                   idle=idle, owns_bot_channel=False, money_path=False, idle_reason="test")


def coord(idle=True, live=True):
    """The coordinator cc-irsyad-coord-<N> (session irsyad-coord): excluded from cap AND kill."""
    return LaneObs(lane="irsyad-coord", base_agent_id="cc-irsyad-coord", agent_id="cc-irsyad-coord-2",
                   live=live, idle=idle, owns_bot_channel=False, money_path=False, idle_reason="test")


def _decide(depth, lanes, readable=True, source="absent", option2=0):
    return decide(depth, readable, source, option2, lanes)


# ── membership: cap (numbered sub-tag) vs kill-eligibility (spun worker session) ────────
def test_pool_membership_keys_on_numbered_subtag():
    assert is_pool_member("cc-irsyad-1") is True          # tabung / worker
    assert is_pool_member("cc-irsyad-3") is True          # worker
    assert is_pool_member("cc-irsyad") is False           # bare client agent (not numbered)
    assert is_pool_member("cc-irsyad-coord-2") is False   # coord (not a bare number)
    assert is_pool_member("cc-finance-1") is False        # unrelated family


def test_cap_membership_is_spun_worker_session_only():
    # cap (Nazim #40850 ruling (a)) keys on the WORKER SESSION, excluding standing lanes
    assert is_elastic_worker("irsyad-worker-1") is True
    assert is_elastic_worker("irsyad-worker-12") is True
    assert is_elastic_worker("irsyad-tabung-jumaat") is False   # standing (tabung)
    assert is_elastic_worker("irsyad-coord") is False           # coord
    assert is_elastic_worker("") is False


def test_kill_allowlist_only_admits_spun_worker_sessions():
    # a spun elastic worker (numbered sub-tag + irsyad-worker-<N> session) is kill-eligible
    assert is_auto_killable("cc-irsyad-3", "irsyad-worker-3", False, False) is True
    # bare client, coord, unrelated family, singleton: protected
    assert is_auto_killable("cc-irsyad", "irsyad-worker-1", False, False) is False       # not numbered
    assert is_auto_killable("cc-irsyad-coord-2", "irsyad-coord", False, False) is False  # coord
    assert is_auto_killable("cc-finance-1", "finance", False, False) is False            # other family
    assert is_auto_killable("cai", "cai", False, False) is False                         # singleton
    # a standing numbered lane (tabung) counts toward the cap but is NEVER kill-eligible
    assert is_auto_killable("cc-irsyad-1", "irsyad-tabung-jumaat", False, False) is False
    # a worker that owns a channel / is money-path drops OUT of the kill allow-list
    assert is_auto_killable("cc-irsyad-3", "irsyad-worker-3", True, False) is False       # poller
    assert is_auto_killable("cc-irsyad-3", "irsyad-worker-3", False, True) is False       # money


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
    pool = [worker(i + 1, idle=False) for i in range(MAX_LANES)]
    d = _decide(depth=99, lanes=pool, source="coord_dispatch_queue")
    assert d.pool_size == MAX_LANES
    assert d.would_spin is False
    assert f"< MAX_LANES={MAX_LANES}: False" in d.interlocks["max_lanes_cap"]


def test_standing_tabung_excluded_from_cap(  # Nazim #40850 ruling (a)
):
    """A standing lane (tabung, cc-irsyad-1 / irsyad-tabung-jumaat) is NOT a spun elastic worker,
    so it is EXCLUDED from the cap: tabung + 1 worker => pool_size 1 (only the worker) => a 2nd
    elastic worker may still spin. Effective elastic capacity is a full MAX_LANES regardless of
    standing lanes. (Supersedes the #40672 sub-tag count where tabung wrongly ate a slot.)"""
    lanes = [standing("cc-irsyad-1", "irsyad-tabung-jumaat", idle=False),
             worker(3, idle=False)]
    d = _decide(depth=5, lanes=lanes, source="coord_dispatch_queue")
    assert d.pool_size == 1              # only the spun worker; tabung excluded
    assert d.would_spin is True          # 1 < MAX_LANES(2)


def test_only_live_numbered_lanes_count_toward_the_cap():
    """coord (not numbered) and a dead worker must not inflate pool_size and block a spin."""
    lanes = [coord(),                 # excluded (coord, not a bare number)
             worker(9, live=False),   # not live
             worker(1, idle=False)]   # 1 real live pool body
    d = _decide(depth=5, lanes=lanes, source="coord_dispatch_queue")
    assert d.pool_size == 1
    assert d.would_spin is True   # 1 < MAX_LANES(2)


# ── kill: idle-proof, protected set, demand-pending holdoff ────────────────────────────
def test_idle_worker_is_a_would_kill_candidate_when_no_demand():
    d = _decide(depth=0, lanes=[worker(1, idle=True)])
    assert [c["lane"] for c in d.would_kill] == ["irsyad-worker-1"]


def test_unprovably_idle_worker_is_never_killed():
    """idle=None means the idle-proof could not be established — fail-safe, never a candidate."""
    d = _decide(depth=0, lanes=[worker(1, idle=None)])
    assert d.would_kill == []


def test_busy_worker_is_never_killed():
    d = _decide(depth=0, lanes=[worker(1, idle=False)])
    assert d.would_kill == []


def test_standing_and_protected_lanes_never_enter_the_would_kill_set():
    lanes = [coord(idle=True),                                  # coord: excluded cap + kill
             standing("cc-irsyad-1", "irsyad-tabung-jumaat", idle=True),  # tabung: cap-only, never killed
             worker(3, idle=True, poller=True),                 # poller worker: cap, not killed
             worker(4, idle=True, money=True)]                  # money worker: cap, not killed
    d = _decide(depth=0, lanes=lanes)
    assert d.would_kill == []
    # cap counts ONLY spun elastic workers (the 2 irsyad-worker-<N>); tabung + coord excluded
    assert d.pool_size == 2


def test_demand_pending_holds_off_any_wind_down():
    """An idle worker is NOT proposed for kill while demand is pending — no shrink under load."""
    d = _decide(depth=SPIN_THRESHOLD, lanes=[worker(1, idle=True)], source="coord_dispatch_queue")
    assert d.would_kill == []
    assert "no shrink while work waits" in d.interlocks["demand_pending_holdoff"]
    assert d.interlocks["idle_proof"] == "not evaluated — demand pending, holdoff active"


# ── fail-safe ───────────────────────────────────────────────────────────────────────────
def test_unreadable_signal_decides_nothing():
    """Genuine read error on an existing coord queue => decide NOTHING (no spin, no kill),
    even with idle workers present and demand nominally high."""
    d = decide(coord_queue_depth=None, coord_queue_readable=False,
               coord_queue_source="coord_dispatch_queue (read error: OperationalError)",
               option2_count=7, lanes=[worker(1, idle=True)])
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
    """Hard proof: the scaler source must never actuate — no lanes.sh spin/kill, no
    lane_winddown --kill, no tmux kill-session. The SUPERVISED arm only POSTS a proposal bus row;
    it must never itself boot or kill a lane. (lane_winddown is imported READ-ONLY for its
    idle-proof predicate; that is may_wind_down, never a kill.)"""
    src = (_ROOT / "scripts" / "irsyad_autoscaler.py").read_text()
    for forbidden in ("lanes.sh up", "lanes.sh down", "kill-session", "--kill", "kill_session"):
        assert forbidden not in src, f"actuation violation: found {forbidden!r}"


# ── SUPERVISED-propose gate (Nazim #40401) — pure logic, no DB ─────────────────────────
def test_inert_mode_never_proposes():
    assert should_emit_proposal("inert", would_spin=True, ambiguous=False,
                                open_proposal_exists=False) is False


def test_supervised_proposes_on_real_would_spin():
    assert should_emit_proposal("supervised", would_spin=True, ambiguous=False,
                                open_proposal_exists=False) is True


def test_supervised_never_proposes_without_would_spin():
    assert should_emit_proposal("supervised", would_spin=False, ambiguous=False,
                                open_proposal_exists=False) is False


def test_supervised_never_proposes_when_ambiguous():
    assert should_emit_proposal("supervised", would_spin=True, ambiguous=True,
                                open_proposal_exists=False) is False


def test_supervised_dedups_when_proposal_already_open():
    assert should_emit_proposal("supervised", would_spin=True, ambiguous=False,
                                open_proposal_exists=True) is False
