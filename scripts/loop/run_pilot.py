#!/usr/bin/env python3
"""run_pilot.py — the cc-loop harness (op#35690, docs/loop-pilot-design-v1.md).

Drives one (task_id, condition) attempt through all three roles end to end:
spec-writer -> harness patches manifest metadata -> builder -> `tests` +
`spec-tests-unchanged` checks -> reviewer (own worktree) -> harness patches
verdict metadata -> grader (ground truth, never seen by any role) -> result
written to a local JSON file (never printed secrets, never guessed numbers --
an unmeasurable field is written as null with a reason, not a fabricated one).

Condition A = all-DeepSeek. Condition B = DeepSeek builder + Claude
spec-writer/reviewer. See docs/loop-pilot-design-v1.md section 6c for the
exact model table and 6d for why every role launch sets
CC_BASE_OVERRIDE=cc-loop.

Usage:
    scripts/loop/run_pilot.py --task-id <id> --condition A|B \
        --brief-file tasks/loop-pilot/<id>/brief.md \
        --owner sheikh-musa --repo hifz [--max-turns-spec N] \
        [--max-turns-build N] [--max-turns-review N] \
        [--keep-worktrees] [--dry-run]

--dry-run prints the exact plan (worktree paths, env, commands) and makes NO
git/filesystem/network/launch side effects at all -- for sanity-checking the
wiring before spending a real session. It is not a substitute for a real
end-to-end attempt on a throwaway task.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ORCH_DIR = Path(__file__).resolve().parent.parent.parent
# The LIVE checkout, distinct from ORCH_DIR (this harness's own worktree) --
# needed ONLY to resolve the real Claude OAuth token (op#35690, 2026-10-30
# condition-B auth gap, see CLAUDE_CODE_OAUTH_TOKEN handling in launch_role()
# below). launch_dangerous_cc.sh self-locates its ORCH_DIR from its own
# invoked path (worktree-relative, `dirname "${BASH_SOURCE[0]}"/..`), so when
# this harness invokes it from ORCH_DIR, its `. "$ORCH_DIR/.env"` silently
# no-ops (worktrees don't carry `.env`) and its lane_token_resolver call
# resolves against the WRONG orch_dir too -- neither failure is loud (both
# are deliberately fail-open elsewhere in the fleet), so a Claude-model role
# session launches with no CLAUDE_CODE_OAUTH_TOKEN and no ANTHROPIC_API_KEY
# at all. Confirmed via a real probe run: the session's own JSONL transcript
# showed an "assistant" turn whose text was literally "Not logged in ·
# Please run /login", with every message type "<synthetic>" (no real model
# turn ever happened) -- not a theory, an observed failure.
LIVE_ORCH_DIR = Path.home() / "wingmen" / "orchestrator"
HIFZ_WORKTREE_ROOT = Path.home() / "wingmen" / "projects"
RESULTS_DIR = Path.home() / "wingmen" / "loop-pilot-state"
DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"

# op#35690 / orch-console bus #83797 (cc-quality MEDIUM on PR#661): role
# sessions and the `tests`-check subprocess both run with DATABASE_URL (and
# other KEEP-tier secrets) in their env (launch_dangerous_cc.sh's shared
# allow-list, scripts/lib/lane_env_allowlist.sh -- not something this
# harness can override per-base-id), so their captured stdout/stderr must
# never be persisted or printed unredacted. Reuses the fleet's existing
# secret-shape patterns (same module secrets_transcript_guard.py's Rule E and
# secrets_output_scanner.py both use) rather than inventing new detection.
sys.path.insert(0, str(ORCH_DIR / "scripts" / "hooks"))
from secret_shape_patterns import SECRET_VALUE_PATTERNS, find_hits  # noqa: E402

REDACTION = "[REDACTED by run_pilot.py -- pattern:{cls}]"


def redact_text(text: str) -> str:
    """Replaces every secret-shaped span in `text` with REDACTION, for every
    pattern class that has at least one hit. Idempotent, safe to call on
    clean text (no-op). Call this on ANY role or subprocess output before it
    is stored on a result object or printed -- never persist/print raw."""
    if not text:
        return text
    for cls, _match in find_hits(text, SECRET_VALUE_PATTERNS):
        text = SECRET_VALUE_PATTERNS[cls].sub(REDACTION.format(cls=cls), text)
    return text

CONDITIONS = {
    "A": {"spec_writer": "deepseek-flash", "builder": "deepseek-flash", "reviewer": "deepseek-flash"},
    "B": {"spec_writer": "claude-opus-4-8", "builder": "deepseek-flash", "reviewer": "claude-opus-4-8"},
}


class PilotError(RuntimeError):
    """A step failed in a way the harness cannot recover from for this task."""


@dataclass
class RoleResult:
    role: str
    model: str
    returncode: int
    duration_s: float
    stdout: str
    stderr: str
    claude_cost_usd: float | None = None  # only set for model.startswith("claude-")


@dataclass
class TaskRunResult:
    task_id: str
    condition: str
    hifz_owner: str
    hifz_repo: str
    started_at: str
    spec_commit_sha: str | None = None
    build_head_sha: str | None = None
    pr_url: str | None = None
    reviewer_verdict: str | None = None
    reviewer_reason: str | None = None
    tests_check: str | None = None  # "success" | "failure" | "error"
    tests_check_detail: str | None = None
    spec_tests_unchanged_check: str | None = None
    spec_tests_unchanged_detail: str | None = None
    grader_any_bug_survived: bool | None = None
    grader_detail: dict | None = None
    outcome: str | None = None  # correct_catch | false_pass | correct_pass | false_alarm | None
    role_timings_s: dict = field(default_factory=dict)
    deepseek_cost_usd: float | str | None = None  # float, or "could not measure: <reason>"
    claude_cost: float | str | None = None  # float, or "could not measure: <reason>"
    claude_cost_usd_by_role: dict = field(default_factory=dict)  # role -> float | None, audit detail behind claude_cost
    error: str | None = None
    finished_at: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd: list[str], cwd: str | None = None, env: dict | None = None,
        check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise PilotError(
            f"command failed (rc={r.returncode}): {' '.join(cmd)}\n"
            f"stdout: {r.stdout[-2000:]}\nstderr: {r.stderr[-2000:]}"
        )
    return r


def git(repo_dir: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", "-C", repo_dir, *args], check=check)


# ── worktrees ──────────────────────────────────────────────────────────────

def make_worktree(hifz_checkout: str, path: Path, branch: str, base: str,
                   detach: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if detach:
        git(hifz_checkout, "worktree", "add", "--detach", str(path), base)
    else:
        git(hifz_checkout, "worktree", "add", "-B", branch, str(path), base)


def remove_worktree(hifz_checkout: str, path: Path) -> None:
    run(["git", "-C", hifz_checkout, "worktree", "remove", "--force", str(path)], check=False)


# ── .loop-task/ harness contract (docs/loop-pilot-design-v1.md section 1) ──
#
# Deliberately contains NO path to the orchestrator checkout (no orch_dir.txt
# -- see the 2026-10-30 isolation fix in section 4/6e): every role writes
# only plain, local files here, and 100% of the record_manifest.py /
# reviewer_verdict.py / gh_status.py / loop-pilot/reviews-branch work is done
# by the harness, in its own process, after the role exits. A role that never
# learns where the orchestrator lives cannot construct a path to
# tasks/loop-pilot/<task_id>/hidden/ under it -- it had no way to find that
# tree before this fix either, except by being handed the one path that led
# straight there.

def write_task_files(worktree: Path, task_id: str, brief_text: str) -> None:
    task_dir = worktree / ".loop-task"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task_id.txt").write_text(task_id + "\n")
    (task_dir / "brief.md").write_text(brief_text)


# ── role launch ──────────────────────────────────────────────────────────

def resolve_claude_oauth_token() -> str:
    """The real fix for the condition-B auth gap (see LIVE_ORCH_DIR's comment
    above): resolve the SAME token file a normal `cc-loop` lane boot would
    get -- the live checkout's lane-default pointer chain -- verify its
    fingerprint against scripts/lib/token_fps.map (same discipline
    launch_dangerous_cc.sh's own token_file_guard applies), and return its
    contents. Never logs the token itself. Raises PilotError (fail-closed --
    no silent "launch unauthenticated and hope") if no usable token is
    resolved or it fails the fingerprint check."""
    sys.path.insert(0, str(ORCH_DIR))
    from scripts.lib.lane_token_resolver import resolve_lane_token_path  # noqa: E402
    from scripts.lib.token_file_guard import check as token_file_check  # noqa: E402

    # Pass the real TMUX SESSION NAME, not the agent_id -- identical to how
    # launch_dangerous_cc.sh derives `_LANE_SESSION`. These differ in
    # practice: cc-loop's own lane has an explicit per-SESSION pin
    # (.lane_session_token.loop -> Syed, token-pool conservation) keyed on
    # the tmux session "loop", NOT on "cc-loop" -- passing the agent_id
    # instead would silently miss that pin and fall through to the fleet
    # default (Musa's personal account), exactly the quota-stealing mistake
    # this per-lane pin exists to prevent. Caught empirically: the two
    # session strings resolved to DIFFERENT accounts (syed vs musa) when
    # tested directly.
    tmux_session = run(["tmux", "display-message", "-p", "#S"], check=False).stdout.strip()
    path = resolve_lane_token_path(tmux_session, orch_dir=str(LIVE_ORCH_DIR))
    if not path:
        raise PilotError(
            "resolve_claude_oauth_token: lane_token_resolver returned no pointer "
            f"for tmux session {tmux_session!r} against live orch_dir {LIVE_ORCH_DIR} -- a "
            "condition-B Claude role session would launch with no "
            "CLAUDE_CODE_OAUTH_TOKEN at all (confirmed failure mode: the "
            "session's own transcript shows 'Not logged in', every message "
            "type '<synthetic>', no real model turn). Refusing to launch.")
    ok, reason = token_file_check(path)
    if not ok:
        raise PilotError(f"resolve_claude_oauth_token: {reason}")
    token = Path(path).read_text(encoding="utf-8").strip()
    if not token:
        raise PilotError(f"resolve_claude_oauth_token: {path} resolved but is empty.")
    return token


def extract_claude_session_cost(worktree: Path, since_epoch: float) -> float | None:
    """Best-effort: find the Claude Code session transcript this exact role
    launch produced and return its final self-reported totalCostUSD.

    Matched on the transcript's own `"cwd":"<worktree>"` field, not a
    hand-rolled guess at Claude Code's `~/.claude/projects/<mangled>` dir-name
    escaping scheme (undocumented, and a wrong guess here would silently
    misattribute cost to the wrong session rather than fail loudly) -- same
    discipline as the `actual_sha` vs self-reported-marker check above:
    trust ground truth, not a derived guess. Scoped to files modified no
    earlier than `since_epoch` (this role launch's own start time) since a
    worktree path is unique per launch (each gets a fresh timestamp suffix)
    but transcripts from unrelated sessions could in principle share a stale
    jsonl mtime scan window otherwise. Returns None (never 0.0) if no
    matching transcript is found -- the caller must treat that as
    unmeasured, not free."""
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.is_dir():
        return None
    needle = f'"cwd":"{worktree}"'.encode("utf-8")
    candidates: list[Path] = []
    for d in projects_root.iterdir():
        if not d.is_dir():
            continue
        for jf in d.glob("*.jsonl"):
            try:
                if jf.stat().st_mtime < since_epoch - 5:
                    continue
                data = jf.read_bytes()
            except OSError:
                continue
            if needle in data:
                candidates.append(jf)
    if not candidates:
        return None
    jf = max(candidates, key=lambda p: p.stat().st_mtime)
    cost = None
    for line in jf.read_text(encoding="utf-8", errors="ignore").splitlines():
        if '"type":"cost-state"' not in line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "totalCostUSD" in doc:
            cost = doc["totalCostUSD"]
    return cost


def launch_role(role: str, worktree: Path, model: str, max_turns: int,
                 dry_run: bool) -> RoleResult:
    prompt_rel = f"scripts/loop/prompts/{role}.md"
    cmd = [str(ORCH_DIR / "scripts" / "launch_dangerous_cc.sh"),
           "--repo", "hifz-companion",
           "--scheduled-prompt", prompt_rel,
           "--", "--max-turns", str(max_turns)]
    env = dict(os.environ)
    env["MODEL"] = model
    env["CC_BASE_OVERRIDE"] = "cc-loop"
    if model.startswith("claude-"):
        # Max-subscription billing, matching launch_dangerous_cc.sh's own
        # intent (CLAUDE.md: "unset ANTHROPIC_API_KEY ... so every lane runs
        # on the Mac Mini's Claude Max subscription, not metered API
        # billing") -- belt-and-suspenders since that script's own scrub
        # relies on `.env` sourcing which no-ops from this harness's worktree.
        env.pop("ANTHROPIC_API_KEY", None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = resolve_claude_oauth_token()

    if dry_run:
        print(f"[dry-run] would launch {role} in {worktree} with MODEL={model} "
              f"CC_BASE_OVERRIDE=cc-loop: {' '.join(cmd)}")
        return RoleResult(role=role, model=model, returncode=0, duration_s=0.0,
                           stdout="", stderr="")

    t0 = time.monotonic()
    start_wall = time.time()
    r = subprocess.run(cmd, cwd=str(worktree), env=env, capture_output=True, text=True)
    duration = time.monotonic() - t0
    claude_cost_usd = (extract_claude_session_cost(worktree, start_wall)
                        if model.startswith("claude-") else None)
    # Redact at the source: the role session's own process carries DATABASE_URL
    # (launch_dangerous_cc.sh's shared KEEP tier) -- its captured output must
    # never be stored unredacted, even transiently, since callers embed
    # rr.stdout/rr.stderr straight into error messages / the result object.
    return RoleResult(role=role, model=model, returncode=r.returncode,
                       duration_s=duration, stdout=redact_text(r.stdout),
                       stderr=redact_text(r.stderr), claude_cost_usd=claude_cost_usd)


def extract_marker(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


# ── gh helpers ─────────────────────────────────────────────────────────────

def gh_pr_view(worktree: Path) -> dict | None:
    r = run(["gh", "pr", "view", "--json", "number,url,headRefName,headRefOid"],
            cwd=str(worktree), check=False)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


# ── DeepSeek balance (shape reused from scripts/deepseek_balance_poll.py,
#    PR#630 -- not its poller; see docs/loop-pilot-design-v1.md section 6b
#    for why a per-task delta is only valid if nothing else spends concurrently) ──

def deepseek_fetch_key() -> str:
    # provider_vault_fetch.py needs psycopg (via nervous_system.vault), which
    # only the orchestrator's venv has -- sys.executable is whatever
    # interpreter happened to launch run_pilot.py itself (e.g. the system
    # python3 on PATH if invoked via its own shebang), not necessarily that
    # venv. Use the venv explicitly so this works regardless of how
    # run_pilot.py was invoked (caught live: the first real dry run hit
    # exactly this, system python3.15 lacking psycopg).
    venv_py = ORCH_DIR / ".venv" / "bin" / "python3"
    python = str(venv_py) if venv_py.exists() else sys.executable
    env = dict(os.environ)
    env.setdefault("AGENT_ID", "cc-loop-1")
    env.setdefault("CC_BASE_AGENT_ID", "cc-loop")
    out = run([python, str(ORCH_DIR / "scripts" / "lib" / "provider_vault_fetch.py"),
               "DEEPSEEK_CODING_KEY", "cc-loop pilot cost tracking"],
              cwd=str(ORCH_DIR), env=env)
    return out.stdout.strip()


def deepseek_balance_usd() -> float:
    key = deepseek_fetch_key()
    req = urllib.request.Request(DEEPSEEK_BALANCE_URL, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    for info in payload.get("balance_infos") or []:
        if info.get("currency") == "USD":
            return float(info["total_balance"])
    infos = payload.get("balance_infos") or []
    if infos:
        return float(infos[0]["total_balance"])
    raise PilotError("no balance_infos in DeepSeek response")


# ── checks ─────────────────────────────────────────────────────────────────

# Secrets local_ci_runner.sh's subprocess (npm/vite/playwright/node build
# tooling, none of which talks to the orchestrator substrate) has no
# legitimate need for, but would inherit via plain env=None since it's
# launched from run_pilot.py's own process. Least-privilege for the one
# subprocess call this harness fully controls and that runs the most
# third-party tooling (orch-console bus #83797, cc-quality MEDIUM on PR#661).
_CI_UNNEEDED_SECRETS = {
    "DATABASE_URL", "SUPABASE_DB_URL", "SUPABASE_SERVICE_KEY",
    "ORCHESTRATOR_SUPABASE_URL", "ORCHESTRATOR_SUPABASE_SERVICE_KEY",
    "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
}


def _ci_subprocess_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _CI_UNNEEDED_SECRETS}


def run_tests_check(worktree: Path, sha: str, owner: str, repo: str) -> tuple[str, str]:
    r = run([str(ORCH_DIR / "scripts" / "loop" / "local_ci_runner.sh"),
             "--repo-dir", str(worktree), "--sha", sha,
             "--post-status", "--owner", owner, "--repo", repo,
             "--orch-dir", str(ORCH_DIR)], env=_ci_subprocess_env(), check=False, timeout=900)
    status = "success" if r.returncode == 0 else "failure"
    # Redact BEFORE slicing (defense in depth even with the env scrub above --
    # GH_TOKEN/etc are still present and this subprocess runs third-party
    # tooling this harness doesn't fully control). Last ~2000 chars of the
    # redacted text: enough to tell a real CI failure from an infra break
    # (e.g. a script missing +x) without flooding the result.
    detail = redact_text(r.stdout + "\n" + r.stderr)[-2000:]
    return status, detail


def run_spec_tests_check(worktree: Path, task_id: str, spec_sha: str, head_sha: str,
                          owner: str, repo: str) -> tuple[str, str]:
    r = run([sys.executable, str(ORCH_DIR / "scripts" / "loop" / "check_spec_tests_unchanged.py"),
             "--repo-dir", str(worktree), "--task-id", task_id,
             "--spec-commit-sha", spec_sha, "--head-sha", head_sha,
             "--owner", owner, "--repo", repo, "--post-status"],
            env=_ci_subprocess_env(), check=False)
    status = "success" if r.returncode == 0 else "failure"
    detail = redact_text(r.stdout + "\n" + r.stderr).strip()[-2000:]
    return status, detail


def run_grader(task_id: str, worktree: Path, head_sha: str) -> dict:
    grader_path = ORCH_DIR / "tasks" / "loop-pilot" / task_id / "hidden" / "grader.py"
    if not grader_path.exists():
        raise PilotError(f"grader not found: {grader_path}")
    r = run([sys.executable, str(grader_path), "--repo-dir", str(worktree), "--head-sha", head_sha],
            env=_ci_subprocess_env(), check=True)
    return json.loads(r.stdout)


# ── per-role orchestration ──────────────────────────────────────────────────

def do_spec_writer(task_id: str, worktree: Path, model: str, max_turns: int,
                    dry_run: bool, result: TaskRunResult) -> str | None:
    t0 = time.monotonic()
    rr = launch_role("spec_writer", worktree, model, max_turns, dry_run)
    result.role_timings_s["spec_writer"] = time.monotonic() - t0
    if model.startswith("claude-"):
        result.claude_cost_usd_by_role["spec_writer"] = rr.claude_cost_usd
    if dry_run:
        return None
    if rr.returncode != 0:
        raise PilotError(f"spec_writer session exited {rr.returncode}:\n{rr.stderr[-2000:]}")

    # Ground truth, not the self-reported marker: ask git directly.
    actual_sha = git(str(worktree), "rev-parse", "HEAD").stdout.strip()
    reported = extract_marker(rr.stdout, "SPEC_COMMIT_SHA=")
    if reported and reported != actual_sha:
        print(f"WARNING: spec_writer printed SPEC_COMMIT_SHA={reported} but HEAD is "
              f"{actual_sha} -- trusting git, not the self-report.")

    # The role declared its acceptance test paths in a plain local file (it
    # has no orch_dir and so cannot run record_manifest.py itself -- see the
    # .loop-task/ contract note above). The harness does the ENTIRE manifest
    # write itself, in one call, now that it has everything: the test paths,
    # the real commit sha, and the authoritative model id.
    test_files_path = worktree / ".loop-task" / "test_files.txt"
    if not test_files_path.exists():
        raise PilotError(f"spec_writer exited but {test_files_path} is missing")
    test_files = [ln.strip() for ln in test_files_path.read_text().splitlines() if ln.strip()]
    if not test_files:
        raise PilotError(f"{test_files_path} is empty -- spec_writer declared no test files")

    run([sys.executable, str(ORCH_DIR / "scripts" / "loop" / "record_manifest.py"),
         "--task-id", task_id, "--repo-dir", str(worktree),
         "--spec-commit-sha", actual_sha, "--spec-writer-model", model,
         *[arg for f in test_files for arg in ("--test-file", f)]],
        env=_ci_subprocess_env())

    # Not committed yet, and nothing has consumed `actual_sha` as a pin point
    # yet (builder hasn't started) -- amending the role's own commit to fold
    # manifest.json in is safe here, unlike the shared reviews-branch case.
    git(str(worktree), "add", f".loop/{task_id}/manifest.json")
    git(str(worktree), "commit", "--amend", "--no-edit")
    amended_sha = git(str(worktree), "rev-parse", "HEAD").stdout.strip()
    return amended_sha


def do_builder(task_id: str, worktree: Path, model: str, max_turns: int,
                dry_run: bool, result: TaskRunResult) -> tuple[str | None, str | None]:
    t0 = time.monotonic()
    rr = launch_role("builder", worktree, model, max_turns, dry_run)
    result.role_timings_s["builder"] = time.monotonic() - t0
    if dry_run:
        return None, None
    if rr.returncode != 0:
        raise PilotError(f"builder session exited {rr.returncode}:\n{rr.stderr[-2000:]}")

    head_sha = git(str(worktree), "rev-parse", "HEAD").stdout.strip()
    pr = gh_pr_view(worktree)
    pr_url = pr["url"] if pr else None
    if pr_url is None:
        print("WARNING: builder session exited but no PR is visible from its worktree "
              "(gh pr view found nothing) -- it may not have pushed/opened one.")
    return head_sha, pr_url


VALID_VERDICTS = {"PASS", "FAIL"}


def parse_verdict_file(path: Path) -> tuple[str, str]:
    """Parses the role's plain `VERDICT=.../REASON=...` file (two lines,
    that order). No orch_dir, no JSON, no script invocation needed from the
    role's side -- see the .loop-task/ contract note on write_task_files."""
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    verdict = reason = None
    for ln in lines:
        if ln.startswith("VERDICT="):
            verdict = ln[len("VERDICT="):].strip()
        elif ln.startswith("REASON="):
            reason = ln[len("REASON="):].strip()
    if verdict not in VALID_VERDICTS or not reason:
        raise PilotError(f"{path} did not parse to a valid VERDICT=/REASON= pair: {lines!r}")
    return verdict, reason


def do_reviewer(task_id: str, hifz_checkout: str, review_worktree: Path, head_sha: str,
                 model: str, max_turns: int, dry_run: bool, owner: str, repo: str,
                 result: TaskRunResult) -> tuple[str | None, str | None]:
    t0 = time.monotonic()
    rr = launch_role("reviewer", review_worktree, model, max_turns, dry_run)
    result.role_timings_s["reviewer"] = time.monotonic() - t0
    if model.startswith("claude-"):
        result.claude_cost_usd_by_role["reviewer"] = rr.claude_cost_usd
    if dry_run:
        return None, None
    if rr.returncode != 0:
        raise PilotError(f"reviewer session exited {rr.returncode}:\n{rr.stderr[-2000:]}")

    # The role wrote only a plain local verdict file (it has no orch_dir and
    # so cannot run reviewer_verdict.py, post the GitHub status, or switch to
    # loop-pilot/reviews itself -- ALL of that is now 100% harness-side).
    verdict_file = review_worktree / ".loop-task" / "verdict.txt"
    if not verdict_file.exists():
        raise PilotError(f"reviewer exited but {verdict_file} is missing")
    verdict, reason = parse_verdict_file(verdict_file)

    # The reviewer role may legitimately run build/test tooling to inform its
    # verdict (e.g. hifz's `update-version` prebuild step stamps a fresh
    # build timestamp into web/public/version.json + web/src/version.js),
    # which leaves review_worktree's tracked files locally modified -- that
    # already broke the loop-pilot/reviews checkout below once (op#35690,
    # task-01 condition B: "Your local changes ... would be overwritten by
    # checkout"). Nothing past this point needs review_worktree's checked-out
    # tree state (verdict/reason are already captured above as plain Python
    # values), so discard it now, before either checkout attempt.
    git(str(review_worktree), "reset", "--hard", "HEAD", check=False)
    git(str(review_worktree), "clean", "-fd", check=False)

    verdict_rel = f".loop/{task_id}/reviews/{head_sha}.json"
    run([sys.executable, str(ORCH_DIR / "scripts" / "loop" / "reviewer_verdict.py"),
         "--task-id", task_id, "--repo-dir", str(review_worktree),
         "--head-sha", head_sha, "--verdict", verdict, "--reason", reason,
         "--reviewer-model", model, "--reviewer-identity", "cc-loop"],
        env=_ci_subprocess_env())

    # loop-pilot/reviews is a SHARED branch other task-runs also push to --
    # fetch/checkout it fresh here (the review_worktree started detached at
    # head_sha, not on this branch at all), commit, push. Append-only, never
    # amend+force on a branch other runs are concurrently writing to.
    git(str(review_worktree), "fetch", "origin", "loop-pilot/reviews", check=False)
    r = git(str(review_worktree), "checkout", "-B", "loop-pilot/reviews",
             "origin/loop-pilot/reviews", check=False)
    if r.returncode != 0:
        git(str(review_worktree), "checkout", "-B", "loop-pilot/reviews", "loop-pilot/base")
    git(str(review_worktree), "add", verdict_rel)
    git(str(review_worktree), "commit", "-m", f"review({task_id}): {verdict} for {head_sha[:12]}")
    git(str(review_worktree), "push", "origin", "loop-pilot/reviews")

    # Post the `reviewer-verdict` GitHub status for the ORIGINAL head_sha
    # (captured by the caller before this worktree ever existed) -- not
    # whatever commit the loop-pilot/reviews checkout just produced.
    run([sys.executable, str(ORCH_DIR / "scripts" / "loop" / "gh_status.py"),
         "--owner", owner, "--repo", repo, "--sha", head_sha,
         "--state", "success" if verdict == "PASS" else "failure",
         "--context", "reviewer-verdict", "--description", reason[:140]],
        env=_ci_subprocess_env())

    return verdict, reason


# ── main task-run driver ────────────────────────────────────────────────────

def run_task(task_id: str, condition: str, brief_path: Path, owner: str, repo: str,
             hifz_checkout: str, max_turns: dict, keep_worktrees: bool,
             dry_run: bool) -> TaskRunResult:
    models = CONDITIONS[condition]
    result = TaskRunResult(task_id=task_id, condition=condition, hifz_owner=owner,
                            hifz_repo=repo, started_at=now_iso())

    brief_text = brief_path.read_text()
    run_tag = f"{task_id}-{condition}-{int(time.time())}"
    main_wt = HIFZ_WORKTREE_ROOT / f"hifz-companion.wt-{run_tag}-main"
    review_wt = HIFZ_WORKTREE_ROOT / f"hifz-companion.wt-{run_tag}-review"
    branch = f"loop-pilot/task-{run_tag}"

    track_deepseek = "deepseek" in models["spec_writer"] or "deepseek" in models["builder"] \
        or "deepseek" in models["reviewer"]
    balance_before = None
    if track_deepseek and not dry_run:
        try:
            balance_before = deepseek_balance_usd()
        except Exception as e:  # noqa: BLE001 - genuinely best-effort telemetry
            print(f"WARNING: could not read DeepSeek balance before task: {e}")

    try:
        if dry_run:
            print(f"[dry-run] would create worktree {main_wt} (branch {branch}) off loop-pilot/base")
        else:
            make_worktree(hifz_checkout, main_wt, branch, "loop-pilot/base")
            write_task_files(main_wt, task_id, brief_text)

        spec_sha = do_spec_writer(task_id, main_wt, models["spec_writer"],
                                   max_turns["spec"], dry_run, result)
        result.spec_commit_sha = spec_sha

        if not dry_run and spec_sha is None:
            raise PilotError("spec_writer produced no usable commit sha")

        if not dry_run:
            write_task_files(main_wt, task_id, brief_text)  # re-write for builder's launch

        head_sha, pr_url = do_builder(task_id, main_wt, models["builder"],
                                       max_turns["build"], dry_run, result)
        result.build_head_sha = head_sha
        result.pr_url = pr_url

        if dry_run:
            print(f"[dry-run] would run tests + spec-tests-unchanged checks, then create "
                  f"{review_wt} detached at the builder head and launch reviewer there")
            result.finished_at = now_iso()
            return result

        if head_sha is None:
            raise PilotError("builder produced no usable head sha")

        result.tests_check, result.tests_check_detail = run_tests_check(
            main_wt, head_sha, owner, repo)
        result.spec_tests_unchanged_check, result.spec_tests_unchanged_detail = \
            run_spec_tests_check(main_wt, task_id, spec_sha, head_sha, owner, repo)

        make_worktree(hifz_checkout, review_wt, branch="", base=head_sha, detach=True)
        write_task_files(review_wt, task_id, brief_text)

        verdict, reason = do_reviewer(task_id, hifz_checkout, review_wt, head_sha,
                                       models["reviewer"], max_turns["review"], dry_run,
                                       owner, repo, result)
        result.reviewer_verdict = verdict
        result.reviewer_reason = reason

        grader_doc = run_grader(task_id, main_wt, head_sha)
        result.grader_any_bug_survived = grader_doc.get("any_bug_survived")
        result.grader_detail = grader_doc

        survived = result.grader_any_bug_survived
        if verdict == "FAIL" and survived:
            result.outcome = "correct_catch"
        elif verdict == "PASS" and survived:
            result.outcome = "false_pass"
        elif verdict == "PASS" and not survived:
            result.outcome = "correct_pass"
        elif verdict == "FAIL" and not survived:
            result.outcome = "false_alarm"

    except PilotError as e:
        result.error = str(e)
    finally:
        if track_deepseek and balance_before is not None:
            try:
                balance_after = deepseek_balance_usd()
                result.deepseek_cost_usd = round(balance_before - balance_after, 4)
            except Exception as e:  # noqa: BLE001
                result.deepseek_cost_usd = f"could not measure: {e}"
        elif track_deepseek:
            result.deepseek_cost_usd = "could not measure: balance read failed before task start"

        claude_roles = [r for r in ("spec_writer", "reviewer") if "claude-opus" in models[r]]
        if claude_roles:
            costs = result.claude_cost_usd_by_role
            missing = [r for r in claude_roles if costs.get(r) is None]
            known = [costs[r] for r in claude_roles if costs.get(r) is not None]
            if missing:
                partial = f" (partial, from {len(known)}/{len(claude_roles)} role(s): ${round(sum(known), 4)})" if known else ""
                result.claude_cost = (
                    f"could not measure: no matching transcript found for role(s) {missing}{partial}")
            else:
                result.claude_cost = round(sum(known), 4)

        result.finished_at = now_iso()

        if not keep_worktrees:
            remove_worktree(hifz_checkout, main_wt)
            remove_worktree(hifz_checkout, review_wt)

    return result


def write_result(result: TaskRunResult) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{result.task_id}-{result.condition}-{int(time.time())}.json"
    out_path.write_text(json.dumps(result.__dict__, indent=2, sort_keys=True) + "\n")
    return out_path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task-id", required=True)
    p.add_argument("--condition", required=True, choices=["A", "B"])
    p.add_argument("--brief-file", required=True)
    p.add_argument("--owner", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--hifz-checkout", default=str(Path.home() / "wingmen" / "projects" / "hifz-companion"))
    p.add_argument("--max-turns-spec", type=int, default=40)
    p.add_argument("--max-turns-build", type=int, default=50)
    p.add_argument("--max-turns-review", type=int, default=30)
    p.add_argument("--keep-worktrees", action="store_true",
                   help="don't remove the task's worktrees on exit (debugging)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan, make no git/filesystem/network/launch side effects")
    args = p.parse_args(argv)

    brief_path = Path(args.brief_file)
    if not brief_path.is_absolute():
        brief_path = ORCH_DIR / brief_path
    if not brief_path.exists():
        p.error(f"--brief-file not found: {brief_path}")

    result = run_task(
        task_id=args.task_id, condition=args.condition, brief_path=brief_path,
        owner=args.owner, repo=args.repo, hifz_checkout=args.hifz_checkout,
        max_turns={"spec": args.max_turns_spec, "build": args.max_turns_build,
                   "review": args.max_turns_review},
        keep_worktrees=args.keep_worktrees, dry_run=args.dry_run,
    )

    if args.dry_run:
        print("[dry-run] plan looked sane, no side effects made.")
        return 0

    out_path = write_result(result)
    print(json.dumps(result.__dict__, indent=2, sort_keys=True))
    print(f"\nWrote result to {out_path}")
    return 1 if result.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
