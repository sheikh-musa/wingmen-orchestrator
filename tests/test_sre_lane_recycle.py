"""Phase-1 fixes for the hands-off active-red worker auto-recycle (op#14539 / bus #27720).

Both defects were found in the MANUAL cc-irsyad recycle #27711 and are documented in
reports/active-red-worker-auto-recycle-design-20260818.md:

  FIX 1 — the boot string a freshly-cleared lane receives must name the ABSOLUTE handoff
          path. The old "Read your newest handoff in reports/" is relative to the lane's
          cwd (its project worktree), so it resolves to the WRONG tree and the lane reads a
          stale handoff. cc-irsyad burned ~4 min on an Aug-5 stale handoff before self-
          correcting off a durable bus note.

  FIX 2 — the fresh_handoff gate's reference epoch must be overridable. compute_gates always
          measures the handoff mtime against last_material_action_epoch (max of ALL the
          lane's bus messages, no type filter), so a "DONE" ack the lane posts AFTER writing
          its handoff makes the fresh handoff read as stale. The DRIVEN checkpoint path must
          be able to measure freshness against T_nudge (written-after-we-asked) instead.
"""
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import sre_lane_recycle as slr  # noqa: E402


# ── FIX 1 — absolute handoff path in the boot string ──────────────────────────
def test_newest_handoff_path_returns_absolute_path(tmp_path):
    """_newest_handoff_path returns the absolute path of the newest matching handoff,
    so the boot string can name an exact file rather than a cwd-relative glob."""
    old = tmp_path / "irsyad-handoff-20260813.md"
    old.write_text("stale\n")
    os.utime(old, (1_000, 1_000))
    new = tmp_path / "irsyad-handoff-NOW.md"
    new.write_text("fresh\n")
    os.utime(new, (2_000, 2_000))

    got = slr._newest_handoff_path("cc-irsyad", f"handoff_glob={tmp_path}/irsyad-handoff-*.md")

    assert got is not None
    assert os.path.isabs(got)
    assert got == str(new)


def test_newest_handoff_path_none_when_no_handoff(tmp_path):
    """No matching handoff -> None (so the caller fails closed, never invents a path)."""
    assert slr._newest_handoff_path("cc-nobody", f"handoff_glob={tmp_path}/none-*.md") is None


def test_build_boot_instruction_names_absolute_handoff_path():
    """The boot instruction must contain the ABSOLUTE handoff path verbatim and must not
    fall back to the bare relative 'newest handoff in reports/' phrasing."""
    abs_path = "/Users/sheikhmusa/wingmen/orchestrator/reports/irsyad-handoff-NOW.md"
    boot = slr.build_boot_instruction("cc-irsyad", abs_path)

    assert abs_path in boot
    assert "newest handoff in reports/" not in boot
    # still tells the fresh body to reconcile its own inbox
    assert "cc-irsyad" in boot


# ── FIX 2 — overridable fresh_handoff reference epoch ─────────────────────────
def _lane_row():
    return {"lane": "irsyad", "base_agent_id": "cc-irsyad",
            "tmux_session": "no-such-session-xyz", "worktree_path": None, "notes": None}


def test_compute_gates_uses_handoff_ref_epoch_override(monkeypatch):
    """When a handoff_ref_epoch is supplied, the fresh_handoff gate is measured against IT,
    not against the lane's last bus message — this is what lets the driven path ignore a
    post-handoff DONE ack. conn=None here proves the override path never needs the DB."""
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 1_000.0)

    fresh = slr.compute_gates(None, _lane_row(), handoff_ref_epoch=999.0)
    assert fresh["fresh_handoff"] is True

    stale = slr.compute_gates(None, _lane_row(), handoff_ref_epoch=1_001.0)
    assert stale["fresh_handoff"] is False


def test_compute_gates_default_reference_fails_closed_without_conn(monkeypatch):
    """Regression: with no override and no DB conn, last_material_action can't be established
    -> fresh_handoff fails CLOSED (unchanged autonomous-detector behaviour)."""
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 1_000.0)
    gates = slr.compute_gates(None, _lane_row())
    assert gates["fresh_handoff"] is False


def test_plan_threads_handoff_ref_epoch(monkeypatch):
    """plan() must pass an explicit handoff_ref_epoch down to compute_gates so the driven
    path's freshness proof reaches the gate."""
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 5_000.0)
    p = slr.plan(None, _lane_row(), armed=False, require_bloat=False, handoff_ref_epoch=4_000.0)
    assert p["gates"]["fresh_handoff"] is True


# ── Phase 3.5 — armed_recycle: recycle a SPECIFIC lane_row in-process ──────────
# The driver must recycle the EXACT session/worktree it discovered, not a re-discovered
# (ambiguous) one. armed_recycle is the shared armed action; it re-verifies the floor,
# audits, then runs reset_lane.sh.
def test_armed_recycle_not_permitted_does_not_reset(monkeypatch):
    monkeypatch.setattr(slr, "plan",
                        lambda conn, lr, armed=True, require_bloat=False, handoff_ref_epoch=None: {
                            "permitted": False, "reason": "gate refused", "gates": {"idle": False},
                            "base": "cc-irsyad", "session": "irsyad"})
    calls = []
    monkeypatch.setattr(slr.subprocess, "run", lambda *a, **k: calls.append(a))
    out = slr.armed_recycle(None, _lane_row(), reason="r")
    assert out["recycled"] is False
    assert calls == []                      # reset_lane.sh NEVER runs when not permitted


