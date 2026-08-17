"""Tests for nervous_system.lane_wedge_watchdog — the fleet idle/stopped-draining
wedge detector + staged-arm recovery.

The four load-bearing cases from the spec:
  (a) the cai incident (unread piling + quiet + ghost/empty composer) -> DETECTED,
  (b) a healthy-idle agent (empty inbox) -> NOT flagged,
  (c) a real non-dim staged composer -> alert-only, NEVER auto-nudged,
  (d) the recovery ACTION is lease-gated: a known non-holder fails CLOSED.

Every side-effecting seam (do_nudge / reset_lane / _page) is monkeypatched so
nothing here touches a live agent or pages the operator; the DB (Signal A) and
tmux (Signal B) are bypassed entirely by injecting AgentObs into run().
"""
from __future__ import annotations

import json

import pytest

from nervous_system import lane_wedge_watchdog as w


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _obs(agent="cai", kind="singleton", session=None, unread=3, oldest=1800.0,
         last_write=6000.0, comp=w.COMP_DELEGATED, text="", reachable=True,
         actionable=0, working=False, wake_eligible=None):
    # Default is a GENUINE stall: quiet 100m (> ALERT_QUIET_SEC) so the canonical
    # test wedge pages. Cycling/actionable/working cases pass those explicitly.
    # wake_eligible defaults to `actionable` unless set (used by the hub-narrow gate).
    if wake_eligible is None:
        wake_eligible = actionable
    return w.AgentObs(agent, kind, session,
                      w.BusSignal(unread, oldest, last_write, actionable, wake_eligible),
                      w.ComposerSignal(comp, text, working=working), reachable=reachable)


@pytest.fixture
def fast_floor(monkeypatch):
    """A single stable scan crosses the wedge floor — so run() with one injected
    obs is enough to reach a WEDGE verdict."""
    monkeypatch.setattr(w, "WEDGE_MIN_POLLS", 1)
    monkeypatch.setattr(w, "WEDGE_GRACE_SEC", 0)


@pytest.fixture
def recorder(monkeypatch, tmp_path):
    """Record every recovery action + page; isolate all side-effect files."""
    calls = {"nudge": [], "reset": [], "page": []}
    monkeypatch.setattr(w, "do_nudge", lambda o: (calls["nudge"].append(o.agent), (True, "stub"))[1])
    monkeypatch.setattr(w, "reset_lane", lambda s: (calls["reset"].append(s), (True, "stub"))[1])
    monkeypatch.setattr(w, "_page", lambda t: calls["page"].append(t))
    monkeypatch.setattr(w, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(w, "LOG_FILE", tmp_path / "log")
    monkeypatch.setattr(w, "HEARTBEAT_FILE", tmp_path / "hb")
    # A clean worktree so lane escalation would proceed if it ever got that far.
    monkeypatch.setattr(w, "_worktree_clean", lambda s, d: (True, "clean (stub)"))
    return calls


# --------------------------------------------------------------------------- #
# (a) the cai incident -> DETECTED
# --------------------------------------------------------------------------- #

def test_cai_incident_is_detected_as_wedge():
    """Unread piling + no self-write + delegated/empty composer, persisted past the
    floor, classifies as a (safe, nudge-eligible) WEDGE."""
    entry = None
    obs = _obs()  # cai, 3 unread @30m, silent 30m
    t = 1000.0
    v, entry = w.evaluate(entry, obs, t)
    assert v == w.V_MONITORING and entry["poll_count"] == 1
    v, entry = w.evaluate(entry, obs, t + 60)
    v, entry = w.evaluate(entry, obs, t + 130)
    v, entry = w.evaluate(entry, obs, t + 320)  # >= 4 polls, > 300s
    assert v == w.V_WEDGE


def test_detect_only_run_pages_cai_but_takes_no_action(fast_floor, recorder):
    w.run(mode=w.MODE_DETECT, alert=True, injected=[_obs()], lane_dirs={}, persist=False)
    assert recorder["nudge"] == []          # detect-only never acts
    assert recorder["reset"] == []
    assert len(recorder["page"]) == 1       # but it DID page the operator once


def test_auto_nudge_run_nudges_a_safe_wedge(fast_floor, recorder):
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[_obs()], lane_dirs={}, persist=False)
    assert recorder["nudge"] == ["cai"]     # armed -> the sanctioned nudge fires
    assert recorder["reset"] == []          # nudge mode stops before escalation


