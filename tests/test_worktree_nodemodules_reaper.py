"""Unit tests for scripts/worktree_nodemodules_reaper.py (orch-console #61047).

The reaper clears ONLY gitignored node_modules/.next from idle (no-live-cwd) PR worktrees —
reversible, git state never touched. These tests pin the safety gates: no-git skip, in-use
spare, kill-time re-verify, NOT-ignored spare (never clobber tracked), and dry-run no-op.
No DB, no real lsof/git dependency except the real-git fixture for the ignore gate.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import worktree_nodemodules_reaper as nm  # noqa: E402


# ---- pure classify ------------------------------------------------------------
def test_classify_no_git():
    assert nm.classify(has_git=False, in_use=False, recently_active=False) == "skip:no-git"


def test_classify_in_use_spared():
    assert nm.classify(has_git=True, in_use=True, recently_active=False) == "skip:in-use"


def test_classify_idle_clears():
    assert nm.classify(has_git=True, in_use=False, recently_active=False) == "clear"


def test_classify_in_use_beats_clear_even_with_git():
    # in-use gate must win (safety) when both git present and in-use
    assert nm.classify(has_git=True, in_use=True, recently_active=False) == "skip:in-use"


def test_classify_recently_active_spared():
    # a recent build in the tree spares it entirely (#61165 cross-worktree build)
    assert nm.classify(has_git=True, in_use=False, recently_active=True) == "skip:active"


def test_tree_recently_active_fresh_root(tmp_path):
    wt = tmp_path / "p.wt-x"
    (wt / "node_modules").mkdir(parents=True)  # fresh
    assert nm._tree_recently_active(wt, 3600) is True


def test_tree_recently_active_old_is_idle(tmp_path):
    import os as _os
    wt = tmp_path / "p.wt-x"
    nmd = wt / "node_modules"
    nmd.mkdir(parents=True)
    old = __import__("time").time() - 7200  # 2h old
    _os.utime(nmd, (old, old))
    assert nm._tree_recently_active(wt, 3600) is False  # older than 1h window → idle


def test_tree_recently_active_catches_cross_worktree_build_dir(tmp_path):
    # the #61165 shape: build happens in apps/<x>/ — a fresh apps/*/node_modules must count
    wt = tmp_path / "p.wt-tablet"
    appnm = wt / "apps" / "tablet-onboarding" / "node_modules"
    appnm.mkdir(parents=True)  # fresh
    assert nm._tree_recently_active(wt, 3600) is True


def test_tree_recently_active_failsafe_on_stat_error(tmp_path, monkeypatch):
    wt = tmp_path / "p.wt-x"
    (wt / "node_modules").mkdir(parents=True)

    import pathlib as _pl
    real_stat = _pl.Path.stat

    def boom(self, *a, **k):
        if self.name == "node_modules":
            raise OSError("stat gone")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(_pl.Path, "stat", boom)
    assert nm._tree_recently_active(wt, 3600) is True  # stat error → assume active → spare


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


@pytest.fixture
def idle_project(monkeypatch):
    """Neutralize the recent-activity gate so the ignore/kill-time logic can be exercised
    directly: treat the tree as having NO recent build (the fixture's dirs are fresh)."""
    monkeypatch.setattr(nm, "_tree_recently_active", lambda wt, s, **k: False)


def test_reap_one_dry_run_touches_nothing(git_worktree, idle_project, monkeypatch):
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(git_worktree, dry_run=True)
    assert rep["verdict"] == "clear"
    assert rep["freed"] > 0
    # dirs still present after dry-run
    assert (git_worktree / "node_modules").is_dir()
    assert (git_worktree / ".next").is_dir()


def test_reap_one_apply_clears_ignored_spares_tracked(git_worktree, idle_project, monkeypatch):
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


@pytest.fixture
def symlinked_worktree(tmp_path):
    """A real git worktree whose node_modules is a SYMLINK into a sibling worktree's real
    node_modules (the cosem-platform.wt-editor34048 shape, bus #77680)."""
    sibling_nm = tmp_path / "proj.wt-sibling" / "node_modules"
    sibling_nm.mkdir(parents=True)
    (sibling_nm / "dep.js").write_bytes(b"z" * 8192)
    wt = tmp_path / "proj.wt-linked"
    wt.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wt, check=True)
    (wt / ".gitignore").write_text("node_modules\n")   # no trailing slash: matches a symlink
    subprocess.run(["git", "add", ".gitignore"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=wt, check=True)
    (wt / "node_modules").symlink_to(sibling_nm)
    return wt, sibling_nm


def test_reap_one_unlinks_symlinked_node_modules_never_touches_target(
        symlinked_worktree, idle_project, monkeypatch):
    wt, sibling_nm = symlinked_worktree
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(wt, dry_run=False)
    assert rep["errors"] == []
    assert not os.path.lexists(wt / "node_modules")       # the link is gone
    assert (sibling_nm / "dep.js").exists()               # the sibling's real tree is intact
    assert rep["freed"] == 0                              # a link frees nothing here
    assert rep["cleared"] == [{"dir": str(wt / "node_modules"), "bytes": 0}]


def test_reap_one_dry_run_leaves_symlink_and_reports_zero_bytes(
        symlinked_worktree, idle_project, monkeypatch):
    wt, sibling_nm = symlinked_worktree
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(wt, dry_run=True)
    assert (wt / "node_modules").is_symlink()
    assert rep["freed"] == 0


def test_reap_one_in_use_spares_everything(git_worktree, monkeypatch):
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: True)
    rep = nm.reap_one(git_worktree, dry_run=False)
    assert rep["verdict"] == "skip:in-use"
    assert rep["freed"] == 0
    assert (git_worktree / "node_modules").is_dir()  # untouched