def test_armed_recycle_permitted_audits_resets_and_names_abs_handoff(monkeypatch):
    monkeypatch.setattr(slr, "plan",
                        lambda conn, lr, armed=True, require_bloat=False, handoff_ref_epoch=None: {
                            "permitted": True, "reason": "ok", "gates": {"idle": True, "git_clean": True},
                            "base": "cc-irsyad", "session": "irsyad-tabung-jumaat"})
    audit = []
    monkeypatch.setattr(slr, "audit_before_clear", lambda *a, **k: audit.append(a))
    monkeypatch.setattr(slr, "_newest_handoff_path", lambda base, notes: "/abs/reports/irsyad-handoff-NOW.md")
    monkeypatch.setattr(slr.hcp, "compact_if_enabled", lambda *a, **k: {"wrote": False, "changed": False})

    class _R:
        returncode = 0; stdout = "reset ok"; stderr = ""
    reset_calls = []
    monkeypatch.setattr(slr.subprocess, "run", lambda *a, **k: (reset_calls.append(a), _R())[1])

    out = slr.armed_recycle(None, _lane_row(), reason="r", handoff_ref_epoch=123.0)
    assert out["recycled"] is True
    assert len(audit) == 1 and len(reset_calls) == 1
    assert out["session"] == "irsyad-tabung-jumaat"
    assert "/abs/reports/irsyad-handoff-NOW.md" in out["boot"]      # absolute-path boot (Phase 1)
    # reset_lane.sh was invoked for the SESSION from the plan (the exact discovered lane)
    assert "irsyad-tabung-jumaat" in reset_calls[0][0]


# ── Item-3 (Nazim #31825): staged handoff compaction is wired into the armed action ───
def _permitted_plan(monkeypatch):
    monkeypatch.setattr(slr, "plan",
                        lambda conn, lr, armed=True, require_bloat=False, handoff_ref_epoch=None: {
                            "permitted": True, "reason": "ok", "gates": {"idle": True},
                            "base": "cc-irsyad", "session": "irsyad-tabung-jumaat"})
    monkeypatch.setattr(slr, "audit_before_clear", lambda *a, **k: None)
    monkeypatch.setattr(slr, "_newest_handoff_path", lambda base, notes: "/abs/reports/irsyad-handoff-NOW.md")


def test_armed_recycle_compacts_handoff_before_reset(monkeypatch):
    _permitted_plan(monkeypatch)
    order = []
    def _cmp(path, **k):
        order.append(("compact", path, k.get("agent"), k.get("session")))
        return {"wrote": True, "changed": True, "before_bytes": 120000, "after_bytes": 27000}
    monkeypatch.setattr(slr.hcp, "compact_if_enabled", _cmp)

    class _R:
        returncode = 0; stdout = "reset ok"; stderr = ""
    monkeypatch.setattr(slr.subprocess, "run",
                        lambda *a, **k: (order.append(("reset",)), _R())[1])

    out = slr.armed_recycle(None, _lane_row(), reason="r")
    assert out["recycled"] is True
    # compaction ran with the lane's agent/session/handoff, and BEFORE reset_lane.sh
    assert order[0] == ("compact", "/abs/reports/irsyad-handoff-NOW.md", "cc-irsyad", "irsyad-tabung-jumaat")
    assert order[1] == ("reset",)


def test_armed_recycle_aborts_when_compaction_raises(monkeypatch):
    _permitted_plan(monkeypatch)
    def _boom(*a, **k):
        raise OSError("disk full mid-write")
    monkeypatch.setattr(slr.hcp, "compact_if_enabled", _boom)
    reset_calls = []
    monkeypatch.setattr(slr.subprocess, "run", lambda *a, **k: reset_calls.append(a))

    out = slr.armed_recycle(None, _lane_row(), reason="r")
    assert out["recycled"] is False
    assert "compaction failed" in out["reason"]
    assert reset_calls == []                 # reset NEVER runs onto an unproven handoff


# ── CAI-RESP-1382: worker-lane recycle carve-out is STANDING-ARMED by default ──────────
# cai ruled (CAI-RESP-1381/1382) that CAI-681 cond 5's post-director-demo precondition was met
# >1mo ago and the disarm just sat stale. Flip the DEFAULT to armed; keep an EXPLICIT force-
# disarm (--no-arm / CTX_WD_LANE_ARM=0); the cond-5 MECHANISM + the other 4 gates stay intact.

def test_resolve_armed_defaults_true():
    """The flip: no flags -> standing-armed."""
    assert slr._resolve_armed(no_arm=False, env_lane_arm=None) is True


def test_resolve_armed_no_arm_flag_force_disarms():
    assert slr._resolve_armed(no_arm=True, env_lane_arm=None) is False


def test_resolve_armed_env_zero_force_disarms():
    assert slr._resolve_armed(no_arm=False, env_lane_arm="0") is False


def test_resolve_armed_env_nonzero_or_empty_stays_armed():
    # only an explicit "0" disarms via env; other/empty values keep the standing arm.
    assert slr._resolve_armed(no_arm=False, env_lane_arm="1") is True
    assert slr._resolve_armed(no_arm=False, env_lane_arm="") is True


def test_resolve_armed_no_arm_wins_over_env():
    assert slr._resolve_armed(no_arm=True, env_lane_arm="1") is False


def test_disarmed_still_refuses_at_boundary_mechanism_intact():
    """The cond-5 MECHANISM is unchanged: an explicit force-disarm still fails closed at the
    boundary even with all gates True (the default flipped, the gate did not go away)."""
    disarmed = slr._resolve_armed(no_arm=True, env_lane_arm=None)
    with pytest.raises(slr.fhb.BoundaryViolation):
        slr.fhb.assert_sre_lane_red_permitted(
            "cc-worker-lane",
            {"idle": True, "git_clean": True, "fresh_handoff": True},
            disarmed, identity=slr.fhb.SRE_AGENT_ID)


def test_singleton_still_fail_closed_even_when_armed():
    """cond 3 (singleton off-limits) is untouched: a singleton target refuses even armed=True."""
    with pytest.raises(slr.fhb.BoundaryViolation):
        slr.fhb.assert_sre_lane_red_permitted(
            "cai",  # a SINGLETON_BODY
            {"idle": True, "git_clean": True, "fresh_handoff": True},
            True, identity=slr.fhb.SRE_AGENT_ID)


