"""CADENCE-008 A execute arm — worktree + claude -p + CI-gate orchestration.

The orchestration (`execute_ruling`) is pure/DI-driven so every safety branch is
unit-tested without spawning processes:
  - claude session fails / ambiguous  -> escalate, NO merge
  - diff adds a migration not named in the ruling -> refuse, NO merge, NO CI
  - CI red -> escalate, NO merge
  - CI green + clean -> fast-forward merge, record spend

Going live is gated on DRAIN_EXECUTE_ENABLED + both CADENCE-008 gates (window
close + >=2 clean report-only cycles). The thin subprocess wrappers
(claude -p, git, CI) mirror ralph_runner and are wired only behind those gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MIGRATION_PREFIX = "supabase/migrations/"
_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")

# cc-ihsanos lane repo + worktree naming (mirrors ralph_runner's BUG-019 scheme).
IHSANOS_REPO_PATH = "~/wingmen/projects/ihsanos"
_UNSAFE_RUN_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DASH_RUN_RE = re.compile(r"-{2,}")


def sanitize_ref(ref: str) -> str:
    """Collapse anything outside [A-Za-z0-9._-] so a decision_ref can never
    inject shell/path metacharacters into a branch name or /tmp worktree path."""
    cleaned = _UNSAFE_RUN_RE.sub("-", ref or "")
    cleaned = _DASH_RUN_RE.sub("-", cleaned).strip("-./")
    return cleaned or "unknown"


def worktree_paths(ref: str) -> tuple[str, str]:
    """(worktree_path, branch) for a ruling ref. Pure — no filesystem touch."""
    safe = sanitize_ref(ref)
    return f"/tmp/wingmen-wt-ihsanos-drain-{safe}", f"ihsanos-drain-{safe}"


def parse_changed_files(stdout: str) -> list:
    """Parse `git diff --name-only` output into a clean path list."""
    return [line.strip() for line in (stdout or "").splitlines() if line.strip()]


def summarize_ci(steps) -> dict:
    """Aggregate per-step (name, returncode, tail) results into a CI verdict.

    Fails closed: empty step list is NOT green. First failing step wins the
    detail so escalation reports point at the exact gate that broke."""
    steps = list(steps)
    if not steps:
        return {"green": False, "detail": "no CI steps ran"}
    for name, rc, tail in steps:
        if rc != 0:
            return {"green": False, "detail": f"{name} (rc={rc}): {(tail or '').strip()[:500]}"}
    return {"green": True, "detail": f"{len(steps)} CI steps passed"}


@dataclass(frozen=True)
class ExecOutcome:
    status: str  # merged | escalated_ci_red | escalated_ambiguous | refused_migration
    ruling_ref: str
    detail: str
    tokens_spent: int = 0


def unauthorized_migrations(changed_files, decision_text: str) -> list:
    """Migration files in the diff whose basename is not literally named in the
    ruling. cai #2067: filename mandatory; sha optional-but-binding (future)."""
    text = decision_text or ""
    bad = []
    for path in changed_files:
        if not path.startswith(MIGRATION_PREFIX):
            continue
        basename = path[len(MIGRATION_PREFIX):]
        if basename not in text and path not in text:
            bad.append(path)
    return bad


def build_prompt(ruling: dict) -> str:
    ref = ruling.get("decision_ref", "(unknown)")
    decision = ruling.get("decision", "")
    return (
        f"You are the cc-ihsanos drain worker executing pre-authorized ruling "
        f"{ref} in an isolated git worktree.\n\n"
        f"RULING TEXT:\n{decision}\n\n"
        "HARD RULES (non-negotiable):\n"
        "1. Do ONLY the work this ruling authorizes. Nothing else.\n"
        "2. NEVER create or modify any database migration file that is not named "
        "in the ruling above. If the work seems to require an unnamed migration, "
        "STOP and report instead of acting.\n"
        "3. Commit your work in the worktree. Do not push, do not merge, do not "
        "touch secrets or .env.\n"
        "4. If anything is ambiguous or you are unsure, STOP and report rather "
        "than guess.\n"
    )


