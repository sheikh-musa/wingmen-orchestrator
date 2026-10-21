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


# ---- gate_registered_reap (pure; orch-console #72902 guardrail) ---------------
def test_gate_orphan_always_proceeds_unconfirmed():
    # no registered lane at all -> nothing to confirm, orphan path is fully automatic
    assert pr.gate_registered_reap(lane=None, confirmed=False) == "proceed"


def test_gate_orphan_always_proceeds_confirmed():
    assert pr.gate_registered_reap(lane=None, confirmed=True) == "proceed"


def test_gate_registered_lane_unconfirmed_flags_first_time():
    assert pr.gate_registered_reap(lane="cosem-exams", confirmed=False) == "flag-first-time"


def test_gate_registered_lane_confirmed_proceeds():
    assert pr.gate_registered_reap(lane="cosem-exams", confirmed=True) == "proceed"


# ---- reap() orchestration: wires gate_registered_reap in, never kills on flag ----
def test_reap_flags_registered_lane_first_time_without_killing(tmp_path, monkeypatch):
    def boom(pid):
        raise AssertionError("must NOT kill on flag-first-time")
    flagged = []
    monkeypatch.setattr(pr, "_kill_process", boom)
    monkeypatch.setattr(pr, "_flag_registered_reap", lambda r: flagged.append(r))
    wt = tmp_path / "lane"
    (wt / ".next").mkdir(parents=True)
    reports = [{"pid": 1, "port": 3200, "worktree": str(wt), "command": "next dev",
                "lane": "cosem-exams", "established": 0, "verdict": "reap", "would_free": 0}]
    out = pr.reap(reports, confirmed=False)
    assert len(out) == 1
    assert out[0]["action"] == "flagged-pending-confirm"
    assert out[0]["freed_bytes"] == 0
    assert (wt / ".next").exists()  # untouched
    assert len(flagged) == 1


def test_reap_proceeds_for_orphan_even_when_unconfirmed(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(pr, "_kill_process", lambda pid: killed.append(pid) or True)
    wt = tmp_path / "lane"
    (wt / ".next").mkdir(parents=True)
    (wt / ".next" / "f").write_bytes(b"x" * 10)
    reports = [{"pid": 1, "port": 3200, "worktree": str(wt), "command": "next dev",
                "lane": None, "established": 0, "verdict": "reap", "would_free": 10}]
    out = pr.reap(reports, confirmed=False)
    assert killed == [1]
    assert out[0]["action"] == "reaped"
    assert out[0]["freed_bytes"] == 10
    assert not (wt / ".next").exists()


def test_reap_proceeds_for_registered_lane_once_confirmed(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(pr, "_kill_process", lambda pid: killed.append(pid) or True)
    wt = tmp_path / "lane"
    (wt / ".next").mkdir(parents=True)
    reports = [{"pid": 1, "port": 3200, "worktree": str(wt), "command": "next dev",
                "lane": "cosem-exams", "established": 0, "verdict": "reap", "would_free": 0}]
    out = pr.reap(reports, confirmed=True)
    assert killed == [1]
    assert out[0]["action"] == "reaped"


# ---- _flag_registered_reap actually calls bus_send.send() with its REAL signature -----
def test_flag_registered_reap_calls_bus_send_with_correct_kwargs(monkeypatch):
    # reproduces a production failure: send()'s real signature is (from_agent, to, mtype,
    # subject, body, priority, req=..., ...) -- a caller passing type=/requires_response=
    # raises TypeError, caught by the except-Exception fallback, which only LOGS (never
    # truly pages) -- so the "dead-man's-switch" page silently never lands. Stub matches
    # the real signature exactly so a wrong kwarg name fails this test the same way it
    # failed in prod.
    calls = []

    def fake_send(from_agent, to, mtype, subject, body, priority, req=False, **_kw):
        calls.append(dict(from_agent=from_agent, to=to, mtype=mtype, subject=subject,
                          body=body, priority=priority, req=req))

    class _FakeBusSend:
        send = staticmethod(fake_send)

    import sys
    monkeypatch.setitem(sys.modules, "scripts.bus_send", _FakeBusSend)
    monkeypatch.setitem(sys.modules, "bus_send", _FakeBusSend)
    # `from scripts import bus_send` resolves via getattr on the ALREADY-IMPORTED `scripts`
    # package object if one exists in this process (a full-suite run very likely has one,
    # since some other test file legitimately imports the real scripts.bus_send first) —
    # the sys.modules patches above alone do not override that cached attribute. Patch it
    # directly too so this test is correct regardless of what else has already run.
    if "scripts" in sys.modules:
        monkeypatch.setattr(sys.modules["scripts"], "bus_send", _FakeBusSend, raising=False)

    pr._flag_registered_reap({"pid": 1, "port": 3200, "worktree": "/x/lane",
                              "lane": "cosem-exams", "command": "next dev"})
    assert len(calls) == 1
    assert calls[0]["to"] == "orch-console"
    assert calls[0]["from_agent"] == "cc-fleet-health"
    assert calls[0]["priority"] == "P2"
