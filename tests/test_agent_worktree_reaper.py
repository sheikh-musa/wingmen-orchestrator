"""Tests for agent_worktree_reaper — the SAFETY-CRITICAL gate is `classify_worktree`:
a stale agent worktree is reaped ONLY when it is old AND clean AND idle. Every other
state (dirty / recent / no-git / in-use) MUST be SPARED. These tests lock that in."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import agent_worktree_reaper as r  # noqa: E402

DAY = 86400
MIN_AGE = 14 * DAY
NOW = 1_000_000_000.0
OLD = NOW - 30 * DAY   # 30d old -> past the age gate
RECENT = NOW - 2 * DAY  # 2d old -> within age gate


def test_reap_only_when_old_clean_idle():
    assert r.classify_worktree(has_git=True, mtime=OLD, now=NOW, min_age_s=MIN_AGE,
                               porcelain="", in_use=False) == "reap"


def test_dirty_is_spared():
    assert r.classify_worktree(has_git=True, mtime=OLD, now=NOW, min_age_s=MIN_AGE,
                               porcelain=" M src/app.ts\n", in_use=False) == "skip:dirty"


def test_untracked_only_is_still_dirty_and_spared():
    # porcelain shows untracked as '??' — must NOT be reaped (git remove would refuse anyway)
    assert r.classify_worktree(has_git=True, mtime=OLD, now=NOW, min_age_s=MIN_AGE,
                               porcelain="?? scratch.txt\n", in_use=False) == "skip:dirty"


def test_recent_is_spared_even_if_clean():
    assert r.classify_worktree(has_git=True, mtime=RECENT, now=NOW, min_age_s=MIN_AGE,
                               porcelain="", in_use=False) == "skip:recent"


def test_no_git_is_spared():
    assert r.classify_worktree(has_git=False, mtime=OLD, now=NOW, min_age_s=MIN_AGE,
                               porcelain="", in_use=False) == "skip:no-git"


def test_in_use_is_spared():
    assert r.classify_worktree(has_git=True, mtime=OLD, now=NOW, min_age_s=MIN_AGE,
                               porcelain="", in_use=True) == "skip:in-use"


def test_gate_order_no_git_beats_everything():
    # a stray dir (no .git) is unclear -> never touched, even if old
    assert r.classify_worktree(has_git=False, mtime=OLD, now=NOW, min_age_s=MIN_AGE,
                               porcelain=" M x\n", in_use=True) == "skip:no-git"


def test_boundary_exactly_min_age_is_reapable():
    # age == min_age is NOT < min_age -> old enough to reap (clean+idle)
    assert r.classify_worktree(has_git=True, mtime=NOW - MIN_AGE, now=NOW, min_age_s=MIN_AGE,
                               porcelain="", in_use=False) == "reap"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_find_agent_worktrees_only_matches_agent_dirs(tmp_path):
    """Discovery returns only `<root>/<project>/.claude/worktrees/agent-*` dirs — named
    worktrees (tg-*) and projects without a worktrees dir are ignored."""
    root = tmp_path / "wingmen"
    proj = root / "projects" / "ihsanos"
    wt = proj / ".claude" / "worktrees"
    wt.mkdir(parents=True)
    (wt / "agent-aaa").mkdir()
    (wt / "agent-bbb").mkdir()
    (wt / "tg-named-worktree").mkdir()   # NOT agent-* -> excluded
    (wt / "agent-note.txt").write_text("x")  # a FILE, not a dir -> excluded
    # a second project with no worktrees dir at all
    (root / "projects" / "empty").mkdir(parents=True)

    found = {p.name for p in r.find_agent_worktrees([root / "projects"])}
    assert found == {"agent-aaa", "agent-bbb"}


def test_scan_end_to_end_reaps_only_old_clean(tmp_path, monkeypatch):
    """Integration: a real git repo with three agent worktrees — old+clean, old+dirty,
    recent+clean — yields exactly ONE reap candidate (the old+clean one)."""
    root = tmp_path / "wingmen" / "projects"
    proj = root / "proj"
    proj.mkdir(parents=True)
    _git(proj, "init", "-q")
    _git(proj, "config", "user.email", "t@t")
    _git(proj, "config", "user.name", "t")
    (proj / "f.txt").write_text("hi")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "init")
    wt = proj / ".claude" / "worktrees"
    wt.mkdir(parents=True)

    def add_wt(name):
        _git(proj, "worktree", "add", "-q", "--detach", str(wt / name))

    # lsof is unavailable/unreliable in the sandbox and fail-SAFE returns True (spare);
    # pin it False here so the test exercises the age+clean gates deterministically.
    monkeypatch.setattr(r, "_lsof_in_use", lambda p: False)

    add_wt("agent-old-clean")
    add_wt("agent-old-dirty")
    add_wt("agent-recent-clean")
    # dirty one: leave an untracked file
    (wt / "agent-old-dirty" / "scratch.txt").write_text("wip")
    # age: backdate old ones past the gate, keep recent one fresh
    old_t = NOW_REAL = __import__("time").time() - 30 * DAY
    for n in ("agent-old-clean", "agent-old-dirty"):
        os.utime(wt / n, (old_t, old_t))

    # in_use always False in test (no lsof); min_age 14d
    summary = r.scan([root], __import__("time").time(), MIN_AGE)
    reap_names = {os.path.basename(c["path"]) for c in summary["reap"]}
    assert reap_names == {"agent-old-clean"}
    assert "skip:dirty" in summary["skips"]
    assert "skip:recent" in summary["skips"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