# --------------------------------------------------------------------------- #
# (b) healthy-idle -> NOT flagged
# --------------------------------------------------------------------------- #

def test_empty_inbox_is_never_a_wedge():
    v, entry = w.evaluate(None, _obs(unread=0, oldest=0, last_write=1e9), 1000.0)
    assert v == w.V_HEALTHY and "poll_count" not in entry


def test_agent_still_writing_is_not_a_wedge():
    # unread is piling, but the agent wrote to the bus 1 min ago -> busy, not wedged.
    v, _ = w.evaluate(None, _obs(unread=9, oldest=3600, last_write=60), 1000.0)
    assert v == w.V_HEALTHY


def test_stale_backlog_below_min_age_is_not_a_candidate():
    # unread exists but nothing older than MIN_AGE (oldest 5 min < 20 min floor).
    v, _ = w.evaluate(None, _obs(unread=4, oldest=300, last_write=99999), 1000.0)
    assert v == w.V_HEALTHY


def test_healthy_run_pages_nothing_and_acts_on_nothing(fast_floor, recorder):
    w.run(mode=w.MODE_ESCALATE, alert=True,
          injected=[_obs(unread=0, oldest=0, last_write=1e9)], lane_dirs={}, persist=False)
    assert recorder["nudge"] == [] and recorder["reset"] == [] and recorder["page"] == []


def test_capped_lane_is_suppressed_not_paged_or_nudged(fast_floor, recorder):
    """A pool-EXHAUSTED lane reads idle + bus-silent + not-draining — identical to a genuine
    stall that would PAGE — but its pane shows the weekly-limit banner, so it is benignly
    WAITING for reset, not wedged. It must NOT page or nudge; a reset/nudge can't help until
    the pool resets (Nazim #25930). Self-clears when the banner is gone."""
    stall_bus = w.BusSignal(9, 3600, 100000, 9, 9)   # unread piling, old, long-silent = would page
    capped = w.AgentObs("cc-irsyad", "lane", "irsyad", stall_bus,
                        w.ComposerSignal(w.COMP_EMPTY, capped=True))
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[capped], lane_dirs={}, persist=False)
    assert recorder["page"] == [], f"a capped lane must NOT page: {recorder['page']}"
    assert recorder["nudge"] == [], f"a capped lane must NOT be nudged: {recorder['nudge']}"


def test_same_stall_without_cap_still_pages(fast_floor, recorder):
    """Specificity: the SAME idle+silent stall, but NOT capped, still pages — proving the
    suppression is exactly the capped flag, not the test setup."""
    stall_bus = w.BusSignal(9, 3600, 100000, 9, 9)
    obs = w.AgentObs("cc-irsyad", "lane", "irsyad", stall_bus, w.ComposerSignal(w.COMP_EMPTY))
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[obs], lane_dirs={}, persist=False)
    assert recorder["page"], "a genuine (non-capped) stall must still page"


# --------------------------------------------------------------------------- #
# (c) real staged draft -> alert-only, never nudged
# --------------------------------------------------------------------------- #

def test_real_composer_classifies_wedge_unsafe():
    obs = _obs(agent="cc-irsyad", kind="lane", session="irsyad",
               comp=w.COMP_REAL, text="apply the money migration now")
    v = None
    e = None
    for dt in (0, 60, 130, 320):
        v, e = w.evaluate(e, obs, 1000.0 + dt)
    assert v == w.V_WEDGE_UNSAFE


def test_real_composer_is_alerted_but_never_nudged_even_when_armed(fast_floor, recorder):
    obs = _obs(agent="cc-irsyad", kind="lane", session="irsyad",
               comp=w.COMP_REAL, text="apply the money migration now")
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[obs], lane_dirs={}, persist=False)
    assert recorder["nudge"] == []          # a real draft is NEVER clobbered
    assert recorder["reset"] == []
    assert len(recorder["page"]) == 1       # it is surfaced instead


