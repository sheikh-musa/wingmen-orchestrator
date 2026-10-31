"""Pins the orchestrator CI's concurrency group (orch-console #87540, op#36768).

The problem it fixes: with nothing cancelling superseded runs, re-pushing a branch
queues another full 12-36 min suite behind the 2 runners. On 2026-10-31 that left 20
queued runs -- 8 of them obsolete duplicates that had to be cancelled by hand -- and
PRs waited hours for work nobody would read.

The property that matters is NOT just "cancel-in-progress is on": it is that the group
key must keep a trunk push and a PR run in DIFFERENT groups. If they ever collapse into
one, a push to trunk cancels every in-flight PR run and vice versa, which is worse than
the queue it replaces. These tests assert the property, not the YAML text.
"""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"


def _wf() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(data, dict), "ci.yml did not parse as a mapping"
    return data


def test_ci_has_a_concurrency_group_with_cancel_in_progress():
    conc = _wf().get("concurrency")
    assert conc, "ci.yml has no concurrency block -- superseded runs will stack again"
    assert conc.get("cancel-in-progress") is True, (
        "concurrency.cancel-in-progress must be true, or a newer run waits instead of "
        "replacing the older one"
    )


def test_concurrency_group_is_keyed_on_both_workflow_and_ref():
    group = _wf()["concurrency"]["group"]
    assert "github.workflow" in group, (
        "group must include github.workflow so a future second workflow in this repo "
        "cannot cancel CI runs"
    )
    assert "github.ref" in group, (
        "group must key on github.ref: that is what separates refs/heads/<branch> "
        "(push) from refs/pull/<N>/merge (PR), so trunk and PRs never cancel each other"
    )


def test_a_trunk_push_and_a_pr_run_land_in_different_groups():
    # the actual separation, evaluated rather than asserted from the string form:
    # simulate the two event kinds GitHub would produce for the same branch.
    group = _wf()["concurrency"]["group"]
    assert "${" in group

    def render(ref: str) -> str:
        return group.replace("${{ github.workflow }}", "CI").replace("${{ github.ref }}", ref)

    trunk_push = render("refs/heads/fable/substrate-safe-fixes")
    pr_run = render("refs/pull/763/merge")
    assert trunk_push != pr_run, (
        "a trunk push and a PR run resolved to the same concurrency group, so one would "
        "cancel the other"
    )
    # ...and two runs of the SAME ref DO share a group, which is the point.
    assert render("refs/pull/763/merge") == pr_run


def test_the_ci_job_still_runs_on_the_cubeasht_self_hosted_runner():
    # guard against a concurrency edit accidentally rewriting the job body
    job = _wf()["jobs"]["quality"]
    assert "cubeasht" in str(job.get("runs-on")), (
        "the quality job moved off the cubeasht self-hosted runner -- that is a "
        "capacity/secret-boundary change, not a concurrency change, and must not ride "
        "along in this PR"
    )
