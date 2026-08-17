"""CAI-985 A3 runner outcome logic (cc-fleet-health, 2026-08-17).

The runner reads ceayj's catalogue (under orch-console, FORK1=b), decides PASS/FAIL/ERROR, and
writes the substrate sink. These pin the pure decision — the D4 negative control DOMINATES:
if the control (a scope KNOWN to carry untrusted grants) comes back empty, the detector's
positive path is broken and NO result can be trusted, so a 'clean' main scan must NOT read PASS.
That is D6 fail-closed + D4 'a detector never observed detecting is not known to be one'.
"""
from scripts.a3_isolation_check import decide_outcome, PASS, FAIL, ERROR


def test_clean_scope_with_working_control_is_pass():
    outcome, detail = decide_outcome(findings_count=0, negcontrol_count=7)
    assert outcome == PASS, detail


def test_untrusted_grants_found_is_fail():
    outcome, detail = decide_outcome(findings_count=3, negcontrol_count=7)
    assert outcome == FAIL and "3" in detail


def test_broken_negative_control_is_error_even_when_scope_is_clean():
    # D4/D6: control returned 0 -> positive path broken -> a clean main scan is NOT trustworthy.
    outcome, detail = decide_outcome(findings_count=0, negcontrol_count=0)
    assert outcome == ERROR, "a clean scan with a broken control must be ERROR, never PASS"


def test_broken_negative_control_dominates_findings_too():
    # if the control is broken we cannot trust ANY output, including a FAIL.
    outcome, _ = decide_outcome(findings_count=5, negcontrol_count=0)
    assert outcome == ERROR


def test_main_loads_dotenv_from_workdir_before_reading_dsns(monkeypatch):
    # Nazim #24321: launchd inherits a minimal env, so the runner MUST load the WorkingDirectory
    # .env itself (works-by-hand-fails-under-scheduler, the fire-window fail-open class). Guard it.
    import scripts.a3_isolation_check as runner
    seen = {}
    monkeypatch.setattr(runner, "load_dotenv", lambda path=None, *a, **k: seen.setdefault("path", path))
    monkeypatch.setenv("IHSANOS_PROD_DATABASE_URL", "x")   # present so it proceeds past the DSN gate
    monkeypatch.setenv("DATABASE_URL", "y")
    monkeypatch.setattr(runner, "run_a3_check",
                        lambda **k: {"outcome": PASS, "detail": "", "finding_count": 0,
                                     "fail_count": 0, "info_count": 0, "negcontrol_count": 1})
    rc = runner.main(["--dry-run"])
    assert rc == 0
    assert str(seen.get("path", "")).endswith(".env"), \
        f"main must load_dotenv from the workdir .env (else it fails under launchd), got {seen}"