def test_safe_to_nudge_predicate():
    assert w.ComposerSignal(w.COMP_EMPTY).safe_to_nudge is True
    assert w.ComposerSignal(w.COMP_DELEGATED).safe_to_nudge is True
    assert w.ComposerSignal(w.COMP_UNREADABLE).safe_to_nudge is True
    assert w.ComposerSignal(w.COMP_REAL, "x").safe_to_nudge is False


# --------------------------------------------------------------------------- #
# (d) recovery ACTION is lease-gated (fail-closed for a non-holder)
# --------------------------------------------------------------------------- #

def test_recovery_deferred_when_lease_held_by_another_body(fast_floor, recorder, monkeypatch):
    # A different live body holds fleet_health_lease -> gate() refuses.
    monkeypatch.setattr(w.fleet_health_lease, "gate",
                        lambda: (False, "held by cc-orchestrator@studio (fresh) — pen deferred"))
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[_obs()], lane_dirs={}, persist=False)
    assert recorder["nudge"] == []          # FAIL-CLOSED: no action under a foreign lease
    assert recorder["reset"] == []
    # detection + the safety page are UNGATED, so the operator is still told.
    assert len(recorder["page"]) == 1


def test_recovery_fires_when_lease_is_held(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[_obs()], lane_dirs={}, persist=False)
    assert recorder["nudge"] == ["cai"]     # holder -> the action runs


# --------------------------------------------------------------------------- #
# escalation ladder + circuit breaker
# --------------------------------------------------------------------------- #

def test_singleton_is_paged_never_reset_on_escalation(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    monkeypatch.setattr(w, "STAGE2_DELAY_SEC", 0)
    monkeypatch.setattr(w, "MIN_NUDGES_BEFORE_ESCALATE", 1)
    obs = _obs()  # cai singleton
    # scan 1: nudge. scan 2: past the (0s) delay with a prior nudge -> escalate.
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[obs], lane_dirs={}, persist=True)
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[obs], lane_dirs={}, persist=True)
    assert recorder["nudge"] == ["cai"]     # nudged once
    assert recorder["reset"] == []          # a singleton is NEVER auto-reset
    assert any("wedged" in p.lower() for p in recorder["page"])  # paged instead


def test_lane_escalation_reaches_guarded_reset(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    monkeypatch.setattr(w, "STAGE2_DELAY_SEC", 0)
    obs = _obs(agent="cc-irsyad", kind="lane", session="irsyad", comp=w.COMP_EMPTY)
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[obs],
          lane_dirs={"irsyad": "/x"}, persist=True)
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[obs],
          lane_dirs={"irsyad": "/x"}, persist=True)
    assert recorder["nudge"] == ["cc-irsyad"]
    assert recorder["reset"] == ["irsyad"]  # a clean lane DOES get the guarded reset


def test_dirty_worktree_blocks_reset_and_alerts(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    monkeypatch.setattr(w, "STAGE2_DELAY_SEC", 0)
    monkeypatch.setattr(w, "_worktree_clean", lambda s, d: (False, "3 uncommitted change(s)"))
    obs = _obs(agent="cc-irsyad", kind="lane", session="irsyad", comp=w.COMP_EMPTY)
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[obs], lane_dirs={"irsyad": "/x"}, persist=True)
    w.run(mode=w.MODE_ESCALATE, alert=True, injected=[obs], lane_dirs={"irsyad": "/x"}, persist=True)
    assert recorder["reset"] == []          # never reset over uncommitted work
    assert any("not clean" in p.lower() or "worktree" in p.lower() for p in recorder["page"])


def test_repeat_wedge_stops_acting_and_pages(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    monkeypatch.setattr(w, "REPEAT_K", 2)
    import time as _t
    now = _t.time()
    # Pre-seed a wedge history at/above REPEAT_K within the window.
    w.STATE_FILE.write_text(json.dumps({
        "agents": {}, "wedge_history": {"cai": [now - 10, now - 5]}, "deadman": {"last_beat": now}}))
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[_obs()], lane_dirs={}, persist=True)
    assert recorder["nudge"] == []          # circuit broken -> stop auto-acting
    assert any("re-wedg" in p.lower() for p in recorder["page"])