def test_armed_worker_lane_with_all_gates_permits():
    """Positive path: default-armed + worker lane + all gates True -> permitted (no raise)."""
    slr.fhb.assert_sre_lane_red_permitted(
        "cc-worker-lane",
        {"idle": True, "git_clean": True, "fresh_handoff": True},
        slr._resolve_armed(no_arm=False, env_lane_arm=None), identity=slr.fhb.SRE_AGENT_ID)


# ── worker-lanes-first staged arming (Nazim 37793): the executor's ACTION scope can exclude
#    client-lane prefixes; discover_lanes stays unfiltered so the dead-man still watches all. ──
def _rows(*bases):
    return [{"base_agent_id": b, "lane": b, "tmux_session": b} for b in bases]


def test_apply_lane_scope_excludes_client_prefixes():
    lanes = _rows("cc-substrate", "cc-ihsanos", "cc-cosem-adcda", "cc-irsyad-coord",
                  "cc-shipforge", "cc-storefront", "cc-finance", "cc-quality")
    kept = [r["base_agent_id"] for r in
            slr.apply_lane_scope(lanes, ["cc-irsyad", "cc-cosem", "cc-shipforge", "cc-storefront"])]
    assert kept == ["cc-substrate", "cc-ihsanos", "cc-finance", "cc-quality"]


def test_apply_lane_scope_empty_exclude_keeps_all():
    # the all-lanes end-state (Musa 37752): no exclusions -> every lane in scope.
    lanes = _rows("cc-substrate", "cc-cosem-adcda", "cc-irsyad")
    assert slr.apply_lane_scope(lanes, []) == lanes


def test_excluded_prefixes_parses_env(monkeypatch):
    monkeypatch.setenv("SRE_LANE_EXCLUDE_PREFIXES", " cc-irsyad , cc-cosem ,, cc-shipforge ")
    assert slr._excluded_prefixes() == ["cc-irsyad", "cc-cosem", "cc-shipforge"]


def test_excluded_prefixes_unset_is_empty(monkeypatch):
    monkeypatch.delenv("SRE_LANE_EXCLUDE_PREFIXES", raising=False)
    assert slr._excluded_prefixes() == []


# ── op#29023 / #63312 / #63326 — identity-recycle GUARDS ──────────────────────
# Tonight a `--arm --ignore-context --lane cosem-adcda` self-check run BY
# cc-cosem-adcda-1 mis-resolved `--lane cosem-adcda` to a SIBLING session
# (cosem-adcda-urgent, freshest heartbeat under the shared bare base) and fired a
# REAL red-reset on it -> two-bodies-as-one collision on a live client lane.

def test_pick_session_single_candidate_returns_it():
    assert slr.pick_session("cosem-adcda", ["cosem-adcda"]) == ("cosem-adcda", None)

def test_pick_session_ambiguous_fails_closed_naming_siblings():
    """A base with >1 live sibling session must NOT silently pick one -- fail closed."""
    session, err = slr.pick_session(
        "cosem-adcda", ["cosem-adcda", "cosem-adcda-urgent", "cosem-adcda-photos"])
    assert session is None
    assert err is not None
    # names the ambiguous siblings + points at the disambiguator
    assert "cosem-adcda-urgent" in err and "cosem-adcda-photos" in err
    assert "--session" in err

def test_pick_session_explicit_disambiguates():
    session, err = slr.pick_session(
        "cosem-adcda", ["cosem-adcda", "cosem-adcda-urgent"], explicit_session="cosem-adcda-urgent")
    assert session == "cosem-adcda-urgent" and err is None

def test_pick_session_explicit_not_live_fails():
    session, err = slr.pick_session(
        "cosem-adcda", ["cosem-adcda"], explicit_session="cosem-adcda-urgent")
    assert session is None and err is not None and "cosem-adcda-urgent" in err

def test_pick_session_no_live_session_fails():
    session, err = slr.pick_session("ghost-lane", [])
    assert session is None and err is not None

def test_self_recycle_refused_when_armed_self_without_i_mean_it():
    """A lane running the recycler ON ITSELF, armed, with no --i-mean-it -> REFUSE
    (a 'self-check' must never silently red-reset). #63326."""
    assert slr.self_recycle_refused(
        invoker_agent="cc-cosem-adcda-1", invoker_session="cosem-adcda",
        target_agent="cc-cosem-adcda-1", target_session="cosem-adcda",
        armed=True, i_mean_it=False) is True

def test_self_recycle_allowed_with_i_mean_it():
    assert slr.self_recycle_refused(
        invoker_agent="cc-cosem-adcda-1", invoker_session="cosem-adcda",
        target_agent="cc-cosem-adcda-1", target_session="cosem-adcda",
        armed=True, i_mean_it=True) is False

def test_self_recycle_other_lane_not_refused():
    """Recycling a DIFFERENT lane is normal SRE action -- never refused by this guard."""
    assert slr.self_recycle_refused(
        invoker_agent="cc-fleet-health", invoker_session="fleet-health",
        target_agent="cc-cosem-adcda-1", target_session="cosem-adcda",
        armed=True, i_mean_it=False) is False

def test_self_recycle_disarmed_not_refused():
    """Disarmed (detect-only) self-run is harmless -- no refusal needed."""
    assert slr.self_recycle_refused(
        invoker_agent="cc-cosem-adcda-1", invoker_session="cosem-adcda",
        target_agent="cc-cosem-adcda-1", target_session="cosem-adcda",
        armed=False, i_mean_it=False) is False


