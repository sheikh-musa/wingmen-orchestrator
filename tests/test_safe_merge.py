"""Tests for the fail-closed merge wrapper (scripts.lib.safe_merge).

WHY: on a repo where branch protection is UNAVAILABLE (sheikh-musa/ihsanos is
private on a free plan → GitHub refuses to mark any check required), `gh pr merge
--auto` gates only on REQUIRED checks — of which there are none — so it merged a
client-prod PR (#332) before the `unit-tests` job finished. It came out clean by
luck. The gate must therefore live in the TOOL: refuse unless EVERY check has
completed and succeeded.

The tests that matter are the REFUSALS. Only an all-SUCCESS rollup may merge; a
pending check, a failed check, a mixed set, a skipped/neutral check, an
unrecognised shape, zero checks, or an enumeration error must all REFUSE.
"""
import pytest

from scripts.lib.safe_merge import (
    MergeDecision,
    classify_check,
    evaluate_checks,
    safe_merge,
)


# ── classify_check: per-check verdict ────────────────────────────────────────

def _run(**kw):
    base = {"__typename": "CheckRun", "name": "unit-tests", "status": "COMPLETED", "conclusion": "SUCCESS"}
    base.update(kw)
    return base


def test_completed_success_checkrun_passes():
    assert classify_check(_run())[0] == "pass"


def test_in_progress_checkrun_is_pending():
    assert classify_check(_run(status="IN_PROGRESS", conclusion=None))[0] == "pending"


def test_queued_checkrun_is_pending():
    assert classify_check(_run(status="QUEUED", conclusion=""))[0] == "pending"


def test_failed_checkrun_fails():
    assert classify_check(_run(conclusion="FAILURE"))[0] == "fail"


@pytest.mark.parametrize("concl", ["CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"])
def test_bad_conclusions_fail(concl):
    assert classify_check(_run(conclusion=concl))[0] == "fail"


@pytest.mark.parametrize("concl", ["SKIPPED", "NEUTRAL"])
def test_skipped_and_neutral_are_not_success_faiclosed(concl):
    # A skipped check has NOT demonstrably succeeded — strict "COMPLETED and
    # SUCCEEDED" means it must not count as green by DEFAULT (a mis-path-filtered
    # unit-tests job that SKIPPED is exactly a way tests "didn't run").
    assert classify_check(_run(conclusion=concl))[0] == "fail"


# ── --allow-skipped: skip permitted BY NAME (ruling 2026-08-18) ──────────────

def test_skipped_permitted_when_named():
    v, label = classify_check(_run(name="e2e-tests", conclusion="SKIPPED"), allow_skipped=["e2e-tests"])
    assert v == "permitted_skip" and "e2e-tests" in label


def test_skipped_refuses_when_not_named():
    # unit-tests skipping is the #332 failure mode — must still refuse even when a
    # DIFFERENT check name is allow-listed.
    assert classify_check(_run(name="unit-tests", conclusion="SKIPPED"), allow_skipped=["e2e-tests"])[0] == "fail"


def test_neutral_never_covered_by_allow_skipped():
    # The flag covers SKIPPED only, never NEUTRAL — even if the name is listed.
    assert classify_check(_run(name="e2e-tests", conclusion="NEUTRAL"), allow_skipped=["e2e-tests"])[0] == "fail"


def test_ihsanos_6of6_shape_merges_with_allow_skipped():
    # The measured ihsanos rollup: several SUCCESS + one by-design e2e-tests SKIPPED.
    rollup = [_run(name=f"job{i}") for i in range(6)] + [_run(name="e2e-tests", conclusion="SKIPPED")]
    d = evaluate_checks(rollup, allow_skipped=["e2e-tests"])
    assert d.ok is True and d.permitted_skips and "e2e-tests" in " ".join(d.permitted_skips)


def test_ihsanos_shape_still_refuses_without_the_flag():
    rollup = [_run(name=f"job{i}") for i in range(6)] + [_run(name="e2e-tests", conclusion="SKIPPED")]
    assert evaluate_checks(rollup).ok is False


def test_permitted_skip_is_printed_in_render():
    d = evaluate_checks([_run(name="lint"), _run(name="e2e-tests", conclusion="SKIPPED")],
                        allow_skipped=["e2e-tests"])
    assert "ALLOWING SKIPPED" in d.render() and "e2e-tests" in d.render()


