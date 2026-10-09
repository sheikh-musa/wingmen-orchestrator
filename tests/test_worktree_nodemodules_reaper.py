"""Unit tests for scripts/worktree_nodemodules_reaper.py (orch-console #61047).

The reaper clears ONLY gitignored node_modules/.next from idle (no-live-cwd) PR worktrees —
reversible, git state never touched. These tests pin the safety gates: no-git skip, in-use
spare, kill-time re-verify, NOT-ignored spare (never clobber tracked), and dry-run no-op.
No DB, no real lsof/git dependency except the real-git fixture for the ignore gate.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import worktree_nodemodules_reaper as nm  # noqa: E402


# ---- pure classify ------------------------------------------------------------
def test_classify_no_git():
    assert nm.classify(has_git=False, in_use=False) == "skip:no-git"


def test_classify_in_use_spared():
    assert nm.classify(has_git=True, in_use=True) == "skip:in-use"


def test_classify_idle_clears():
    assert nm.classify(has_git=True, in_use=False) == "clear"


def test_classify_in_use_beats_clear_even_with_git():
    # in-use gate must win (safety) when both git present and in-use
    assert nm.classify(has_git=True, in_use=True) == "skip:in-use"


# ---- find_family_worktrees ----------------------------------------------------
def test_find_family_worktrees_glob_and_dedupe(tmp_path):
    (tmp_path / "proj.wt-a").mkdir()
    (tmp_path / "proj.wt-b").mkdir()
    (tmp_path / "proj.wt-c-file").write_text("not a dir")  # non-dir skipped
    (tmp_path / "other").mkdir()  # doesn't match glob
    pat = str(tmp_path / "proj.wt-*")
    got = {p.name for p in nm.find_family_worktrees([pat], home=str(tmp_path))}
    assert got == {"proj.wt-a", "proj.wt-b"}


# ---- find_reclaimable_dirs (prune) --------------------------------------------
def test_find_reclaimable_dirs_prunes_nested(tmp_path):
    wt = tmp_path / "wt"
    (wt / "node_modules" / "pkg" / "node_modules").mkdir(parents=True)  # nested must be pruned
    (wt / "apps" / "web" / "node_modules").mkdir(parents=True)
    (wt / ".next").mkdir()
    (wt / "src").mkdir()
    found = {str(d.relative_to(wt)) for d in nm.find_reclaimable_dirs(wt)}
    # top-level node_modules + apps/web/node_modules + .next; NOT the nested one inside node_modules
    assert found == {"node_modules", "apps/web/node_modules", ".next"}


# ---- real-git fixture: ignore gate + apply + dry-run --------------------------
@pytest.fixture
def git_worktree(tmp_path):
    """A real git repo with gitignored node_modules and a TRACKED fake-node_modules dir."""
    wt = tmp_path / "proj.wt-x"
    wt.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wt, check=True)
    (wt / ".gitignore").write_text("node_modules/\n.next/\n")
    # gitignored, reclaimable
    nmdir = wt / "node_modules"
    nmdir.mkdir()
    (nmdir / "big.bin").write_bytes(b"x" * 4096)
    nxt = wt / ".next"
    nxt.mkdir()
    (nxt / "cache.bin").write_bytes(b"y" * 2048)
    # a TRACKED dir that happens to be named node_modules under a NOT-ignored path:
    # simulate a pathological committed node_modules — must be SPARED.
    tracked = wt / "vendor" / "node_modules"
    tracked.mkdir(parents=True)
    (tracked / "committed.js").write_text("console.log(1)")
    # vendor/node_modules is NOT matched by the root "node_modules/" ignore (git anchors it to
    # the dir where .gitignore lives only when pattern has no slash prefix... actually
    # "node_modules/" matches at any depth). Force-add to make it tracked regardless of ignore.
    subprocess.run(["git", "add", "-f", ".gitignore", "vendor/node_modules/committed.js"],
                   cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=wt, check=True)
    return wt


def test_reap_one_dry_run_touches_nothing(git_worktree, monkeypatch):
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(git_worktree, dry_run=True)
    assert rep["verdict"] == "clear"
    assert rep["freed"] > 0
    # dirs still present after dry-run
    assert (git_worktree / "node_modules").is_dir()
    assert (git_worktree / ".next").is_dir()


def test_reap_one_apply_clears_ignored_spares_tracked(git_worktree, monkeypatch):
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(git_worktree, dry_run=False)
    # ignored node_modules + .next removed
    assert not (git_worktree / "node_modules").exists()
    assert not (git_worktree / ".next").exists()
    # tracked vendor/node_modules SPARED (never clobber tracked), reported as error/spared
    assert (git_worktree / "vendor" / "node_modules" / "committed.js").exists()
    assert any("NOT-IGNORED" in e for e in rep["errors"])
    # git tree still clean (we only removed ignored files)
    status = subprocess.run(["git", "-C", str(git_worktree), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_reap_one_in_use_spares_everything(git_worktree, monkeypatch):
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: True)
    rep = nm.reap_one(git_worktree, dry_run=False)
    assert rep["verdict"] == "skip:in-use"
    assert rep["freed"] == 0
    assert (git_worktree / "node_modules").is_dir()  # untouched


def test_reap_one_kill_time_live_aborts(git_worktree, monkeypatch):
    # idle at top-level scan, then goes LIVE right before the rm → must spare + mark skip
    calls = {"n": 0}

    def fake_lsof(p):
        calls["n"] += 1
        return calls["n"] > 1  # first call (top scan) idle; subsequent (kill-time) live

    monkeypatch.setattr(nm, "_lsof_cwd_in_use", fake_lsof)
    rep = nm.reap_one(git_worktree, dry_run=False)
    assert rep["verdict"] == "skip:in-use(kill-time)"
    assert (git_worktree / "node_modules").is_dir()  # nothing deleted


def test_is_ignored_failsafe_on_error(monkeypatch, tmp_path):
    # git check-ignore raising → _is_ignored returns False (spare), never True
    def boom(*a, **k):
        raise OSError("git gone")

    monkeypatch.setattr(nm.subprocess, "run", boom)
    assert nm._is_ignored(tmp_path, tmp_path / "node_modules") is False