def test_pick_instance_id_returns_registered_instance_for_session():
    rows = [("cc-cosem-adcda-2", "cosem-adcda-urgent"),
            ("cc-cosem-adcda-1", "cosem-adcda")]
    assert slr.pick_instance_id("cosem-adcda-urgent", rows, "cc-cosem-adcda") == "cc-cosem-adcda-2"

def test_pick_instance_id_falls_back_to_base_when_no_match():
    assert slr.pick_instance_id("ghost", [("cc-x-1", "other")], "cc-x") == "cc-x"

def test_pick_instance_id_falls_back_when_no_session():
    assert slr.pick_instance_id(None, [("cc-x-1", "s")], "cc-x") == "cc-x"


# ── state_key_for (bus #73009/#73011 -- per-sibling state-dict keying) ───────────
def test_state_key_for_disambiguates_siblings_of_the_same_base():
    k1 = slr.state_key_for("cc-x", "x-1")
    k3 = slr.state_key_for("cc-x", "x-3")
    assert k1 != k3

def test_state_key_for_falls_back_to_bare_base_without_a_session():
    assert slr.state_key_for("cc-x", None) == "cc-x"

def test_state_key_for_falls_back_to_bare_base_on_empty_session():
    assert slr.state_key_for("cc-x", "") == "cc-x"


# ── Build B facet 13 (bus #74293): sibling-ambiguous gates fall back / fail closed ──────────
# compute_gates' 3 base-keyed gates were discovered to have 3 DIFFERENT root causes. This slice
# fixes the 2 that need no new data: gate_context_bloat/latest_context_frac (fall back to a
# session-specific pane probe, mirroring lane_proactive_recycle_nudge._gauge_for_lane) and
# gate_fresh_handoff (fail closed -- handoff_find has no per-instance marker to fall back to).
# last_material_action_epoch's instance-attribution sub-fix (sub-fix 2) lands below, now that
# PR #324 (sub_tag column + bus_send.resolve_sub_tag) is on trunk.

def test_pane_only_frac_known_pane_returns_fraction(monkeypatch):
    monkeypatch.setattr(slr._lrd, "_pane", lambda session: (42, None))
    monkeypatch.setattr(slr._lrd, "resolve_lane_pct",
                        lambda **kw: (True, 42, None, "pane-pct=42% (fallback measurement)"))
    assert slr._pane_only_frac("some-session", 1_000_000) == pytest.approx(0.42)


def test_pane_only_frac_blind_returns_none(monkeypatch):
    monkeypatch.setattr(slr._lrd, "_pane", lambda session: (None, None))
    monkeypatch.setattr(slr._lrd, "resolve_lane_pct",
                        lambda **kw: (False, None, None, "UNKNOWN — gauge stale AND pane blind"))
    assert slr._pane_only_frac("some-session", 1_000_000) is None


def test_pane_only_frac_passes_no_gauge_input_to_resolve(monkeypatch):
    """A sibling-ambiguous gauge must never leak in even as a tiebreak -- resolve_lane_pct must
    be called with gauge_tokens=None, gauge_age_s=None, no matter what."""
    captured = {}
    monkeypatch.setattr(slr._lrd, "_pane", lambda session: (10, "k"))

    def _fake_resolve(**kw):
        captured.update(kw)
        return False, None, None, "n/a"
    monkeypatch.setattr(slr._lrd, "resolve_lane_pct", _fake_resolve)
    slr._pane_only_frac("s", 1_000_000)
    assert captured["gauge_tokens"] is None
    assert captured["gauge_age_s"] is None
    assert captured["pane_pct"] == 10 and captured["pane_hint_k"] == "k"


def test_latest_context_frac_falls_back_to_pane_when_sibling_ambiguous(monkeypatch):
    """>1 live sibling sharing a base -> the base-keyed cc_session_costs query must NEVER even
    run (it could be reading a DIFFERENT sibling's row) -- only the pane fallback fires."""
    monkeypatch.setattr(slr, "live_sessions_for_base", lambda conn, base: ["s-1", "s-2"])
    monkeypatch.setattr(slr, "_pane_only_frac", lambda session, window: 0.91)

    class _BoomConn:
        def cursor(self):
            raise AssertionError("base-keyed gauge query must not run when sibling-ambiguous")

    frac = slr.latest_context_frac(_BoomConn(), "cc-cosem-platform", session="cosem-platform-3")
    assert frac == 0.91


def test_latest_context_frac_single_sibling_keeps_base_keyed_gauge(monkeypatch):
    """Regression: exactly one live session (or no session passed) -> today's unchanged
    base-keyed behaviour, never the pane fallback."""
    monkeypatch.setattr(slr, "live_sessions_for_base", lambda conn, base: ["s-1"])
    called = []
    monkeypatch.setattr(slr, "_pane_only_frac", lambda session, window: called.append(1) or 0.5)

    class _Row:
        def __init__(self, row):
            self._row = row
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, *a, **k):
            pass
        def fetchone(self):
            return self._row

    class _Conn:
        def cursor(self):
            return _Row((800_000, 10))

    frac = slr.latest_context_frac(_Conn(), "cc-cosem-platform", window=1_000_000, session="s-1")
    assert frac == pytest.approx(0.8)
    assert called == []                      # pane fallback never invoked


def test_latest_context_frac_no_session_keeps_base_keyed_gauge(monkeypatch):
    """A caller that passes no session (every pre-existing call site) gets today's exact
    behaviour -- the sibling check is never even consulted."""
    monkeypatch.setattr(slr, "live_sessions_for_base",
                        lambda conn, base: (_ for _ in ()).throw(AssertionError("must not be called")))

    class _Row:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, *a, **k):
            pass
        def fetchone(self):
            return (800_000, 10)

    class _Conn:
        def cursor(self):
            return _Row()

    frac = slr.latest_context_frac(_Conn(), "cc-cosem-platform", window=1_000_000)
    assert frac == pytest.approx(0.8)