def execute_ruling(
    ruling: dict,
    *,
    run_claude,
    changed_files_fn,
    run_ci,
    merge_fn,
) -> ExecOutcome:
    ref = ruling.get("decision_ref", "(unknown)")
    prompt = build_prompt(ruling)

    res = run_claude(prompt) or {}
    tokens = int(res.get("tokens", 0))
    if not res.get("ok"):
        return ExecOutcome(
            "escalated_ambiguous", ref,
            res.get("summary", "claude session failed/ambiguous"), tokens,
        )

    bad = unauthorized_migrations(changed_files_fn(), ruling.get("decision", ""))
    if bad:
        return ExecOutcome(
            "refused_migration", ref, f"unauthorized migrations: {bad}", tokens,
        )

    ci = run_ci() or {}
    if not ci.get("green"):
        return ExecOutcome("escalated_ci_red", ref, ci.get("detail", "ci red"), tokens)

    merge_fn()
    return ExecOutcome("merged", ref, res.get("summary", "merged"), tokens)


# ── Live I/O wrappers (system boundary — gated behind DRAIN_EXECUTE_ENABLED) ──
# Thin shell-outs mirroring ralph_runner's proven worktree/claude plumbing. The
# decision logic they feed (migration gate, CI verdict) is unit-tested above via
# DI; these are the untestable edges, validated at supervised first live run.

import os  # noqa: E402  (kept local to the live-arm section)
import subprocess  # noqa: E402

_SAFE_ENV_KEYS = {"PATH", "HOME", "USER", "SHELL", "LANG", "TERM", "LC_ALL", "LC_CTYPE"}
_CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
_CLAUDE_TIMEOUT_S = 1800  # 30 min, matches ralph_runner


def _repo_path() -> str:
    return os.path.expanduser(IHSANOS_REPO_PATH)


def _safe_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
    env["HOME"] = os.path.expanduser("~")
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def _git(cwd: str, args: list, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


def create_worktree(ref: str) -> tuple[str, str]:
    """Isolated worktree on a fresh branch off ihsanos main. Cleans any stale
    leftover from an interrupted prior cycle first (best-effort)."""
    repo = _repo_path()
    wt_path, branch = worktree_paths(ref)
    for args in (["worktree", "remove", "--force", wt_path], ["branch", "-D", branch]):
        try:
            _git(repo, args)
        except Exception:
            pass  # cleanup is best-effort
    res = _git(repo, ["worktree", "add", wt_path, "-b", branch])
    if res.returncode != 0:
        raise RuntimeError(f"git worktree add failed ({ref}): {res.stderr.strip()}")
    return wt_path, branch


def remove_worktree(wt_path: str, branch: str) -> None:
    """Best-effort teardown — never raises so a cleanup miss can't mask outcome."""
    repo = _repo_path()
    for args in (["worktree", "remove", "--force", wt_path], ["branch", "-D", branch]):
        try:
            _git(repo, args)
        except Exception:
            pass


def git_changed_files(wt_path: str) -> list:
    """Every path the worktree touches vs main — committed, staged, AND untracked.
    Untracked matters: a brand-new migration file is the exact thing the migration
    gate must catch, and `git diff` alone would miss it."""
    files = set(parse_changed_files(_git(wt_path, ["diff", "--name-only", "main...HEAD"]).stdout))
    for line in _git(wt_path, ["status", "--porcelain"]).stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        path = line[3:]
        if " -> " in path:  # rename: take the destination
            path = path.split(" -> ", 1)[1]
        files.add(path.strip())
    return sorted(files)


def run_claude_in_worktree(prompt: str, wt_path: str) -> dict:
    """Headless `claude -p` inside the worktree. Secrets are never passed to the
    subprocess (env whitelist). Returns {ok, summary, tokens}."""
    cmd = [
        _CLAUDE_BIN,
        "--dangerously-skip-permissions",
        "-p", prompt,
        "--output-format", "text",
    ]
    try:
        res = subprocess.run(
            cmd, cwd=wt_path, capture_output=True, text=True,
            env=_safe_env(), timeout=_CLAUDE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "summary": "claude timed out (30m)", "tokens": 0}
    ok = res.returncode == 0
    summary = (res.stdout or res.stderr or "").strip()[-2000:]
    return {"ok": ok, "summary": summary, "tokens": 0}
