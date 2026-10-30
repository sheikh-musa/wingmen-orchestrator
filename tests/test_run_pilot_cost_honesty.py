"""Tests for scripts/loop/run_pilot.py's cost-honesty contract (op#35690).

run_task()'s `finally` block computes two cost fields on the TaskRunResult it
returns -- `deepseek_cost_usd` and `claude_cost` -- and the whole op#35690
trial depends on an unmeasurable cost coming back as an explicit
`"could not measure: ..."` string, NEVER a silently-wrong 0 or a made-up
number. These tests pin that contract down.

They exercise the REAL `finally` block, unmocked, by calling
`run_task(..., dry_run=True)`. The dry-run path reaches no subprocess,
filesystem/worktree, or network code: `launch_role(..., dry_run=True)`
short-circuits before any launch; `make_worktree`/`write_task_files` are only
called in the `not dry_run` branch; and `do_builder`'s caller hits an early
`return result` inside the `try:`, so the `finally:` still runs. Because
`balance_before` is only ever assigned `when not dry_run`, it stays `None`,
which deterministically drives the "balance read failed before task start"
branch. `keep_worktrees=True` keeps the one `finally` subprocess
(`remove_worktree`) from firing on worktrees that were never created.

The task_id/owner/repo/hifz_checkout values below are placeholders: none are
touched on the dry-run path (no git, gh, worktree or balance call reads them),
so they need not point at anything real.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "scripts" / "loop" / "run_pilot.py"
spec = importlib.util.spec_from_file_location("run_pilot", MODULE_PATH)
run_pilot = importlib.util.module_from_spec(spec)
sys.modules["run_pilot"] = run_pilot
spec.loader.exec_module(run_pilot)


_BALANCE_FAILED = "could not measure: balance read failed before task start"
_CLAUDE_NO_TRANSCRIPT = (
    "could not measure: no matching transcript found for role(s) "
    "['spec_writer', 'reviewer']")

_MAX_TURNS = {"spec": 1, "build": 1, "review": 1}


def _dry_run(condition: str, tmp_path: Path):
    """Runs one real dry-run task-run and returns its TaskRunResult.

    No real side effects: dry_run short-circuits every launch/worktree/network
    path, and keep_worktrees=True skips the only subprocess the finally block
    would otherwise make.
    """
    brief = tmp_path / "brief.md"
    brief.write_text("# placeholder brief\nnot read on the dry-run path\n")
    return run_pilot.run_task(
        task_id="placeholder-task",
        condition=condition,
        brief_path=brief,
        owner="placeholder-owner",
        repo="placeholder-repo",
        hifz_checkout="/nonexistent/placeholder/checkout",
        max_turns=_MAX_TURNS,
        keep_worktrees=True,
        dry_run=True,
    )


def test_condition_a_deepseek_unmeasurable_claude_cost_untouched(tmp_path):
    # Condition A: all-DeepSeek, no Claude roles. track_deepseek is True
    # (builder is deepseek-flash), balance_before stays None on the dry-run
    # path -> the explicit "balance read failed before task start" string.
    result = _dry_run("A", tmp_path)

    assert result.deepseek_cost_usd == _BALANCE_FAILED
    # No Claude roles -> the whole claude_cost block is skipped, so it stays
    # at its dataclass default of None (not 0, not a fabricated number).
    assert result.claude_cost is None


def test_condition_b_both_cost_fields_report_unmeasurable(tmp_path):
    # Condition B: Claude spec_writer + reviewer, DeepSeek builder.
    result = _dry_run("B", tmp_path)

    # Builder is still deepseek-flash, so track_deepseek is still True and the
    # DeepSeek field reports the same unmeasurable string as condition A.
    assert result.deepseek_cost_usd == _BALANCE_FAILED

    # Both Claude roles end up with None in claude_cost_usd_by_role (launch_role
    # never set a real cost on the dry-run path, and the reviewer role is never
    # even reached before the early dry-run return). So `known` is empty, the
    # "(partial, ...)" suffix must NOT appear, and both roles are listed.
    assert result.claude_cost == _CLAUDE_NO_TRANSCRIPT
    assert "partial" not in result.claude_cost


def test_deepseek_cost_is_never_a_silent_zero(tmp_path):
    # The property stated directly: an unmeasurable DeepSeek cost can never be
    # misread as a legitimate zero-cost run, for either condition.
    for condition in ("A", "B"):
        result = _dry_run(condition, tmp_path)
        assert result.deepseek_cost_usd != 0
        assert result.deepseek_cost_usd != 0.0
        assert isinstance(result.deepseek_cost_usd, str)
        assert result.deepseek_cost_usd.startswith("could not measure")