def test_reap_one_kill_time_live_aborts(git_worktree, idle_project, monkeypatch):
    # idle at top-level scan, then goes LIVE right before the rm → must spare + mark skip
    calls = {"n": 0}

    def fake_lsof(p):
        calls["n"] += 1
        return calls["n"] > 1  # first call (top scan) idle; subsequent (kill-time) live

    monkeypatch.setattr(nm, "_lsof_cwd_in_use", fake_lsof)
    rep = nm.reap_one(git_worktree, dry_run=False)
    assert rep["verdict"] == "skip:in-use(kill-time)"
    assert (git_worktree / "node_modules").is_dir()  # nothing deleted


def test_reap_one_recent_build_spares_whole_tree(git_worktree, monkeypatch):
    # #61165/#61169: a recent build in the tree spares it ENTIRELY (cross-worktree build case).
    # The fixture's node_modules/.next are freshly created → recent-activity gate fires for real.
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(git_worktree, dry_run=False)
    assert rep["verdict"] == "skip:active"
    assert rep["freed"] == 0
    assert (git_worktree / "node_modules").is_dir()  # untouched — fresh build
    assert (git_worktree / ".next").is_dir()


def test_reap_one_idle_tree_still_clears(git_worktree, idle_project, monkeypatch):
    # when the tree is NOT recently active, the reaper still reclaims (the fix doesn't over-spare)
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(git_worktree, dry_run=False)
    assert rep["verdict"] == "clear"
    assert not (git_worktree / "node_modules").exists()
    assert not (git_worktree / ".next").exists()


def test_is_ignored_failsafe_on_error(monkeypatch, tmp_path):
    # git check-ignore raising → _is_ignored returns False (spare), never True
    def boom(*a, **k):
        raise OSError("git gone")

    monkeypatch.setattr(nm.subprocess, "run", boom)
    assert nm._is_ignored(tmp_path, tmp_path / "node_modules") is False


# ---- _page actually calls bus_send.send() with its REAL signature --------------
def test_page_calls_bus_send_with_correct_kwargs(monkeypatch):
    # bus_send.send()'s real signature is (from_agent, to, mtype, subject, body, priority,
    # req=..., ...) -- a caller passing type=/requires_response= raises TypeError, caught
    # by the except-Exception fallback, which only LOGS (never truly pages) -- the
    # dead-man's-switch silently never lands. Stub matches the real signature exactly.
    calls = []

    def fake_send(from_agent, to, mtype, subject, body, priority, req=False, **_kw):
        calls.append(dict(from_agent=from_agent, to=to, mtype=mtype, priority=priority, req=req))

    class _FakeBusSend:
        send = staticmethod(fake_send)

    monkeypatch.setitem(sys.modules, "scripts.bus_send", _FakeBusSend)
    monkeypatch.setitem(sys.modules, "bus_send", _FakeBusSend)
    # see the matching comment in test_preview_devserver_reaper.py: `from scripts import
    # bus_send` resolves via getattr on an already-imported `scripts` package object if one
    # exists (true in a full-suite run), which the sys.modules patches alone can't override.
    if "scripts" in sys.modules:
        monkeypatch.setattr(sys.modules["scripts"], "bus_send", _FakeBusSend, raising=False)

    nm._page({"errors": ["rm failed /x: boom"]})
    assert len(calls) == 1
    assert calls[0]["to"] == "orch-console"
    assert calls[0]["from_agent"] == "cc-fleet-health"


@pytest.fixture
def tracked_symlink_worktree(tmp_path):
    """cosem-adcda shape (orch-console #80423): ops/node_modules is a COMMITTED symlink
    (git mode 120000) to ../functions/node_modules, by design (PR486)."""
    wt = tmp_path / "proj.wt-tracked-link"
    (wt / "functions" / "node_modules").mkdir(parents=True)
    (wt / "functions" / "node_modules" / "dep.js").write_bytes(b"z" * 1024)
    (wt / "ops").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wt, check=True)
    (wt / ".gitignore").write_text("functions/node_modules/\n")
    (wt / "ops" / "node_modules").symlink_to("../functions/node_modules")
    subprocess.run(["git", "add", ".gitignore", "ops/node_modules"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=wt, check=True)
    return wt


def test_tracked_symlink_spared_silently_80423(tracked_symlink_worktree, idle_project, monkeypatch):
    wt = tracked_symlink_worktree
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(wt, dry_run=False)
    assert (wt / "ops" / "node_modules").is_symlink()        # the tracked link is untouched
    assert not any("ops/node_modules" in e for e in rep["errors"])   # and NOT paged
    assert str(wt / "ops" / "node_modules") in rep["spared"]
    # the real (ignored) dir behind it is still reclaimed as usual
    assert not (wt / "functions" / "node_modules").exists()
    status = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_untracked_unignored_symlink_still_pages(tmp_path, idle_project, monkeypatch):
    """Only a TRACKED link is expected; an untracked, un-ignored link is still surfaced."""
    target = tmp_path / "elsewhere"
    target.mkdir()
    wt = tmp_path / "proj.wt-odd-link"
    wt.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    (wt / "node_modules").symlink_to(target)
    monkeypatch.setattr(nm, "_lsof_cwd_in_use", lambda p: False)
    rep = nm.reap_one(wt, dry_run=False)
    assert (wt / "node_modules").is_symlink()
    assert any("NOT-IGNORED" in e for e in rep["errors"])
