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
