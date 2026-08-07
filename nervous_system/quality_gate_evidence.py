"""Quality Gate evidence gatherer — the GIT-DERIVED slice (Head of Quality, Phase 2b).

Head of Quality, Phase 2b (SHADOW, read-only). See reports/head-of-quality-spec.md §8.

The Phase 2 evaluator (`quality_gate.py`) is a PURE function over an evidence dict
`{sha, checks, reviews}`; it does not gather anything itself. This module gathers
the small subset of that evidence whose truth is UNAMBIGUOUS from `git` alone, for
a given working directory, and returns it in the `{sha, checks}` shape the
evaluator consumes.

WHAT IT GATHERS (and ONLY this — three checks whose semantics git settles fully):
  - committed-on-branch      (G10): clean working tree AND HEAD on a named branch
  - migrations-tracked       (G8):  nothing untracked/modified under migrations/
  - ancestor-of-origin-main  (G7):  origin/main is an ancestor of HEAD

WHAT IT DELIBERATELY OMITS — honest dead-man default:
  CI status, secret/PII leak scans, deploy provenance, i18n parity, screenshot
  manifests, reviewer arms — NONE are derivable from git alone, so this module
  leaves them ABSENT. An absent check surfaces at the evaluator as `unproven`
  (fail-strict), which is the CORRECT honest behaviour: absence of proof is never
  a pass. This module NEVER fabricates a pass for something it cannot observe.

CONTRACT (this phase):
  - READ-ONLY. Pure `git` subprocess READS against a passed-in `cwd`. No writes,
    no network, no fetch, no config mutation, no side effects.
  - TOTAL. A non-git dir, a detached HEAD, or a missing origin/main never raises;
    the gatherer returns what it can honestly determine and leaves the rest absent.
  - It does NOT evaluate, block, merge, or deploy. It only produces evidence.
"""

from __future__ import annotations

import subprocess
from typing import Optional

# Manifest check-name constants (docs/ihsan-gate-manifest.yaml). Keeping them as
# named constants keeps this module honest about which manifest checks it feeds.
CHECK_COMMITTED_ON_BRANCH = "committed-on-branch"     # G10
CHECK_MIGRATIONS_TRACKED = "migrations-tracked"       # G8
CHECK_ANCESTOR_OF_ORIGIN_MAIN = "ancestor-of-origin-main"  # G7

PASS = "pass"
FAIL = "fail"

# Subprocess guardrails: short timeout, never inherit a tty, capture everything.
_GIT_TIMEOUT = 15


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    """Run a read-only git command in `cwd`. Returns the CompletedProcess (rc in
    .returncode); NEVER raises on a non-zero git exit — callers branch on rc.
    A missing git binary / OS error is swallowed into a synthetic rc=127 so the
    gatherer stays total (a broken environment yields absent checks, not a crash).
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(args=["git", *args], returncode=127,
                                           stdout="", stderr="")


def is_git_repo(cwd: str) -> bool:
    """True iff `cwd` is inside a git work tree."""
    r = _git(cwd, "rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def head_sha(cwd: str) -> str:
    """`git rev-parse HEAD`, or "" if it cannot be resolved (non-git / no commits)."""
    r = _git(cwd, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def _on_named_branch(cwd: str) -> bool:
    """True iff HEAD is a symbolic ref to a branch (i.e. not detached)."""
    return _git(cwd, "symbolic-ref", "--quiet", "HEAD").returncode == 0


def _working_tree_clean(cwd: str) -> bool:
    """True iff `git status --porcelain` is empty (no staged/unstaged/untracked)."""
    r = _git(cwd, "status", "--porcelain")
    return r.returncode == 0 and r.stdout.strip() == ""


def check_committed_on_branch(cwd: str) -> Optional[str]:
    """G10: clean working tree AND on a named branch -> "pass" else "fail".
    Absent (None) only when `cwd` is not a git repo."""
    if not is_git_repo(cwd):
        return None
    return PASS if (_working_tree_clean(cwd) and _on_named_branch(cwd)) else FAIL


def check_migrations_tracked(cwd: str) -> Optional[str]:
    """G8: nothing untracked OR modified under migrations/ -> "pass" else "fail".
    No migrations/ dir at all is vacuously "pass" (nothing untracked there).
    Absent (None) only when `cwd` is not a git repo."""
    if not is_git_repo(cwd):
        return None
    r = _git(cwd, "status", "--porcelain", "--", "migrations/")
    if r.returncode != 0:
        return None
    return PASS if r.stdout.strip() == "" else FAIL


def check_ancestor_of_origin_main(cwd: str) -> Optional[str]:
    """G7: origin/main is an ancestor of HEAD -> "pass" else "fail".
    Absent (None) when `cwd` is not a git repo OR origin/main does not resolve —
    we cannot honestly judge "current on main" without the ref, so we say nothing
    (the evaluator marks it unproven) rather than guess."""
    if not is_git_repo(cwd):
        return None
    # origin/main must resolve, else the answer is genuinely unknown -> absent.
    if _git(cwd, "rev-parse", "--verify", "--quiet", "origin/main").returncode != 0:
        return None
    if not head_sha(cwd):
        return None
    r = _git(cwd, "merge-base", "--is-ancestor", "origin/main", "HEAD")
    if r.returncode == 0:
        return PASS
    if r.returncode == 1:      # git's documented "not an ancestor" exit
        return FAIL
    return None                # any other rc (e.g. 128) -> undeterminable -> absent


def gather(cwd: str) -> dict:
    """Gather the git-derivable evidence for the repo at `cwd`.

    Returns `{"sha": <HEAD sha or "">, "checks": {name: "pass"|"fail"}}` with
    ONLY the checks git can honestly settle present; undeterminable checks are
    omitted so the evaluator surfaces them as `unproven`. A non-git dir yields
    `{"sha": "", "checks": {}}`. Pure read — no writes, no network.
    """
    checks: dict = {}
    for name, fn in (
        (CHECK_COMMITTED_ON_BRANCH, check_committed_on_branch),
        (CHECK_MIGRATIONS_TRACKED, check_migrations_tracked),
        (CHECK_ANCESTOR_OF_ORIGIN_MAIN, check_ancestor_of_origin_main),
    ):
        val = fn(cwd)
        if val is not None:
            checks[name] = val
    return {"sha": head_sha(cwd), "checks": checks}


def main(argv=None, *, stdout=None) -> int:
    """Read-only CLI seam: emit the gathered evidence as JSON on stdout so a
    shadow wrapper can pipe it into `quality_gate.main`. Pure read — no writes,
    no network. Always exits 0 (a gatherer never breaks a caller).

    Usage:  python -m nervous_system.quality_gate_evidence [cwd]   (default cwd='.')
    """
    import json
    import sys

    stdout = stdout if stdout is not None else sys.stdout
    argv = list(sys.argv[1:] if argv is None else argv)
    cwd = argv[0] if argv else "."
    stdout.write(json.dumps(gather(cwd)) + "\n")
    return 0


__all__ = [
    "CHECK_COMMITTED_ON_BRANCH",
    "CHECK_MIGRATIONS_TRACKED",
    "CHECK_ANCESTOR_OF_ORIGIN_MAIN",
    "is_git_repo",
    "head_sha",
    "check_committed_on_branch",
    "check_migrations_tracked",
    "check_ancestor_of_origin_main",
    "gather",
    "main",
]


if __name__ == "__main__":   # pragma: no cover
    import sys
    sys.exit(main())