def test_gate_context_bloat_threads_session_through(monkeypatch):
    captured = {}
    def _fake_frac(conn, base, window, session=None):
        captured["session"] = session
        return 0.9
    monkeypatch.setattr(slr, "latest_context_frac", _fake_frac)
    assert slr.gate_context_bloat(object(), "cc-cosem-platform", session="cosem-platform-3") is True
    assert captured["session"] == "cosem-platform-3"


def test_gate_fresh_handoff_sibling_ambiguous_fails_closed(monkeypatch):
    """Even a genuinely-fresh handoff and a known last-action must fail closed when >1 live
    sibling shares the base -- there is no per-instance marker to attribute it to THIS one."""
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 2_000.0)
    assert slr.gate_fresh_handoff("cc-x", None, 1_000.0, sibling_ambiguous=True) is False
    # unchanged when not ambiguous (regression on the existing default):
    assert slr.gate_fresh_handoff("cc-x", None, 1_000.0, sibling_ambiguous=False) is True


def test_compute_gates_sibling_ambiguous_forces_fresh_handoff_false(monkeypatch):
    monkeypatch.setattr(slr, "live_sessions_for_base", lambda conn, base: ["cosem-platform-2", "cosem-platform-3"])
    monkeypatch.setattr(slr, "last_material_action_epoch",
                        lambda conn, base, session=None, sibling_ambiguous=False: 1_000.0)
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 5_000.0)  # genuinely fresh
    monkeypatch.setattr(slr, "gate_context_bloat", lambda conn, base, session=None: False)
    monkeypatch.setattr(slr, "gate_idle", lambda session, ssh_target=None: True)
    monkeypatch.setattr(slr, "gate_git_clean", lambda wt, ssh_target=None: True)

    lane_row = {"lane": "cosem-platform-3", "base_agent_id": "cc-cosem-platform",
               "tmux_session": "cosem-platform-3", "worktree_path": "/wt", "notes": None}
    gates = slr.compute_gates(object(), lane_row)
    assert gates["fresh_handoff"] is False     # ambiguous -> fail closed, despite a fresh handoff


def test_compute_gates_single_sibling_fresh_handoff_unchanged(monkeypatch):
    monkeypatch.setattr(slr, "live_sessions_for_base", lambda conn, base: ["cosem-platform-3"])
    monkeypatch.setattr(slr, "last_material_action_epoch",
                        lambda conn, base, session=None, sibling_ambiguous=False: 1_000.0)
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 5_000.0)
    monkeypatch.setattr(slr, "gate_context_bloat", lambda conn, base, session=None: False)
    monkeypatch.setattr(slr, "gate_idle", lambda session, ssh_target=None: True)
    monkeypatch.setattr(slr, "gate_git_clean", lambda wt, ssh_target=None: True)

    lane_row = {"lane": "cosem-platform-3", "base_agent_id": "cc-cosem-platform",
               "tmux_session": "cosem-platform-3", "worktree_path": "/wt", "notes": None}
    gates = slr.compute_gates(object(), lane_row)
    assert gates["fresh_handoff"] is True       # exactly one sibling -> unchanged behaviour


def test_compute_gates_conn_none_skips_ambiguity_check_defaults_unambiguous(monkeypatch):
    """conn=None (the offline self-test / several existing unit tests) can't prove ambiguity
    via a DB read -- defaults to unambiguous, today's single-sibling shape, unchanged."""
    monkeypatch.setattr(slr, "_newest_handoff_mtime", lambda base, notes: 1_000.0)
    gates = slr.compute_gates(None, _lane_row())
    # same assertion test_compute_gates_default_reference_fails_closed_without_conn already
    # makes for a DIFFERENT reason (no last_action) -- here we confirm it's not ALSO forced
    # False by a spuriously-True ambiguity check when conn is None.
    assert gates["fresh_handoff"] is False


# ── last_material_action_epoch (bus #74293 sub-fix 2): sibling-aware via sub_tag ────────────
# Unblocked by PR #324 (sub_tag column + bus_send.resolve_sub_tag). A bare from_agent=base
# query is unattributable across siblings -- ANY sibling's bus post would count toward EVERY
# sibling's "last material action", the same class of gap gate_context_bloat/gate_fresh_handoff
# already closed in facet 13's first slice.

class _CaptureCursor:
    def __init__(self, row):
        self._row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self._row


class _CaptureConn:
    def __init__(self, row):
        self.cur = _CaptureCursor(row)
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


def test_last_material_action_epoch_unambiguous_queries_by_from_agent(monkeypatch):
    """Regression: sibling_ambiguous=False (the default, every pre-existing caller) keeps
    today's exact base-keyed query -- instance_id_for_session must never even be consulted."""
    monkeypatch.setattr(
        slr, "instance_id_for_session",
        lambda cur, session, fallback: (_ for _ in ()).throw(
            AssertionError("must not resolve an instance when unambiguous")))
    import datetime as _dt
    ts = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
    conn = _CaptureConn((ts,))

    got = slr.last_material_action_epoch(conn, "cc-cosem-platform")

    assert got == ts.timestamp()
    sql, params = conn.cur.calls[0]
    assert "from_agent" in sql and "sub_tag" not in sql
    assert params == ["cc-cosem-platform"]


def test_last_material_action_epoch_sibling_ambiguous_filters_by_sub_tag(monkeypatch):
    """>1 live sibling sharing a base -> resolve THIS session's own instance id
    (instance_id_for_session, the same resolver pick_instance_id/the nudge path already use)
    and filter agent_messages on sub_tag -- never the unattributable bare base."""
    monkeypatch.setattr(slr, "instance_id_for_session",
                        lambda cur, session, fallback: "cc-cosem-platform-3")
    import datetime as _dt
    ts = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
    conn = _CaptureConn((ts,))

    got = slr.last_material_action_epoch(conn, "cc-cosem-platform",
                                         session="cosem-platform-3", sibling_ambiguous=True)

    assert got == ts.timestamp()
    sql, params = conn.cur.calls[0]
    assert "sub_tag" in sql
    assert params == ["cc-cosem-platform-3"]


