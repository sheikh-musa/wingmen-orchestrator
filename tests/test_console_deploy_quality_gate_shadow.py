"""Shadow-mode quality_gate.py wiring into deploy_console.sh (bus #43108 proposal,
GO'd bus #43109 2026-09-24, 4 explicit conditions):

  1. Evidence honesty — map to what `deploy-prod` actually requires; a field
     deploy_console.sh cannot attest to must come back ABSENT, never a
     fabricated "pass".
  2. Verdict lands at reports/console-deploy/<hash>/quality-gate-verdict.json,
     not deploy-log.txt.
  3. The shadow call is wrapped so an evaluator exception logs and the deploy
     continues — never blocks or crashes.
  4. A test proves the shadow step cannot change deploy_console.sh's exit code.

This file is that test (condition #4), plus direct coverage of the evidence
builder (condition #1) and the wrapper's write target (condition #2).
"""
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SHADOW = _ROOT / "scripts" / "console_deploy_quality_gate_shadow.sh"
_EVIDENCE = _ROOT / "scripts" / "console_deploy_gate_evidence.py"
_DEPLOY_CONSOLE = _ROOT / "scripts" / "deploy_console.sh"

sys.path.insert(0, str(_ROOT))
from scripts.console_deploy_gate_evidence import build_evidence  # noqa: E402


def test_shadow_wrapper_and_evidence_builder_exist():
    assert _SHADOW.is_file()
    assert _EVIDENCE.is_file()


def test_deploy_console_calls_the_shadow_wrapper_after_gate_4():
    text = _DEPLOY_CONSOLE.read_text()
    gate4_idx = text.index("GATE 4")
    # the header comment mentions the wrapper by name too; the actual invocation
    # is the LAST occurrence, after gate 4's code.
    shadow_idx = text.rindex("console_deploy_quality_gate_shadow.sh")
    deploy_idx = text.index("all gates passed: deploy")
    assert gate4_idx < shadow_idx < deploy_idx, (
        "shadow quality-gate call must run strictly between gate 4 and the deploy step"
    )
    # condition #3: deploy_console.sh must not gate on the shadow call's own exit code.
    shadow_line = next(l for l in text.splitlines() if "console_deploy_quality_gate_shadow.sh" in l)
    assert "|| fail" not in shadow_line, "the shadow call must not be able to fail() the deploy"


# ---- condition #4: the shadow step cannot change deploy_console.sh's exit code ----