def test_wrapper_merges_with_permitted_skip():
    merged = {}

    def do_merge(repo, pr, method):
        merged["ok"] = True
        return 0

    r = safe_merge("o/r", 1, allow_skipped=["e2e-tests"],
                   fetch=_fetch_ok([_run(name="ut"), _run(name="e2e-tests", conclusion="SKIPPED")]),
                   do_merge=do_merge)
    assert r.ok is True and merged.get("ok") is True


def test_wrapper_refuses_unnamed_skip_and_does_not_merge():
    called = {"merge": False}
    r = safe_merge("o/r", 1, allow_skipped=["e2e-tests"],
                   fetch=_fetch_ok([_run(name="unit-tests", conclusion="SKIPPED")]),
                   do_merge=lambda *a: called.__setitem__("merge", True))
    assert r.ok is False and called["merge"] is False


def test_completed_no_conclusion_fails():
    assert classify_check(_run(conclusion=None))[0] == "fail"


# legacy StatusContext (external CI via the commit-status API)
def _ctx(state):
    return {"__typename": "StatusContext", "context": "ci/external", "state": state}


def test_statuscontext_success_passes():
    assert classify_check(_ctx("SUCCESS"))[0] == "pass"


@pytest.mark.parametrize("state", ["PENDING", "EXPECTED"])
def test_statuscontext_pending(state):
    assert classify_check(_ctx(state))[0] == "pending"


@pytest.mark.parametrize("state", ["FAILURE", "ERROR"])
def test_statuscontext_failure(state):
    assert classify_check(_ctx(state))[0] == "fail"


def test_unrecognised_shape_failcloses():
    assert classify_check({"weird": "object"})[0] == "fail"


# ── evaluate_checks: the merge decision ──────────────────────────────────────

def test_all_success_permits_merge():
    d = evaluate_checks([_run(name="unit-tests"), _run(name="lint")])
    assert d.ok is True


def test_zero_checks_refuses():
    # The biggest silent-pass hole: no CI configured must NOT read as all-green.
    d = evaluate_checks([])
    assert d.ok is False and "zero" in d.reason.lower()


def test_non_list_refuses():
    d = evaluate_checks(None)
    assert d.ok is False


def test_one_pending_among_passing_refuses():
    d = evaluate_checks([_run(name="lint"), _run(name="unit-tests", status="IN_PROGRESS", conclusion=None)])
    assert d.ok is False and d.pending and "unit-tests" in " ".join(d.pending)


def test_one_failure_among_passing_refuses():
    d = evaluate_checks([_run(name="lint"), _run(name="unit-tests", conclusion="FAILURE")])
    assert d.ok is False and d.failed


def test_returns_decision_type():
    assert isinstance(evaluate_checks([_run()]), MergeDecision)


# ── safe_merge wrapper: fail-closed around the gh calls ───────────────────────

def _fetch_ok(checks, state="OPEN"):
    return lambda repo, pr: {"state": state, "statusCheckRollup": checks}


def test_wrapper_merges_only_on_all_green():
    merged = {}

    def do_merge(repo, pr, method):
        merged["called"] = (repo, pr, method)
        return 0

    r = safe_merge("o/r", 1, fetch=_fetch_ok([_run()]), do_merge=do_merge)
    assert r.ok is True and merged["called"] == ("o/r", 1, "squash")


def test_wrapper_refuses_and_does_not_merge_on_pending():
    called = {"merge": False}

    def do_merge(repo, pr, method):
        called["merge"] = True
        return 0

    r = safe_merge("o/r", 1, fetch=_fetch_ok([_run(status="IN_PROGRESS", conclusion=None)]), do_merge=do_merge)
    assert r.ok is False and called["merge"] is False


def test_wrapper_failcloses_when_fetch_raises():
    def boom(repo, pr):
        raise RuntimeError("gh API 500")

    called = {"merge": False}
    r = safe_merge("o/r", 1, fetch=boom, do_merge=lambda *a: called.__setitem__("merge", True))
    assert r.ok is False and "fail" in r.reason.lower() and called["merge"] is False


def test_wrapper_refuses_when_pr_not_open():
    r = safe_merge("o/r", 1, fetch=_fetch_ok([_run()], state="MERGED"),
                   do_merge=lambda *a: 0)
    assert r.ok is False and "open" in r.reason.lower()


def test_wrapper_refuses_when_merge_command_fails():
    r = safe_merge("o/r", 1, fetch=_fetch_ok([_run()]),
                   do_merge=lambda repo, pr, method: 1)  # non-zero from gh merge
    assert r.ok is False and "merge" in r.reason.lower()