# --------------------------------------------------------------------------- #
# bus-signal predicates + mode resolution
# --------------------------------------------------------------------------- #

def test_bus_signal_predicates(monkeypatch):
    monkeypatch.setattr(w, "UNREAD_MIN_AGE_SEC", 1200)
    monkeypatch.setattr(w, "QUIET_SEC", 1200)
    assert w.BusSignal(3, 1800, 1800).piling is True
    assert w.BusSignal(3, 1800, 1800).quiet is True
    assert w.BusSignal(0, 0, 9999).piling is False      # no unread
    assert w.BusSignal(3, 300, 9999).piling is False     # too fresh (< MIN_AGE)
    assert w.BusSignal(3, 1800, 60).quiet is False       # wrote recently


def test_resolve_mode(monkeypatch):
    monkeypatch.delenv("LANE_WEDGE_ARM", raising=False)
    assert w.resolve_mode(None) == w.MODE_DETECT
    assert w.resolve_mode("nudge") == w.MODE_NUDGE
    assert w.resolve_mode("escalate") == w.MODE_ESCALATE
    monkeypatch.setenv("LANE_WEDGE_ARM", "nudge")
    assert w.resolve_mode(None) == w.MODE_NUDGE
    monkeypatch.setenv("LANE_WEDGE_ARM", "escalate")
    assert w.resolve_mode(None) == w.MODE_ESCALATE


def test_self_test_passes():
    assert w.self_test() == 0


# --------------------------------------------------------------------------- #
# op#8807 stage-2: alerts are wired to the BUS (attributable), not nazim_send.sh
# --------------------------------------------------------------------------- #

class _FakeCur:
    def __init__(self, sink): self._sink = sink
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self._sink.append((sql, params))


class _FakeConn:
    def __init__(self, sink): self._sink = sink; self.committed = False; self.closed = False
    def cursor(self): return _FakeCur(self._sink)
    def commit(self): self.committed = True
    def close(self): self.closed = True


def test_emit_bus_alert_writes_attributable_row_to_orch_console(monkeypatch):
    """The alert path emits an agent_messages row FROM cc-fleet-health TO
    orch-console (the SRE's sanctioned channel) — carrying the alert text, and
    requires_response=false so it never re-triggers the SLA false-stall flood."""
    sink = []
    conn = _FakeConn(sink)
    monkeypatch.setattr(w, "_pg_connect", lambda: (lambda dsn, **kw: conn))
    monkeypatch.setattr(w, "_dsn", lambda: "postgres://fake")

    w._emit_bus_alert("⚠️ lane 'irsyad' looks wedged — idle but not draining its inbox\nmore detail")

    assert conn.committed and conn.closed
    # the INSERT is the last statement (first sets the identity for the trigger)
    insert_sql, params = sink[-1]
    assert "INSERT INTO agent_messages" in insert_sql
    assert "'cc-fleet-health'" in insert_sql and "'orch-console'" in insert_sql
    assert " false, now()" in insert_sql          # requires_response=false, responded stamped
    subject, body = params
    assert subject.startswith("[wedge-watchdog] lane 'irsyad'")   # icon stripped, titled
    assert "looks wedged" in body                 # full alert text preserved
    # identity is set in the SAME transaction (agents table has no trigger)
    assert any("set_config('app.current_agent_id','cc-fleet-health'" in s.replace(" ", "")
               for s, _ in sink)


def test_page_off_hostpath_uses_bus_not_nazim_send(monkeypatch):
    """Outside tests, _page routes through the bus emit — never nazim_send.sh."""
    monkeypatch.delenv("LANE_WEDGE_ALERT_STDOUT", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # simulate the daemon path
    monkeypatch.setattr(w, "LOG_FILE", __import__("pathlib").Path("/tmp/_wedge_test_log"))
    emitted = []
    monkeypatch.setattr(w, "_emit_bus_alert", lambda t: emitted.append(t))
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("nazim_send.sh must NOT be called"))
    monkeypatch.setattr(w.subprocess, "run", boom)
    w._page("⚠️ test wedge alert")
    assert emitted == ["⚠️ test wedge alert"]