def test_shadow_wrapper_always_exits_0_on_missing_deploy_dir(tmp_path):
    missing_dir = tmp_path / "does-not-exist"
    out = subprocess.run(
        ["bash", str(_SHADOW), "deadbeef00000000", str(missing_dir)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    assert out.returncode == 0, out.stderr


def test_shadow_wrapper_always_exits_0_when_python_is_broken(tmp_path):
    """If the interpreter/evaluator itself is broken, the wrapper must still not
    propagate a non-zero exit — deploy_console.sh calls it unconditionally."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    broken_python = fake_bin / "python3"
    broken_python.write_text("#!/usr/bin/env bash\nexit 17\n")
    broken_python.chmod(0o755)
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin"}
    out = subprocess.run(
        ["bash", str(_SHADOW), "deadbeef00000000", str(deploy_dir)],
        capture_output=True, text=True, cwd=str(_ROOT), env=env,
    )
    assert out.returncode == 0, out.stderr


def test_shadow_wrapper_kills_a_hung_evaluator_within_the_timeout(tmp_path):
    """orch-console review (#45683), condition 1: a stuck evaluator must not hang
    deploy_console.sh. QUALITY_GATE_SHADOW_PYTHON points PY at a fake interpreter
    that sleeps far longer than the (shortened, via QUALITY_GATE_SHADOW_TIMEOUT_SEC)
    timeout — the real $ROOT/.venv/bin/python3 always exists in this repo and would
    otherwise be picked before any fake one on PATH, masking this test."""
    import time

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    sleepy_python = fake_bin / "python3"
    sleepy_python.write_text("#!/usr/bin/env bash\nsleep 30\n")
    sleepy_python.chmod(0o755)
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "QUALITY_GATE_SHADOW_PYTHON": str(sleepy_python),
        "QUALITY_GATE_SHADOW_TIMEOUT_SEC": "2",
    }
    started = time.monotonic()
    out = subprocess.run(
        ["bash", str(_SHADOW), "deadbeef00000000", str(deploy_dir)],
        capture_output=True, text=True, cwd=str(_ROOT), env=env, timeout=20,
    )
    elapsed = time.monotonic() - started
    assert out.returncode == 0, out.stderr
    assert elapsed < 15, f"wrapper took {elapsed}s — timeout did not fire"
    err = (deploy_dir / "quality-gate.err").read_text()
    assert "TIMED OUT" in err


def test_shadow_wrapper_always_exits_0_with_pathological_deploy_dir(tmp_path):
    """A deploy-dir that isn't writable (or otherwise pathological) must not turn
    into a non-zero exit for the wrapper — only its OWN arg contract (missing
    positional args) is allowed to fail-fast; anything past that must not."""
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    deploy_dir.chmod(0o500)  # read+execute only, no write
    try:
        out = subprocess.run(
            ["bash", str(_SHADOW), "deadbeef00000000", str(deploy_dir)],
            capture_output=True, text=True, cwd=str(_ROOT),
        )
        assert out.returncode == 0, out.stderr
    finally:
        deploy_dir.chmod(0o700)


def test_shadow_wrapper_writes_verdict_to_quality_gate_verdict_json_not_deploy_log(tmp_path):
    """condition #2: the verdict file name, explicitly not deploy-log.txt."""
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    subprocess.run(
        ["bash", str(_SHADOW), "deadbeef00000000", str(deploy_dir)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    assert (deploy_dir / "quality-gate-verdict.json").exists() or (deploy_dir / "quality-gate.err").exists()
    assert not (deploy_dir / "deploy-log.txt").exists()


def test_shadow_wrapper_produces_a_parseable_verdict_with_real_python(tmp_path):
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    (deploy_dir / "pytest.log").write_text("3 passed in 0.1s\n")
    out = subprocess.run(
        ["bash", str(_SHADOW), "deadbeef00000000", str(deploy_dir)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    assert out.returncode == 0, out.stderr
    verdict_path = deploy_dir / "quality-gate-verdict.json"
    assert verdict_path.is_file(), out.stderr
    verdict = json.loads(verdict_path.read_text())
    assert verdict["change_class"] == "deploy-prod"
    assert verdict["mode"] == "shadow"
    assert verdict["enforced"] is False, "shadow mode must never enforce"


# ---- condition #1: evidence honesty ----

def test_evidence_builder_leaves_unavailable_checks_absent(tmp_path):
    """Fields deploy_console.sh cannot attest to must be OMITTED from checks,
    not fabricated as pass. A bare deploy-dir with none of the console's own
    artifacts must not claim any of the checks the console gate doesn't run."""
    evidence = build_evidence("deadbeef00000000", tmp_path)
    checks = evidence["checks"]
    for absent_check in (
        "typecheck", "lint", "boundaries", "e2e-tests",
        "design-pipeline-artifact-present", "design-dim-verdict-present",
        "screenshot-manifest-1440", "no-console-errors",
        "no-4xx-5xx", "no-error-toast", "no-broken-layout", "empty-states-present",
        "no-secret-leak", "no-pii-leak", "deny-by-default-preserved", "residency-verified",
        "i18n-key-parity", "no-hardcoded-strings", "both-locales-render",
        "deploy-provenance-verified",
        "no-test-rows-on-prod", "no-seed-scaffolding", "migrations-tracked",
        "on-brand", "content-complete", "responsive", "fast-load",
    ):
        assert absent_check not in checks, f"{absent_check} must be absent, not fabricated"
    assert evidence["reviews"] == {"G5": False}, "G5 mandatory review arm must be honestly absent"


def test_evidence_builder_reads_pytest_log(tmp_path):
    (tmp_path / "pytest.log").write_text("5 passed in 0.4s\n")
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["unit-tests"] == "pass"

    (tmp_path / "pytest.log").write_text("1 failed, 4 passed\nFAILED tests/x.py::y\n")
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["unit-tests"] == "fail"


def test_evidence_builder_unit_tests_ignores_benign_warning_text_containing_failed_or_error(tmp_path):
    """cc-quality (PR#275 shadow-run, 2026-10-04): a real, all-green run on a host
    with no local Postgres socket prints a handled UserWarning containing the
    literal substring "failed" ("DB read failed (OperationalError(...)) — falling
    back to the static floor") — this fires on every run on such a host regardless
    of test outcome. A whole-log substring scan for "failed"/"error" wrongly scores
    this "fail"; only pytest's own final summary line (the true last non-blank
    line) is authoritative."""
    (tmp_path / "pytest.log").write_text(
        "nervous_system/protected_agents.py:194: UserWarning: protected_agents: "
        "DB read failed (OperationalError('connection is bad: ... socket \"/tmp/"
        ".s.PGSQL.5432\" failed: No such file or directory')) — falling back to "
        "the static floor\n"
        "  rows = _safe_registry_rows(dsn)\n"
        "\n"
        "-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n"
        "92 passed, 3 warnings in 117.61s (0:01:57)\n"
    )
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["unit-tests"] == "pass"

    # a REAL failure on the summary line must still fail, warnings or not.
    (tmp_path / "pytest.log").write_text(
        "nervous_system/protected_agents.py:194: UserWarning: ... failed ...\n"
        "3 failed, 89 passed, 5 warnings in 100.00s\n"
    )
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["unit-tests"] == "fail"


def test_evidence_builder_unit_tests_requires_a_positive_passed_signal(tmp_path):
    """orch-console review (#45683), condition 2: the old logic scored 'pass'
    whenever neither 'FAILED' nor ' failed' appeared — which is also true of an
    EMPTY or truncated log, or one that blew up during collection before any test
    ran. Require the positive 'N passed' signal, not just the absence of a
    failure marker."""
    (tmp_path / "pytest.log").write_text("")
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["unit-tests"] == "fail", "an empty log must not score pass"

    (tmp_path / "pytest.log").write_text(
        "ERROR tests/conftest.py - ImportError: cannot import name 'x'\n"
        "!!! Interrupted: 1 error during collection !!!\n"
    )
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["unit-tests"] == "fail", "a collection error must not score pass"


def test_evidence_builder_reads_review_presence(tmp_path):
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["evidence-bundle-present"] == "fail"
    (tmp_path / "cc-quality-review.md").write_text("looks good\n")
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["evidence-bundle-present"] == "pass"


def test_evidence_builder_reads_render_pngs(tmp_path):
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert "screenshot-manifest-390" not in evidence["checks"]
    (tmp_path / "fleet.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "lanes.png").write_bytes(b"\x89PNG\r\n")
    evidence = build_evidence("deadbeef00000000", tmp_path)
    assert evidence["checks"]["screenshot-manifest-390"] == "pass"


def test_evidence_through_real_quality_gate_shows_would_block_true(tmp_path):
    """Sanity: an honest, partial evidence set fed through the REAL
    quality_gate.py for deploy-prod must show would_block=True — proving shadow
    mode surfaces the console gate's real gap against the full ihsan floor
    rather than silently agreeing with it."""
    from nervous_system import quality_gate

    evidence = build_evidence("deadbeef00000000", tmp_path)
    verdict = quality_gate.evaluate("deploy-prod", evidence, mode="shadow")
    assert verdict.would_block is True
    assert verdict.enforced is False, "shadow mode must never enforce"
