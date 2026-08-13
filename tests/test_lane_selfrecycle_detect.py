#!/usr/bin/env python3
"""Unit tests for lane_selfrecycle_detect.py (Stage-0 detect-only).

TDD each gate function with monkeypatched tmux capture / DB — no live fleet, no
side effects. Covers the two root-cause fixes explicitly:
  GAP-1  pane-% parse (incl. no-%-line -> below-threshold, and a stale-gauge vs
         live-pane disagreement).
  GAP-2  per-session enumeration — a shared-base family yields N distinct
         sessions, NOT 1.
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ORCH = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ORCH, "scripts"))
sys.path.insert(0, os.path.join(_ORCH, "scripts", "lib"))

import fleet_health_boundaries as fhb  # noqa: E402
import lane_selfrecycle_detect as d  # noqa: E402


# ── GAP-1: live-pane context % parse ─────────────────────────────────────────
class TestPaneContextParse:
    def test_explicit_percent_used(self):
        assert d.parse_pane_context_pct("✻ Working  97% context used") == 97.0

    def test_incident_line_97(self):
        # the cc-irsyad-1 incident: pane showed "97% context used" while gauge stale
        pane = "\n".join(["some transcript", "❯ ", "  97% context used · esc"])
        assert d.parse_pane_context_pct(pane) == 97.0

    def test_percent_left_is_inverted(self):
        assert d.parse_pane_context_pct("12% context left") == 88.0

    def test_token_count_decimal(self):
        # the LIVE fleet's CC version renders a token count, not a %.
        assert d.parse_pane_context_pct("new task? /clear to save 858.1k tokens", window=1_000_000) == 85.8

    def test_token_count_integer(self):
        assert d.parse_pane_context_pct("/clear to save 551k tokens", window=1_000_000) == 55.1

    def test_token_count_custom_window(self):
        # 141.1k on a 200k window -> 70.55 -> round(,1) = 70.5
        assert d.parse_pane_context_pct("/clear to save 141.1k tokens", window=200_000) == 70.5

    def test_no_line_is_none_below_threshold(self):
        # GAP-1 fail-safe: no readable context line -> None -> not a candidate.
        assert d.parse_pane_context_pct("just some prose\nno status chrome here") is None

    def test_empty_and_none(self):
        assert d.parse_pane_context_pct("") is None
        assert d.parse_pane_context_pct(None) is None

    def test_mention_upscroll_not_matched(self):
        # a line ABOUT context far up-scroll must not trip (only STATUS_TAIL scanned)
        pane = "\n".join(["we discussed 99% context used earlier"] + [f"line {i}" for i in range(40)])
        assert d.parse_pane_context_pct(pane) is None


class TestGateBloat:
    def test_over_threshold(self):
        assert d.gate_bloat(97.0) is True

    def test_under_threshold(self):
        assert d.gate_bloat(85.0) is False

    def test_none_fail_safe(self):
        assert d.gate_bloat(None) is False

    def test_boundary_exact(self):
        assert d.gate_bloat(90.0, threshold=90.0) is True
        assert d.gate_bloat(89.9, threshold=90.0) is False


# ── idle part (a): sustained idle ────────────────────────────────────────────
class TestIdleSustained:
    def test_identical_and_not_busy_is_idle(self, monkeypatch):
        monkeypatch.setattr(d, "_capture_raw", lambda s: "❯ \nquiet pane")
        monkeypatch.setattr(d, "_pane_text_busy", lambda t: False)
        idle, reason = d.gate_idle_sustained("x", settle_s=0)
        assert idle is True

    def test_busy_marker_not_idle(self, monkeypatch):
        monkeypatch.setattr(d, "_capture_raw", lambda s: "working... esc to interrupt")
        monkeypatch.setattr(d, "_pane_text_busy", lambda t: True)
        idle, reason = d.gate_idle_sustained("x", settle_s=0)
        assert idle is False
        assert "active-turn" in reason

    def test_changed_pane_not_idle(self, monkeypatch):
        seq = iter(["frame A", "frame B"])
        monkeypatch.setattr(d, "_capture_raw", lambda s: next(seq))
        monkeypatch.setattr(d, "_pane_text_busy", lambda t: False)
        idle, reason = d.gate_idle_sustained("x", settle_s=0)
        assert idle is False
        assert "changed" in reason

    def test_unreadable_pane_fail_closed(self, monkeypatch):
        monkeypatch.setattr(d, "_capture_raw", lambda s: None)
        idle, reason = d.gate_idle_sustained("x", settle_s=0)
        assert idle is False


# ── idle part (b): composer clean (ghost vs real draft) ──────────────────────
class TestComposerClean:
    def test_empty_is_clean(self):
        clean, _ = d.gate_composer_clean({"empty": True, "partial": "ok"})
        assert clean is True

    def test_dim_ghost_is_clean(self):
        # a dim history-ghost is empty underneath -> still idle/clean
        clean, reason = d.gate_composer_clean(
            {"empty": False, "ghost": True, "partial": "ok", "flat": "old recalled cmd"})
        assert clean is True
        assert "ghost" in reason

    def test_real_bright_draft_not_clean(self):
        clean, reason = d.gate_composer_clean(
            {"empty": False, "ghost": False, "partial": "ok", "flat": "poll the bus for reply"})
        assert clean is False
        assert "HOLD" in reason

    def test_real_dim_novel_draft_not_clean(self):
        # the FIX-1 case: dim BUT novel (not a ghost) -> real staged work -> HOLD
        clean, _ = d.gate_composer_clean(
            {"empty": False, "ghost": False, "partial": "ok", "flat": "continue with PR-4"})
        assert clean is False

    def test_noprompt_fail_closed(self):
        clean, reason = d.gate_composer_clean({"empty": True, "partial": "noprompt"})
        assert clean is False

    def test_none_fail_closed(self):
        clean, _ = d.gate_composer_clean(None)
        assert clean is False


# ── GAP-2: per-session enumeration (NOT distinct-on-base) ────────────────────
class TestPerSessionEnumeration:
    def _shared_base_rows(self):
        # cc-irsyad has 4 live sessions — the exact collapse GAP-2 fixes.
        return [
            {"agent_id": "cc-irsyad-1", "base_agent_id": "cc-irsyad", "tmux_session": "irsyad",
             "host": "Sheikhs-Mini", "hb_min": 2, "worktree_path": "/wt/irsyad", "notes": ""},
            {"agent_id": "cc-irsyad-2", "base_agent_id": "cc-irsyad", "tmux_session": "irsyad-coord",
             "host": "Sheikhs-Mini", "hb_min": 0, "worktree_path": "/wt/coord", "notes": ""},
            {"agent_id": "cc-irsyad-3", "base_agent_id": "cc-irsyad", "tmux_session": "irsyad-prog1",
             "host": "Sheikhs-Mini", "hb_min": 1, "worktree_path": "/wt/prog1", "notes": ""},
            {"agent_id": "cc-irsyad-4", "base_agent_id": "cc-irsyad", "tmux_session": "irsyad-prog2",
             "host": "Sheikhs-Mini", "hb_min": 5, "worktree_path": "/wt/prog2", "notes": ""},
        ]

    def test_shared_base_yields_n_sessions_not_one(self, monkeypatch):
        monkeypatch.setattr(d, "_fetch_status_rows", lambda conn: self._shared_base_rows())
        out = d.enumerate_worker_sessions(None)
        assert len(out) == 4  # NOT collapsed to 1 by base
        sessions = {r["tmux_session"] for r in out}
        assert sessions == {"irsyad", "irsyad-coord", "irsyad-prog1", "irsyad-prog2"}
        # all share the same base but remain individually targetable
        assert {r["base_agent_id"] for r in out} == {"cc-irsyad"}

    def test_singleton_excluded_defence_in_depth(self, monkeypatch):
        rows = self._shared_base_rows() + [
            {"agent_id": "cai", "base_agent_id": "cai", "tmux_session": "cai",
             "host": None, "hb_min": 1, "worktree_path": None, "notes": ""},
            {"agent_id": "cc-fleet-health", "base_agent_id": "cc-fleet-health",
             "tmux_session": "fleet-health", "host": None, "hb_min": 1,
             "worktree_path": None, "notes": ""},
        ]
        monkeypatch.setattr(d, "_fetch_status_rows", lambda conn: rows)
        out = d.enumerate_worker_sessions(None)
        bases = {r["base_agent_id"] for r in out}
        assert "cai" not in bases
        assert "cc-fleet-health" not in bases
        assert len(out) == 4


# ── worker-only boundary ─────────────────────────────────────────────────────
class TestWorkerOnly:
    def test_singletons_are_off_limits(self):
        for body in fhb.SINGLETON_BODIES:
            with pytest.raises(fhb.BoundaryViolation):
                fhb.assert_sre_never_targets_singleton(body, identity=fhb.SRE_AGENT_ID)

    def test_worker_permitted_by_guard(self):
        # a worker base does not raise
        fhb.assert_sre_never_targets_singleton("cc-irsyad", identity=fhb.SRE_AGENT_ID)

    def test_evaluate_asserts_never_targets_singleton(self, monkeypatch):
        # if a singleton somehow reached evaluate_session, it must crash loudly
        monkeypatch.setattr(d, "_capture_raw", lambda s: None)
        with pytest.raises(fhb.BoundaryViolation):
            d.evaluate_session(None, {"tmux_session": "cai", "base_agent_id": "cai"})


# ── checkpointable (handoff freshness) ───────────────────────────────────────
class TestHandoffState:
    def test_missing(self, monkeypatch):
        monkeypatch.setattr(d.slr, "_newest_handoff_mtime", lambda b, n: None)
        assert d.handoff_state(None, "cc-x", None) == "missing"

    def test_fresh(self, monkeypatch):
        monkeypatch.setattr(d.slr, "_newest_handoff_mtime", lambda b, n: 2000.0)
        monkeypatch.setattr(d.slr, "last_material_action_epoch", lambda c, b: 1000.0)
        assert d.handoff_state(None, "cc-x", None) == "fresh"

    def test_stale_older_than_action(self, monkeypatch):
        monkeypatch.setattr(d.slr, "_newest_handoff_mtime", lambda b, n: 500.0)
        monkeypatch.setattr(d.slr, "last_material_action_epoch", lambda c, b: 1000.0)
        assert d.handoff_state(None, "cc-x", None) == "stale"

    def test_unverifiable_action_is_stale(self, monkeypatch):
        # handoff exists but last action can't be established -> conservative 'stale'
        monkeypatch.setattr(d.slr, "_newest_handoff_mtime", lambda b, n: 500.0)
        monkeypatch.setattr(d.slr, "last_material_action_epoch", lambda c, b: None)
        assert d.handoff_state(None, "cc-x", None) == "stale"


# ── git-clean tri-state ──────────────────────────────────────────────────────
class TestGitState:
    def test_clean(self, monkeypatch):
        monkeypatch.setattr(d.slr, "gate_git_clean", lambda p: True)
        assert d.git_state("/some/wt") == "clean"

    def test_unknown_when_no_path(self, monkeypatch):
        monkeypatch.setattr(d.slr, "gate_git_clean", lambda p: False)
        assert d.git_state(None) == "unknown"

    def test_dirty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(d.slr, "gate_git_clean", lambda p: False)
        assert d.git_state(str(tmp_path)) == "dirty"


# ── gauge cross-check: stale-vs-live disagreement (GAP-1 logging) ────────────
class TestGaugeDisagreement:
    class _Cur:
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
        def __init__(self, row):
            self._row = row

        def cursor(self):
            return TestGaugeDisagreement._Cur(self._row)

    def test_stale_gauge_reading(self):
        # 428553 tokens @ 2531 min old -> ~42.9%, STALE
        conn = self._Conn((428553, 2531.0))
        pct, age = d.gauge_reading(conn, "cc-irsyad", "irsyad-coord")
        assert pct == 42.9
        assert age == 2531

    def test_evaluate_flags_disagreement(self, monkeypatch):
        # live pane says ~86% while a stale gauge says ~7% -> DISAGREE + STALE logged
        monkeypatch.setattr(d, "_capture_raw", lambda s: "/clear to save 858.1k tokens")
        monkeypatch.setattr(d, "gauge_reading", lambda c, b, s: (7.0, 2531.0))
        # short-circuit the heavy gates (bloated path) deterministically
        monkeypatch.setattr(d, "gate_idle_sustained", lambda s, **k: (False, "stub"))
        v = d.evaluate_session(None, {"tmux_session": "irsyad-coord", "base_agent_id": "cc-irsyad",
                                      "agent_id": "cc-irsyad-2", "host": "Sheikhs-Mini",
                                      "hb_min": 0, "worktree_path": None, "notes": ""})
        assert v["pane_pct"] == 85.8
        assert v["gauge_pct"] == 7.0
        assert v["gauge_stale"] is True
        assert v["gauge_disagree"] is True


# ── verdict integration (end-to-end, all seams stubbed; FIRES NOTHING) ───────
class TestVerdict:
    def _lane(self):
        return {"tmux_session": "worklane", "base_agent_id": "cc-worker", "agent_id": "cc-worker-1",
                "host": "Sheikhs-Mini", "hb_min": 1, "worktree_path": "/wt/x", "notes": ""}

    def test_below_threshold_skips(self, monkeypatch):
        monkeypatch.setattr(d, "_capture_raw", lambda s: "/clear to save 400k tokens")  # 40%
        monkeypatch.setattr(d, "gauge_reading", lambda c, b, s: None)
        v = d.evaluate_session(None, self._lane())
        assert v["verdict"] == "skip"
        assert "< 90" in v["reason"]

    def test_would_recycle_all_gates_pass(self, monkeypatch):
        monkeypatch.setattr(d, "_capture_raw", lambda s: "/clear to save 950k tokens")  # 95%
        monkeypatch.setattr(d, "gauge_reading", lambda c, b, s: None)
        monkeypatch.setattr(d, "gate_idle_sustained", lambda s, **k: (True, "idle"))
        monkeypatch.setattr(d, "probe_composer", lambda s: {"empty": True, "partial": "ok"})
        monkeypatch.setattr(d, "handoff_state", lambda c, b, n: "fresh")
        monkeypatch.setattr(d, "git_state", lambda p: "clean")
        v = d.evaluate_session(None, self._lane())
        assert v["verdict"] == "WOULD-RECYCLE"

    def test_bloated_idle_but_missing_handoff_is_pending(self, monkeypatch):
        # the incident shape: bloated + idle + clean + git-clean but NO fresh handoff
        monkeypatch.setattr(d, "_capture_raw", lambda s: "/clear to save 970k tokens")  # 97%
        monkeypatch.setattr(d, "gauge_reading", lambda c, b, s: None)
        monkeypatch.setattr(d, "gate_idle_sustained", lambda s, **k: (True, "idle"))
        monkeypatch.setattr(d, "probe_composer", lambda s: {"empty": True, "partial": "ok"})
        monkeypatch.setattr(d, "handoff_state", lambda c, b, n: "missing")
        monkeypatch.setattr(d, "git_state", lambda p: "clean")
        v = d.evaluate_session(None, self._lane())
        assert v["verdict"] == "WOULD-RECYCLE-PENDING-CHKPT"

    def test_real_draft_holds(self, monkeypatch):
        monkeypatch.setattr(d, "_capture_raw", lambda s: "/clear to save 970k tokens")
        monkeypatch.setattr(d, "gauge_reading", lambda c, b, s: None)
        monkeypatch.setattr(d, "gate_idle_sustained", lambda s, **k: (True, "idle"))
        monkeypatch.setattr(d, "probe_composer",
                            lambda s: {"empty": False, "ghost": False, "partial": "ok", "flat": "deploy now"})
        monkeypatch.setattr(d, "handoff_state", lambda c, b, n: "fresh")
        monkeypatch.setattr(d, "git_state", lambda p: "clean")
        v = d.evaluate_session(None, self._lane())
        assert v["verdict"] == "skip"
        assert "HOLD" in v["reason"]

    def test_dirty_git_skips(self, monkeypatch):
        monkeypatch.setattr(d, "_capture_raw", lambda s: "/clear to save 970k tokens")
        monkeypatch.setattr(d, "gauge_reading", lambda c, b, s: None)
        monkeypatch.setattr(d, "gate_idle_sustained", lambda s, **k: (True, "idle"))
        monkeypatch.setattr(d, "probe_composer", lambda s: {"empty": True, "partial": "ok"})
        monkeypatch.setattr(d, "handoff_state", lambda c, b, n: "fresh")
        monkeypatch.setattr(d, "git_state", lambda p: "dirty")
        v = d.evaluate_session(None, self._lane())
        assert v["verdict"] == "skip"
        assert "git dirty" in v["reason"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