def test_last_material_action_epoch_sibling_ambiguous_unattributable_returns_none(monkeypatch):
    """No live agent_status row for this session -> instance_id_for_session falls back to the
    bare base (its own documented no-match behaviour), which can NEVER equal a real sub_tag
    (the DB CHECK requires sub_tag to be strictly longer than from_agent, 'base-something') --
    so the sub_tag filter naturally finds nothing -> None, fail-closed, never the bare-base
    unattributable row."""
    monkeypatch.setattr(slr, "instance_id_for_session",
                        lambda cur, session, fallback: fallback)
    conn = _CaptureConn((None,))

    got = slr.last_material_action_epoch(conn, "cc-cosem-platform",
                                         session="cosem-platform-3", sibling_ambiguous=True)

    assert got is None


def test_compute_gates_passes_session_and_ambiguity_to_last_material_action_epoch(monkeypatch):
    """compute_gates must thread its own already-computed session + ambiguity verdict into
    last_material_action_epoch, not just gate_context_bloat/gate_fresh_handoff."""
    captured = {}

    def _fake(conn, base, session=None, sibling_ambiguous=False):
        captured["session"] = session
        captured["sibling_ambiguous"] = sibling_ambiguous
        return 1_000.0

    monkeypatch.setattr(slr, "last_material_action_epoch", _fake)
    monkeypatch.setattr(slr, "live_sessions_for_base",
                        lambda conn, base: ["cosem-platform-2", "cosem-platform-3"])
    monkeypatch.setattr(slr, "gate_context_bloat", lambda conn, base, session=None: False)
    monkeypatch.setattr(slr, "gate_idle", lambda session, ssh_target=None: True)
    monkeypatch.setattr(slr, "gate_git_clean", lambda wt, ssh_target=None: True)

    lane_row = {"lane": "cosem-platform-3", "base_agent_id": "cc-cosem-platform",
               "tmux_session": "cosem-platform-3", "worktree_path": "/wt", "notes": None}
    slr.compute_gates(object(), lane_row)

    assert captured["session"] == "cosem-platform-3"
    assert captured["sibling_ambiguous"] is True


# ── cross-host (gzb) support (bus #74293 FIX 2) ──────────────────────────────────────────────
# Design check-in with cc-fleet-health (bus #74346/#74348): gate_idle/gate_git_clean get
# READ-ONLY cross-host ssh support (gated on agent_status.host resolving to gzb) so detection
# works on a gzb-hosted lane (today cosem-port "always fails closed blind" — no ssh dispatch
# anywhere in the detection path). The FORCING action stays local-only: armed_recycle/
# reset_lane.sh is NEVER driven cross-host (no such primitive exists, by fleet doctrine — see
# hub_reach.py's own gzb remedy text, which explicitly forbids raw cross-host keystroke
# injection). A gzb-hosted lane whose gates confirm idle+clean+bloated+fresh-handoff gets the
# SAME non-destructive self-recycle-nudge shape lane_proactive_recycle_nudge.py already proves
# end-to-end, instead of a forced /clear.

def test_gate_idle_local_dispatches_the_existing_tmux_capture(monkeypatch):
    """ssh_target=None (the default, every pre-existing caller) must keep today's EXACT local
    tmux capture-pane invocation — no ssh anywhere in the call."""
    calls = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = "some idle pane text\n"
        return _R()

    monkeypatch.setattr(slr.subprocess, "run", _fake_run)
    assert slr.gate_idle("cosem-port") is True
    assert len(calls) == 1
    assert calls[0][0] == slr.TMUX
    assert "ssh" not in calls[0]


def test_gate_idle_remote_dispatches_over_ssh(monkeypatch):
    """ssh_target set -> the SAME capture-pane primitive, read-only, over `ssh <target>` —
    the identical shape hub_reach.py documents for the hub's own gzb reach, here parameterized
    by the lane's own session instead of the hub's 'orch'."""
    calls = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = "some idle pane text\n"
        return _R()

    monkeypatch.setattr(slr.subprocess, "run", _fake_run)
    assert slr.gate_idle("cosem-port", ssh_target="gzb") is True
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "ssh" and cmd[1] == "gzb"
    assert "capture-pane" in cmd[2]
    assert "cosem-port" in cmd[2]


def test_gate_idle_remote_ssh_failure_fails_closed(monkeypatch):
    """A remote capture that errors (unreachable host, dead ssh) must fail CLOSED — same shape
    as a local capture failure, never a guessed idle verdict."""
    def _raise(cmd, **kw):
        raise OSError("ssh unreachable")
    monkeypatch.setattr(slr.subprocess, "run", _raise)
    assert slr.gate_idle("cosem-port", ssh_target="gzb") is False


def test_gate_git_clean_local_unchanged(monkeypatch, tmp_path):
    """ssh_target=None keeps today's exact local os.path + git-status behaviour."""
    calls = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    monkeypatch.setattr(slr.subprocess, "run", _fake_run)
    assert slr.gate_git_clean(str(tmp_path)) is True
    assert len(calls) == 1
    assert calls[0][0] == "git"
    assert "ssh" not in calls[0]


def test_gate_git_clean_remote_dispatches_over_ssh_without_a_local_isdir_check(monkeypatch):
    """A remote worktree_path lives on the OTHER host — a local os.path.isdir check on it would
    always (wrongly-for-the-wrong-reason) read as missing. The remote path must be proven via
    ssh instead, never short-circuited by a local filesystem check."""
    calls = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    monkeypatch.setattr(slr.subprocess, "run", _fake_run)
    assert slr.gate_git_clean("/Users/gazzai/wingmen/orchestrator/cosem-port",
                             ssh_target="gzb") is True
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "ssh" and cmd[1] == "gzb"
    assert "git -C" in cmd[2] and "status --porcelain" in cmd[2]
    assert "cosem-port" in cmd[2]


