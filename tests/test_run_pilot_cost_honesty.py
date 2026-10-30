"""Tests for scripts/loop/run_pilot.py's cost-honesty contract (op#35690).

`run_task()`'s `finally` block must report a cost it genuinely cannot measure
as an explicit `"could not measure: ..."` string -- never a silent `0` or a
fabricated number. The whole pilot trial's conclusions depend on that.

These tests call `run_pilot.run_task(..., dry_run=True)` for real, which
exercises the actual `finally` block without launching any role session,
creating a worktree, or reading the DeepSeek balance. Two details of the
dry-run path matter and are handled here rather than assumed away:

* `launch_role()` resolves the Claude OAuth token *before* its `if dry_run`
  early return, so a condition-B run would spawn `tmux` and read the live
  token file. `resolve_claude_oauth_token` is stubbed -- the dry-run
  `RoleResult` never uses the token, and `claude_cost_usd_by_role` still gets
  its real `None` from `RoleResult`'s default.
* `run_task()`'s `finally` calls `remove_worktree()` (a `git` subprocess)
  unless `keep_worktrees=True`, even in a dry run. Tests pass
  `keep_worktrees=True`.

A module-level fixture additionally fails the test if any code path reaches
`run_pilot.run()`, so the "no subprocesses" property stays enforced rather
than merely intended.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parent.parent / "scripts" / "loop" / "run_pilot.py"
spec = importlib.util.spec_from_file_location("run_pilot", MODULE_PATH)
run_pilot = importlib.util.module_from_spec(spec)
sys.modules["run_pilot"] = run_pilot
spec.loader.exec_module(run_pilot)

UNMEASURED_DEEPSEEK = "could not measure: balance read failed before task start"


@pytest.fixture(autouse=True)
def _no_external_processes(monkeypatch):
    """run_task() must not reach run()/git() on the dry-run path."""

    def _boom(*args, **kwargs):
        raise AssertionError(f"dry-run path must not spawn a process: {args!r}")

    monkeypatch.setattr(run_pilot, "run", _boom)


@pytest.fixture(autouse=True)
def _stub_claude_token_resolution(monkeypatch):
    """See module docstring: launch_role() resolves this before the dry_run
    early return. The token is never used by a dry-run RoleResult."""

    monkeypatch.setattr(run_pilot, "resolve_claude_oauth_token", lambda: "test-token")


def _run_dry(task_id: str, condition: str, brief_path: Path):
    return run_pilot.run_task(
        task_id=task_id,
        condition=condition,
        brief_path=brief_path,
        owner="test-owner",
        repo="test-repo",
        hifz_checkout="/nonexistent/hifz-companion",
        max_turns={"spec": 1, "build": 1, "review": 1},
        keep_worktrees=True,
        dry_run=True,
    )


@pytest.fixture
def brief_path(tmp_path) -> Path:
    p = tmp_path / "brief.md"
    p.write_text("Add a subtract(a, b) function.\n")
    return p


def _assert_never_a_silent_zero(value, field: str) -> None:
    assert isinstance(value, str), f"{field} must be an explicit reason string, got {value!r}"
    assert value.startswith("could not measure"), f"{field} must start with the honesty marker: {value!r}"
    # The property stated directly: this can never be misread as a real (free) run.
    assert value != 0
    assert value != 0.0


def test_condition_a_reports_unmeasured_deepseek_cost_not_zero(brief_path):
    result = _run_dry("task-a", "A", brief_path)

    assert result.error is None
    assert result.finished_at is not None
    assert result.deepseek_cost_usd == UNMEASURED_DEEPSEEK
    _assert_never_a_silent_zero(result.deepseek_cost_usd, "deepseek_cost_usd")
    # Condition A has no Claude roles, so the whole claude_cost block is skipped
    # and the dataclass default stands.
    assert result.claude_cost is None
    assert result.claude_cost_usd_by_role == {}


def test_condition_b_reports_unmeasured_deepseek_and_claude_costs(brief_path):
    result = _run_dry("task-b", "B", brief_path)

    assert result.error is None
    assert result.finished_at is not None
    # The builder is still deepseek-flash in condition B, so track_deepseek is
    # still True and balance_before is still None on a dry run.
    assert result.deepseek_cost_usd == UNMEASURED_DEEPSEEK
    _assert_never_a_silent_zero(result.deepseek_cost_usd, "deepseek_cost_usd")

    # spec_writer ran (dry) and recorded a None cost; reviewer never ran, so it
    # is absent -- both count as missing, and neither is a known cost, so the
    # "partial" suffix must NOT appear.
    assert result.claude_cost_usd_by_role == {"spec_writer": None}
    assert result.claude_cost == (
        "could not measure: no matching transcript found for role(s) "
        "['spec_writer', 'reviewer']"
    )
    _assert_never_a_silent_zero(result.claude_cost, "claude_cost")
    assert "partial" not in result.claude_cost


def test_partial_claude_cost_names_the_missing_role_and_the_known_subtotal(
        brief_path, monkeypatch):
    """Exercise the `partial` branch: one Claude role's cost is known, the
    other is unmeasurable. `launch_role` is the seam (dry-run sessions cannot
    produce a real transcript cost), not the `finally` logic under test."""
    real_launch_role = run_pilot.launch_role

    def fake_launch_role(role, worktree, model, max_turns, dry_run):
        rr = real_launch_role(role, worktree, model, max_turns, dry_run)
        if role == "spec_writer":
            rr.claude_cost_usd = 1.2345
        return rr

    monkeypatch.setattr(run_pilot, "launch_role", fake_launch_role)

    result = _run_dry("task-b-partial", "B", brief_path)

    assert result.error is None
    assert result.claude_cost_usd_by_role == {"spec_writer": 1.2345}
    assert result.claude_cost == (
        "could not measure: no matching transcript found for role(s) ['reviewer'] "
        "(partial, from 1/2 role(s): $1.2345)"
    )
