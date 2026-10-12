"""test_deploy_safety_guard.py — the fail-closed pre-deploy guard for
deploy_ihsanos_prod.sh (op#29023 / orch-console #63449).

Exercises the REAL guard (scripts/lib/deploy_safety_guard.sh) against REAL
scratch git repos (git init + a bare 'origin'), not a mocked helper. The only
stub is the Vercel link state — a .vercel/project.json file written to the
scratch tree (we cannot hit real Vercel). The four refusals (a dirty tree, a
wrong branch, a tree behind origin/main, a wrong linked project) and the happy
path are all driven through the shipping script.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

_GUARD = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lib" / "deploy_safety_guard.sh"
# The intended target for these tests.
PROJ = "prj_REAL_ihsanos_irsyad"
ORG = "team_wingmen_aa9356e1"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(tmp_path, *, branch="main", dirty=False, behind=False,
               vercel=("prj_REAL_ihsanos_irsyad", "team_wingmen_aa9356e1")):
    """A real scratch repo cloned from a real bare 'origin', positioned per the flags."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "t@t"); _git(work, "config", "user.name", "t")
    (work / "app.txt").write_text("v1\n")
    # .vercel/ is gitignored in the real ihsanos repo (Vercel standard), so a linked
    # project.json must NOT count as a dirty working tree — mirror that here.
    (work / ".gitignore").write_text(".vercel/\n")
    _git(work, "add", "-A"); _git(work, "commit", "-m", "base")
    _git(work, "push", "origin", "main")
    # advance origin past the work tree -> work is "behind"
    if behind:
        clone2 = tmp_path / "clone2"
        subprocess.run(["git", "clone", str(origin), str(clone2)], check=True, capture_output=True)
        _git(clone2, "config", "user.email", "t@t"); _git(clone2, "config", "user.name", "t")
        (clone2 / "app.txt").write_text("v2\n")
        _git(clone2, "add", "-A"); _git(clone2, "commit", "-m", "ahead"); _git(clone2, "push", "origin", "main")
    if branch != "main":
        _git(work, "checkout", "-b", branch)
    if dirty:
        (work / "wip-untracked.txt").write_text("WIP\n")  # untracked must also trip the guard
    if vercel is not None:
        vdir = work / ".vercel"; vdir.mkdir()
        (vdir / "project.json").write_text(json.dumps({"projectId": vercel[0], "orgId": vercel[1]}))
    return work


def _run(work):
    script = f'source "{_GUARD}"; _deploy_safety_guard "$1" "$2" "$3"'
    return subprocess.run(["bash", "-c", script, "bash", str(work), PROJ, ORG],
                          capture_output=True, text=True, timeout=30)


def test_clean_on_main_uptodate_correct_project_PASSES(tmp_path):
    r = _run(_make_repo(tmp_path))
    assert r.returncode == 0, r.stderr


def test_dirty_tree_REFUSES(tmp_path):
    r = _run(_make_repo(tmp_path, dirty=True))
    assert r.returncode != 0
    assert "DIRTY" in r.stderr


def test_wrong_branch_REFUSES(tmp_path):
    r = _run(_make_repo(tmp_path, branch="feat/x"))
    assert r.returncode != 0
    assert "not 'main'" in r.stderr or "not main" in r.stderr.lower()


def test_behind_origin_main_REFUSES(tmp_path):
    r = _run(_make_repo(tmp_path, behind=True))
    assert r.returncode != 0
    assert "origin/main" in r.stderr


def test_wrong_linked_project_REFUSES(tmp_path):
    r = _run(_make_repo(tmp_path, vercel=("prj_SOMETHING_ELSE", ORG)))
    assert r.returncode != 0
    assert "project" in r.stderr.lower()


def test_no_vercel_link_REFUSES(tmp_path):
    r = _run(_make_repo(tmp_path, vercel=None))
    assert r.returncode != 0
