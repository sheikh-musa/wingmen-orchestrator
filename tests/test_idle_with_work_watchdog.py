"""Acceptance test for the idle-with-work watchdog core (orch-console #48417).

Replays today's incident: cc-irsyad-coord idle while owning ready work (#114/#119/#129)
-> MUST fire. A lane whose only items carry blocked_on -> MUST NOT fire. Pure core, no DB.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import idle_with_work_watchdog as w  # noqa: E402


def _item(ref, blocked_on=None):
    return {"ref": ref, "summary": ref, "blocked_on": blocked_on}


COORD_OPEN = [_item("commitment#114"), _item("commitment#119"), _item("commitment#129")]


def test_replay_coord_idle_with_open_work_fires():
    c = w.classify(COORD_OPEN, pane_idle=True)
    assert c["should_fire"] is True
    assert len(c["actionable"]) == 3 and c["blocked"] == []


def test_only_blocked_items_does_not_fire():
    items = [_item("commitment#200", blocked_on="waiting on Wan since 2026-10-01"),
             _item("commitment#201", blocked_on="client review (Shuq)")]
    c = w.classify(items, pane_idle=True)
    assert c["should_fire"] is False
    assert c["actionable"] == [] and len(c["blocked"]) == 2


def test_not_idle_does_not_fire():
    assert w.classify(COORD_OPEN, pane_idle=False)["should_fire"] is False


def test_mixed_fires_on_actionable_subset():
    items = COORD_OPEN + [_item("commitment#210", blocked_on="external: bank batch")]
    c = w.classify(items, pane_idle=True)
    assert c["should_fire"] is True
    assert len(c["actionable"]) == 3 and len(c["blocked"]) == 1


def test_empty_blocked_on_is_actionable():
    # a blocked_on of "" or whitespace is NOT a real block
    c = w.classify([_item("commitment#1", blocked_on="   ")], pane_idle=True)
    assert c["should_fire"] is True and len(c["actionable"]) == 1


def test_items_hash_is_order_stable():
    assert w.items_hash(COORD_OPEN) == w.items_hash(list(reversed(COORD_OPEN)))
    assert w.items_hash(COORD_OPEN) != w.items_hash(COORD_OPEN[:2])


# --------------------------------------------------------------------------
# Dispatch path (cc-quality BLOCKING HIGH #48596): _nudge must call the REAL
# scripts/lane_nudge.sh (positional args, via fleet_lanes tmux-session lookup),
# check its return code, and NEVER report a silent success. A nudge that cannot
# be delivered MUST fall through to the page path. These tests drive the REAL
# lane_nudge.sh against a non-existent tmux session (a deterministic, side-effect-
# free "dry-run target" — the script exits 2 "no such session" in ~40ms), NOT a
# mock of subprocess, per Nazim's directive + wet-prove-the-shipped-artifact.
# --------------------------------------------------------------------------

_W = [{"ref": "commitment#1", "summary": "do the thing", "blocked_on": None}]


class _FakeCur:
    """Minimal cursor: no state row (-> tick 1), swallows writes. Lets run_lane's
    dispatch logic run without a DB, while _nudge still calls the real lane_nudge.sh."""
    def execute(self, sql, params=None):
        self._last = sql
    def fetchone(self):
        return None  # idle_with_work_state has no prior row -> fresh -> tick 1


def test_nudge_runs_real_lane_nudge_sh_and_reports_failure_on_missing_session():
    # Mapped to a tmux session that does not exist -> real lane_nudge.sh exits 2 ->
    # _nudge MUST return False (not None, not a silent success).
    ok = w._nudge("cc-bogus", _W, [], dry=False,
                  lane_map={"cc-bogus": "no-such-tmux-session-iww-test"})
    assert ok is False


def test_nudge_unmapped_lane_is_failure_not_silent():
    # cc-irsyad-1 / cc-irsyad-2 have NO fleet_lanes row -> cannot resolve a session ->
    # must be a failure, never a silent no-op.
    ok = w._nudge("cc-irsyad-1", _W, [], dry=False, lane_map={})
    assert ok is False


def test_run_lane_tick1_nudge_failure_escalates_to_page_not_silent(monkeypatch):
    # The bug: run_lane set fired="nudge" even when the nudge never reached the lane.
    # With the real lane_nudge.sh failing (bogus session), run_lane MUST page, not
    # report a silent nudge success.
    monkeypatch.setattr(w, "gather_owned_work", lambda cur, lane: _W)
    monkeypatch.setattr(w, "lane_idle", lambda cur, lane: (True, 1800.0))
    paged = []
    monkeypatch.setattr(w, "_page", lambda subject, body, dry: paged.append(subject))
    res = w.run_lane(_FakeCur(), "cc-bogus", lane_map={"cc-bogus": "no-such-tmux-iww"},
                     dry=False)
    assert paged, "nudge failed but run_lane sent no page (silent skip regression)"
    assert res["fired"] != "nudge"


def test_run_lane_dry_does_not_invoke_lane_nudge(monkeypatch):
    # dry-run stays observation-only: no subprocess, reports the intended nudge.
    monkeypatch.setattr(w, "gather_owned_work", lambda cur, lane: _W)
    monkeypatch.setattr(w, "lane_idle", lambda cur, lane: (True, 1800.0))
    res = w.run_lane(_FakeCur(), "cc-irsyad-coord",
                     lane_map={"cc-irsyad-coord": "irsyad-coord"}, dry=True)
    assert res["fired"] == "nudge"