# --------------------------------------------------------------------------- #
# Nazim 14067/14103/14470: a pane in a LIVE turn ('esc to interrupt') is WORKING,
# not wedged — suppress. This killed the dominant false-positive class.
# --------------------------------------------------------------------------- #

def _obs_working(agent="cc-irsyad", kind="lane", session="irsyad"):
    """A would-be wedge (unread piling + quiet) whose pane shows a live turn."""
    return w.AgentObs(agent, kind, session,
                      w.BusSignal(3, 1800.0, 1800.0),
                      w.ComposerSignal(w.COMP_EMPTY, "", working=True))


def test_working_pane_is_not_a_candidate():
    assert w._candidate(_obs_working()) is False
    # same bus signature but idle-at-prompt IS a candidate
    idle = w.AgentObs("cc-irsyad", "lane", "irsyad",
                      w.BusSignal(3, 1800.0, 1800.0), w.ComposerSignal(w.COMP_EMPTY))
    assert w._candidate(idle) is True


def test_working_lane_evaluates_healthy_not_wedge(fast_floor):
    v, entry = w.evaluate(None, _obs_working(), 1000.0)
    assert v == w.V_HEALTHY
    # episode is not opened for a working pane
    assert "poll_count" not in entry


def test_working_singleton_is_not_wedged(fast_floor):
    obs = w.AgentObs("cai", "singleton", None,
                     w.BusSignal(3, 1800.0, 1800.0),
                     w.ComposerSignal(w.COMP_DELEGATED, "", working=True))
    v, _ = w.evaluate(None, obs, 1000.0)
    assert v == w.V_HEALTHY


def test_working_run_takes_no_action_and_does_not_page(fast_floor, recorder):
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[_obs_working()], lane_dirs={}, persist=False)
    assert recorder["nudge"] == [] and recorder["reset"] == [] and recorder["page"] == []


# --------------------------------------------------------------------------- #
# Nazim 14413/13969/14033: a lane cycling between short tasks (wrote recently,
# only FYI unread) is nudged but NOT paged, and never trips the repeat breaker.
# The real-stall catch (fully-quiet, or an actionable req=True unread) is kept.
# --------------------------------------------------------------------------- #

def test_genuine_stall_helper():
    cyc = _obs(kind="lane", session="x", last_write=1800.0, actionable=0)   # 30m, FYI
    assert w._genuine_stall(cyc) is False
    quiet = _obs(kind="lane", session="x", last_write=6000.0, actionable=0)  # 100m
    assert w._genuine_stall(quiet) is True
    act = _obs(kind="lane", session="x", last_write=1800.0, actionable=1)    # req=True
    assert w._genuine_stall(act) is True


# --------------------------------------------------------------------------- #
# Nazim #17117 / the 315.7k-token lesson: the HUB (cc-orchestrator) is cross-host,
# composer-UNREADABLE and operator-attended, so quiet-alone cannot tell idle-attended
# from stuck. It pages ONLY on WAKE-ELIGIBLE unread (CAI-451 narrow floor P0/P1+rr)
# it is failing to drain — never on quiet + benign FYIs. cai/lanes are unchanged.
# --------------------------------------------------------------------------- #

def test_hub_idle_with_benign_unread_is_not_a_genuine_stall():
    hub = _obs(agent="cc-orchestrator", kind="singleton", last_write=6000.0,
               actionable=0, wake_eligible=0)   # quiet 100m, only P2/non-rr FYIs
    assert w._genuine_stall(hub) is False        # was a FALSE page (#17116)


def test_hub_with_wake_eligible_unread_is_a_genuine_stall():
    hub = _obs(agent="cc-orchestrator", kind="singleton", last_write=6000.0,
               actionable=1, wake_eligible=1)   # a P0/P1+rr row it is not draining
    assert w._genuine_stall(hub) is True