def test_gate_git_clean_remote_dirty_fails(monkeypatch):
    def _fake_run(cmd, **kw):
        class _R:
            returncode = 0
            stdout = " M some/file.py\n"
        return _R()
    monkeypatch.setattr(slr.subprocess, "run", _fake_run)
    assert slr.gate_git_clean("/wt/cosem-port", ssh_target="gzb") is False


def test_gate_git_clean_remote_ssh_failure_fails_closed(monkeypatch):
    def _raise(cmd, **kw):
        raise OSError("ssh unreachable")
    monkeypatch.setattr(slr.subprocess, "run", _raise)
    assert slr.gate_git_clean("/wt/cosem-port", ssh_target="gzb") is False


def test_compute_gates_threads_ssh_target_from_lane_row_host(monkeypatch):
    """compute_gates must resolve lane_row['host'] -> an ssh_target (via hub_reach) and pass it
    to BOTH gate_idle and gate_git_clean — a gzb-hosted lane's gates must be read cross-host,
    not silently local (which would always read as unreachable/not-clean for the wrong reason)."""
    captured = {}

    def _fake_idle(session, ssh_target=None):
        captured["idle_ssh_target"] = ssh_target
        return True

    def _fake_clean(wt, ssh_target=None):
        captured["clean_ssh_target"] = ssh_target
        return True

    monkeypatch.setattr(slr, "gate_idle", _fake_idle)
    monkeypatch.setattr(slr, "gate_git_clean", _fake_clean)
    monkeypatch.setattr(slr, "gate_context_bloat", lambda conn, base, session=None: False)
    monkeypatch.setattr(slr, "last_material_action_epoch",
                        lambda conn, base, session=None, sibling_ambiguous=False: None)

    lane_row = {"lane": "cosem-port", "base_agent_id": "cc-cosem-port",
               "tmux_session": "cosem-port", "worktree_path": "/wt/cosem-port",
               "notes": None, "host": "gzbai"}
    slr.compute_gates(None, lane_row)

    assert captured["idle_ssh_target"] == "gzb"
    assert captured["clean_ssh_target"] == "gzb"


def test_compute_gates_local_host_passes_no_ssh_target(monkeypatch):
    captured = {}

    def _fake_idle(session, ssh_target=None):
        captured["idle_ssh_target"] = ssh_target
        return True

    def _fake_clean(wt, ssh_target=None):
        captured["clean_ssh_target"] = ssh_target
        return True

    monkeypatch.setattr(slr, "gate_idle", _fake_idle)
    monkeypatch.setattr(slr, "gate_git_clean", _fake_clean)
    monkeypatch.setattr(slr, "gate_context_bloat", lambda conn, base, session=None: False)
    monkeypatch.setattr(slr, "last_material_action_epoch",
                        lambda conn, base, session=None, sibling_ambiguous=False: None)

    lane_row = {"lane": "irsyad", "base_agent_id": "cc-irsyad",
               "tmux_session": "irsyad", "worktree_path": "/wt/irsyad",
               "notes": None, "host": "Sheikhs-Mini"}
    slr.compute_gates(None, lane_row)

    assert captured["idle_ssh_target"] is None
    assert captured["clean_ssh_target"] is None


def test_discover_lanes_sql_selects_host_per_instance(monkeypatch):
    """discover_lanes must surface agent_status.host per row (bus #74293 FIX 2) — FIX 2's
    cross-host gates key off lane_row['host'], so discover_lanes (the only populator of
    lane_row) must select it, in BOTH the per_instance and the DISTINCT-ON cardinality."""
    captured_sql = {}

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            captured_sql["sql"] = sql
            captured_sql["params"] = params

        @property
        def description(self):
            return [("lane",), ("worktree_path",), ("branch",), ("base_agent_id",),
                   ("desired_state",), ("notes",), ("tmux_session",), ("host",)]

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

    slr.discover_lanes(_Conn(), per_instance=True)
    assert "s.host" in captured_sql["sql"]
    assert ", host" in captured_sql["sql"] or " host " in captured_sql["sql"] or "host\n" in captured_sql["sql"]

    captured_sql.clear()
    slr.discover_lanes(_Conn(), per_instance=False)
    assert "s.host" in captured_sql["sql"]


# ── cross-host self-recycle nudge (the FORCING-action fallback) ──────────────────────────────

def test_cross_host_recycle_nudge_message_is_pure_and_non_destructive():
    subject, body = slr.cross_host_recycle_nudge_message(
        "cc-cosem-port", "cosem-port", "gzbai",
        {"idle": True, "git_clean": True, "fresh_handoff": True, "context_bloat": True},
        "SRE auto-recycle: wedged/bloated lane")
    assert slr._CROSS_HOST_NUDGE_PREFIX in subject
    assert "cc-cosem-port" in subject
    assert "self_recycle.sh" in body
    assert "reset_lane.sh" in body
    assert "gzbai" in body
    assert "you decide" in body.lower() or "YOU decide" in body


def test_cross_host_nudge_recently_true_within_cooldown():
    conn = _CaptureConn((1,))
    assert slr._cross_host_nudge_recently(conn, "cc-cosem-port") is True


def test_cross_host_nudge_recently_false_when_no_prior_row():
    conn = _CaptureConn(None)
    assert slr._cross_host_nudge_recently(conn, "cc-cosem-port") is False


