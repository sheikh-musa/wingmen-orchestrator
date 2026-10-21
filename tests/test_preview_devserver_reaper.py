"""Unit tests for scripts/preview_devserver_reaper.py (orch-console #72892/#72894, held via
#72889/#72897 self-correction).

The reaper stops idle, unwatched `next dev`/`vite` preview servers (and clears their
regenerable .next/dist build cache) — it never touches anything else. These tests pin the
pure decision gate: busy (established connections) always wins, then idle-grace, then a
registered lane's own live pane as a belt-and-suspenders spare. No real process/lsof/tmux
dependency for the pure-logic tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import preview_devserver_reaper as pr  # noqa: E402


# ---- pure classify ------------------------------------------------------------
def test_classify_busy_established_conn_spares():
    assert pr.classify(established_count=1, grace_elapsed=True, lane_pane_busy=False) == "skip:busy"


def test_classify_busy_wins_even_if_grace_elapsed_and_pane_idle():
    assert pr.classify(established_count=3, grace_elapsed=True, lane_pane_busy=False) == "skip:busy"


def test_classify_idle_but_grace_not_elapsed_spares():
    assert pr.classify(established_count=0, grace_elapsed=False, lane_pane_busy=False) == "skip:grace"


def test_classify_idle_grace_elapsed_lane_pane_busy_spares():
    # registered lane's own pane is mid-turn — don't yank its tool out from under it
    assert pr.classify(established_count=0, grace_elapsed=True, lane_pane_busy=True) == "skip:lane-busy"


def test_classify_idle_grace_elapsed_no_lane_pane_reaps():
    assert pr.classify(established_count=0, grace_elapsed=True, lane_pane_busy=False) == "reap"


def test_classify_no_registered_lane_means_pane_busy_is_false_and_reaps():
    # a clean orphan (no fleet_lanes row at all) has no pane to check — treated as not-busy
    assert pr.classify(established_count=0, grace_elapsed=True, lane_pane_busy=False) == "reap"


# ---- update_grace_state (pure, persists idle-start timestamps) ---------------
def test_grace_busy_resets_and_removes_key():
    state = {"8:3200": 100.0}
    elapsed, new_state = pr.update_grace_state(state, "8:3200", now=500.0,
                                                established_count=1, idle_grace_s=1200)
    assert elapsed is False
    assert "8:3200" not in new_state


def test_grace_first_seen_idle_records_timestamp_not_yet_elapsed():
    state = {}
    elapsed, new_state = pr.update_grace_state(state, "8:3200", now=1000.0,
                                                established_count=0, idle_grace_s=1200)
    assert elapsed is False
    assert new_state["8:3200"] == 1000.0


def test_grace_elapsed_once_window_passes():
    state = {"8:3200": 1000.0}
    elapsed, new_state = pr.update_grace_state(state, "8:3200", now=1000.0 + 1200.0,
                                                established_count=0, idle_grace_s=1200)
    assert elapsed is True
    assert new_state["8:3200"] == 1000.0  # unchanged — grace is measured from first-seen


def test_grace_not_yet_elapsed_before_window():
    state = {"8:3200": 1000.0}
    elapsed, new_state = pr.update_grace_state(state, "8:3200", now=1000.0 + 600.0,
                                                established_count=0, idle_grace_s=1200)
    assert elapsed is False
    assert new_state["8:3200"] == 1000.0


# ---- dev-server pattern matching (pure) ---------------------------------------
def test_matches_dev_server_pattern_next_dev():
    assert pr._matches_dev_server_pattern(
        "node /x/node_modules/.bin/next dev -p 3200", pr.DEV_SERVER_PATTERNS) is True


def test_matches_dev_server_pattern_next_server():
    assert pr._matches_dev_server_pattern("next-server (v16.2.10) ", pr.DEV_SERVER_PATTERNS) is True


def test_matches_dev_server_pattern_unrelated_process():
    assert pr._matches_dev_server_pattern("node server.js", pr.DEV_SERVER_PATTERNS) is False


# ---- _established_count (I/O via lsof, fail-safe) -----------------------------
class _FakeRun:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_established_count_zero_when_lsof_finds_nothing(monkeypatch):
    def fake_run(*a, **k):
        return _FakeRun(1, "")  # lsof exit 1 = no matches, not an error
    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    assert pr._established_count(3200) == 0


def test_established_count_counts_matching_lines(monkeypatch):
    def fake_run(*a, **k):
        return _FakeRun(0, "p12345\nf10\np12346\nf11\n")
    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    assert pr._established_count(3200) == 2


def test_established_count_failsafe_on_lsof_error(monkeypatch):
    # fail-safe: an unverifiable read must NEVER be treated as idle — report busy (>0)
    def boom(*a, **k):
        raise OSError("lsof gone")
    monkeypatch.setattr(pr.subprocess, "run", boom)
    assert pr._established_count(3200) > 0


# ---- _pane_busy (I/O via tmux, fail-safe) --------------------------------------
def test_pane_busy_true_when_footer_present(monkeypatch):
    def fake_run(*a, **k):
        return _FakeRun(0, "some output\n  esc to interrupt\n")
    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    assert pr._pane_busy("cosem-exams") is True


def test_pane_busy_false_when_footer_absent(monkeypatch):
    def fake_run(*a, **k):
        return _FakeRun(0, "idle output\n> \n")
    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    assert pr._pane_busy("cosem-exams") is False


def test_pane_busy_false_when_no_such_session(monkeypatch):
    # tmux exits non-zero with "no such session" — not a live pane to worry about
    def fake_run(*a, **k):
        return _FakeRun(1, "")
    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    assert pr._pane_busy("ghost-session") is False


def test_pane_busy_failsafe_true_on_unexpected_error(monkeypatch):
    # fail-safe: an unverifiable pane read must NEVER be treated as idle
    def boom(*a, **k):
        raise OSError("tmux gone")
    monkeypatch.setattr(pr.subprocess, "run", boom)
    assert pr._pane_busy("cosem-exams") is True


# ---- _parse_ps_output (pure) ---------------------------------------------------
def test_parse_ps_output_extracts_pid_and_command():
    text = (
        "  PID COMMAND\n"
        "99528 next-server (v16.2.10)\n"
        "99526 node /x/node_modules/.bin/next dev -p 3200\n"
    )
    rows = pr._parse_ps_output(text)
    assert rows == [
        (99528, "next-server (v16.2.10)"),
        (99526, "node /x/node_modules/.bin/next dev -p 3200"),
    ]


def test_parse_ps_output_skips_unparseable_lines():
    text = "  PID COMMAND\nnotanumber some command\n"
    assert pr._parse_ps_output(text) == []


# ---- _dir_size / reclaim (reused shape from worktree_nodemodules_reaper) ------
def test_dir_size_sums_file_bytes(tmp_path):
    d = tmp_path / "next"
    d.mkdir()
    (d / "a").write_bytes(b"x" * 10)
    (d / "b").write_bytes(b"y" * 5)
    assert pr._dir_size(d) == 15


def test_find_reclaimable_dirs_finds_next_and_dist(tmp_path):
    wt = tmp_path / "lane"
    (wt / ".next" / "dev").mkdir(parents=True)
    (wt / "dist").mkdir(parents=True)
    (wt / "src").mkdir(parents=True)
    found = {p.name for p in pr.find_reclaimable_dirs(wt)}
    assert found == {".next", "dist"}