def test_hub_actionable_but_below_narrow_floor_does_not_page():
    # a P2 requires_response row: actionable=1 but wake_eligible=0 (below CAI-451 floor)
    hub = _obs(agent="cc-orchestrator", kind="singleton", last_write=6000.0,
               actionable=1, wake_eligible=0)
    assert w._genuine_stall(hub) is False


def test_cycling_wedge_is_nudged_but_not_paged(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    obs = _obs(agent="cc-irsyad", kind="lane", session="irsyad",
               last_write=1800.0, actionable=0)   # cycling: wrote 30m ago, FYI unread
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[obs], lane_dirs={}, persist=False)
    assert recorder["nudge"] == ["cc-irsyad"]   # still auto-recovered (cheap)
    assert recorder["page"] == []               # but NOT paged — benign cycling


def test_fully_quiet_wedge_is_paged(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    obs = _obs(agent="cc-irsyad", kind="lane", session="irsyad",
               last_write=6000.0, actionable=0)   # fully quiet 100m
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[obs], lane_dirs={}, persist=False)
    assert len(recorder["page"]) == 1


def test_actionable_unread_pages_even_when_recently_active(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    obs = _obs(agent="cai", kind="singleton", session=None,
               last_write=1800.0, actionable=1)   # the 2026-07-29 money-grant class
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[obs], lane_dirs={}, persist=False)
    assert len(recorder["page"]) == 1


def test_cycling_does_not_accumulate_repeat_breaker(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    # Ten cycling episodes must never trip the repeat-wedge breaker (it would if any
    # counted toward history). Distinct signatures force a fresh episode each time.
    for i in range(10):
        obs = _obs(agent="cc-irsyad", kind="lane", session="irsyad",
                   unread=1 + (i % 3), last_write=1800.0, actionable=0)
        w.run(mode=w.MODE_NUDGE, alert=True, injected=[obs], lane_dirs={}, persist=True)
    assert not any("re-wedg" in p.lower() for p in recorder["page"])


# --------------------------------------------------------------------------- #
# Nazim 14937/14938: an AskUserQuestion MENU-TRAP is genuinely stuck (blocks the
# bus) — distinct from idle and from working. It must ALWAYS surface, and is never
# auto-nudged (a nudge types INTO the menu). This is the ~1-day cc-ihsanos miss.
# --------------------------------------------------------------------------- #

def test_menu_state_is_not_safe_to_nudge():
    assert w.ComposerSignal(w.COMP_MENU).safe_to_nudge is False


def test_menu_trap_classifies_wedge_unsafe(fast_floor):
    obs = _obs(agent="cc-ihsanos", kind="lane", session="ihsanos", comp=w.COMP_MENU)
    v = e = None
    for dt in (0, 60, 130, 320):
        v, e = w.evaluate(e, obs, 1000.0 + dt)
    assert v == w.V_WEDGE_UNSAFE   # menu -> not-safe-to-nudge -> unsafe verdict


def test_menu_trap_pages_distinctly_and_never_nudges(fast_floor, recorder, monkeypatch):
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    obs = _obs(agent="cc-ihsanos", kind="lane", session="ihsanos", comp=w.COMP_MENU)
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[obs], lane_dirs={}, persist=False)
    assert recorder["nudge"] == []          # NEVER typed into a menu
    assert recorder["reset"] == []
    assert len(recorder["page"]) == 1
    assert any(("menu" in p.lower() or "trapped" in p.lower()) for p in recorder["page"])


def test_menu_trap_pages_even_when_not_a_genuine_stall(fast_floor, recorder, monkeypatch):
    # Recent bus write + FYI-only unread would suppress a plain wedge — but a menu
    # is definitively stuck, so it surfaces regardless.
    monkeypatch.setattr(w.fleet_health_lease, "gate", lambda: (True, "holder-current"))
    obs = _obs(agent="cc-ihsanos", kind="lane", session="ihsanos",
               comp=w.COMP_MENU, last_write=1800.0, actionable=0)
    w.run(mode=w.MODE_NUDGE, alert=True, injected=[obs], lane_dirs={}, persist=False)
    assert len(recorder["page"]) == 1
