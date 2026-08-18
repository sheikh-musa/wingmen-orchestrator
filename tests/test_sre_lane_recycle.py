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