def test_send_cross_host_recycle_nudge_inserts_when_not_on_cooldown(monkeypatch):
    monkeypatch.setattr(slr, "_cross_host_nudge_recently", lambda conn, base, **kw: False)
    conn = _CaptureConn(None)
    lane_row = {"base_agent_id": "cc-cosem-port", "tmux_session": "cosem-port", "host": "gzbai"}

    sent = slr.send_cross_host_recycle_nudge(
        conn, lane_row, {"idle": True}, "SRE auto-recycle: wedged/bloated lane")

    assert sent is True
    assert conn.commits == 1
    sql, params = conn.cur.calls[-1]
    assert "INSERT INTO agent_messages" in sql
    assert params[0] == slr.fhb.SRE_AGENT_ID
    assert params[1] == "cc-cosem-port"


def test_send_cross_host_recycle_nudge_skips_on_cooldown(monkeypatch):
    monkeypatch.setattr(slr, "_cross_host_nudge_recently", lambda conn, base, **kw: True)
    conn = _CaptureConn(None)
    lane_row = {"base_agent_id": "cc-cosem-port", "tmux_session": "cosem-port", "host": "gzbai"}

    sent = slr.send_cross_host_recycle_nudge(
        conn, lane_row, {"idle": True}, "SRE auto-recycle: wedged/bloated lane")

    assert sent is False
    assert conn.commits == 0
    assert conn.cur.calls == []


# ── routing: a gzb-hosted permitted lane gets the nudge, never a cross-host armed_recycle ────

def _plan_result(base="cc-cosem-port", session="cosem-port"):
    return {"lane": session, "base": base, "session": session,
           "gates": {"idle": True, "git_clean": True, "fresh_handoff": True, "context_bloat": True},
           "permitted": True, "reason": "all gates verified; armed"}


def test_perform_armed_action_remote_host_sends_nudge_not_armed_recycle(monkeypatch):
    calls = {"armed_recycle": 0, "nudge": 0}
    monkeypatch.setattr(slr, "armed_recycle",
                        lambda *a, **k: calls.__setitem__("armed_recycle", calls["armed_recycle"] + 1))
    monkeypatch.setattr(slr, "send_cross_host_recycle_nudge",
                        lambda *a, **k: (calls.__setitem__("nudge", calls["nudge"] + 1), True)[1])
    monkeypatch.setattr(slr, "self_recycle_refused", lambda *a, **k: False)

    lane_row = {"base_agent_id": "cc-cosem-port", "tmux_session": "cosem-port", "host": "gzbai"}
    p = _plan_result()

    act = slr._perform_armed_action(object(), lane_row, p, reason="r",
                                    handoff_ref_epoch=None,
                                    invoker_agent=None, invoker_session=None, i_mean_it=False)

    assert calls["armed_recycle"] == 0
    assert calls["nudge"] == 1
    assert act["cross_host"] is True
    assert act["acted"] is True


def test_perform_armed_action_local_host_calls_armed_recycle(monkeypatch):
    calls = {"armed_recycle": 0, "nudge": 0}
    monkeypatch.setattr(slr, "armed_recycle",
                        lambda *a, **k: (calls.__setitem__("armed_recycle", calls["armed_recycle"] + 1),
                                        {"recycled": True, "rc": 0, "output": "ok"})[1])
    monkeypatch.setattr(slr, "send_cross_host_recycle_nudge",
                        lambda *a, **k: calls.__setitem__("nudge", calls["nudge"] + 1))
    monkeypatch.setattr(slr, "self_recycle_refused", lambda *a, **k: False)

    lane_row = {"base_agent_id": "cc-irsyad", "tmux_session": "irsyad", "host": "Sheikhs-Mini"}
    p = _plan_result(base="cc-irsyad", session="irsyad")

    act = slr._perform_armed_action(object(), lane_row, p, reason="r",
                                    handoff_ref_epoch=None,
                                    invoker_agent=None, invoker_session=None, i_mean_it=False)

    assert calls["nudge"] == 0
    assert calls["armed_recycle"] == 1
    assert act["cross_host"] is False
    assert act["acted"] is True
    assert act["recycled"] is True


def test_perform_armed_action_local_host_missing_still_calls_armed_recycle(monkeypatch):
    """A lane_row with NO host key at all (every pre-existing discover_lanes caller before this
    FIX) must keep dispatching to armed_recycle exactly as today — ssh_target_for_host(None) is
    None, so this is the unchanged default path."""
    monkeypatch.setattr(slr, "armed_recycle", lambda *a, **k: {"recycled": True})
    monkeypatch.setattr(slr, "send_cross_host_recycle_nudge",
                        lambda *a, **k: pytest.fail("must not nudge a local/unknown-host lane"))
    monkeypatch.setattr(slr, "self_recycle_refused", lambda *a, **k: False)

    lane_row = {"base_agent_id": "cc-irsyad", "tmux_session": "irsyad"}
    p = _plan_result(base="cc-irsyad", session="irsyad")

    act = slr._perform_armed_action(object(), lane_row, p, reason="r",
                                    handoff_ref_epoch=None,
                                    invoker_agent=None, invoker_session=None, i_mean_it=False)

    assert act["cross_host"] is False
    assert act["recycled"] is True


def test_perform_armed_action_self_recycle_refused_never_nudges_or_recycles(monkeypatch):
    monkeypatch.setattr(slr, "armed_recycle",
                        lambda *a, **k: pytest.fail("must not armed_recycle when self-refused"))
    monkeypatch.setattr(slr, "send_cross_host_recycle_nudge",
                        lambda *a, **k: pytest.fail("must not nudge when self-refused"))
    monkeypatch.setattr(slr, "self_recycle_refused", lambda *a, **k: True)

    lane_row = {"base_agent_id": "cc-cosem-port", "tmux_session": "cosem-port", "host": "gzbai"}
    p = _plan_result()

    act = slr._perform_armed_action(object(), lane_row, p, reason="r",
                                    handoff_ref_epoch=None,
                                    invoker_agent="cc-cosem-port", invoker_session="cosem-port",
                                    i_mean_it=False)

    assert act["refused"] is True
    assert act["acted"] is False
