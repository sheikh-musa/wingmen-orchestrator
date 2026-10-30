"""Regression test for scripts/safe_merge.sh's OWN --allow-skipped flag parsing.

tests/test_merge_gate.py already covers scripts/lib/merge_gate.evaluate_checks (the
Python decision). What was UNTESTED is safe_merge.sh itself: the bash `ALLOW=()` array
that accumulates each `--allow-skipped <name>` and passes them through to merge_gate.py's
CLI via `"${ALLOW[@]+"${ALLOW[@]}"}"`.

The Mac Mini runs /bin/bash 3.2, whose empty-array / array-expansion handling under
`set -u` is exactly where that guard earns its keep. These tests run the REAL script as a
subprocess with a stubbed `gh` on PATH (tests/fixtures/fake_gh/gh) — no network, no real
PR — and assert the end-to-end exit codes, with the headline case being TWO repeated
`--allow-skipped` flags both making it through (the actual gap).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SAFE_MERGE = _ROOT / "scripts" / "safe_merge.sh"
_FAKE_GH_DIR = Path(__file__).resolve().parent / "fixtures" / "fake_gh"

_HEAD = "deadbeefcafe0000deadbeefcafe0000deadbeef"


def _bash():
    """Prefer /bin/bash — on the Mini that's the 3.2 the script actually runs under, the
    whole point of this regression test. Fall back to PATH bash elsewhere (the guard is
    version-agnostic, so both must pass)."""
    return "/bin/bash" if os.path.exists("/bin/bash") else "bash"


def _run(allow_flags, checks, *, state="OPEN", mergeable="MERGEABLE"):
    """Invoke the REAL safe_merge.sh --dry-run with `gh` stubbed on PATH.

    allow_flags: list of bare check names to pass as repeated --allow-skipped.
    checks: list of (name, state) tuples -> the canned `gh pr checks` JSON array.
    """
    import json

    checks_json = json.dumps([{"name": n, "state": s} for n, s in checks])
    env = {
        **os.environ,
        "PATH": f"{_FAKE_GH_DIR}{os.pathsep}{os.environ['PATH']}",
        "STUB_STATE": state,
        "STUB_MERGEABLE": mergeable,
        "STUB_HEAD": _HEAD,
        "STUB_CHECKS_JSON": checks_json,
    }
    cmd = [_bash(), str(_SAFE_MERGE), "--repo", "owner/repo", "123", "--dry-run"]
    for name in allow_flags:
        cmd += ["--allow-skipped", name]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)


def test_fake_gh_is_the_one_we_shadow():
    """Guard the guard: our stub must win PATH resolution, never the real gh binary."""
    env = {**os.environ, "PATH": f"{_FAKE_GH_DIR}{os.pathsep}{os.environ['PATH']}"}
    assert shutil.which("gh", path=env["PATH"]) == str(_FAKE_GH_DIR / "gh")


def test_no_allow_skipped_refuses_a_skipped_check():
    """Zero --allow-skipped flags + one SKIPPED check -> fail-closed REFUSE (exit 3)."""
    out = _run(
        allow_flags=[],
        checks=[("Lint & Test", "SUCCESS"), ("e2e-tests", "SKIPPED")],
    )
    assert out.returncode == 3, (out.stdout, out.stderr)
    assert "REFUSE" in out.stderr


def test_one_allow_skipped_accepts_that_check():
    """One --allow-skipped <name> waives exactly that SKIPPED check -> ACCEPT (exit 0)."""
    out = _run(
        allow_flags=["e2e-tests"],
        checks=[("Lint & Test", "SUCCESS"), ("e2e-tests", "SKIPPED")],
    )
    assert out.returncode == 0, (out.stdout, out.stderr)
    assert "DRY-RUN" in out.stdout


def test_two_repeated_allow_skipped_both_pass_through():
    """THE GAP: two separate --allow-skipped flags must BOTH reach merge_gate.py.

    If the bash ALLOW=() array dropped or mangled either value (a 3.2 array quirk), one
    of foo/bar would stay un-waived and the whole run would REFUSE. ACCEPT proves both
    accumulated and passed through."""
    out = _run(
        allow_flags=["foo", "bar"],
        checks=[("Lint & Test", "SUCCESS"), ("foo", "SKIPPED"), ("bar", "SKIPPED")],
    )
    assert out.returncode == 0, (out.stdout, out.stderr)
    assert "DRY-RUN" in out.stdout


def test_two_repeated_allow_skipped_still_refuses_an_unlisted_skip():
    """Complement to the gap test: waiving foo+bar must NOT waive a third SKIPPED check —
    i.e. the pass-through is exact, not a blanket 'allow all skips' once any flag appears."""
    out = _run(
        allow_flags=["foo", "bar"],
        checks=[("foo", "SKIPPED"), ("bar", "SKIPPED"), ("baz", "SKIPPED")],
    )
    assert out.returncode == 3, (out.stdout, out.stderr)
    assert "REFUSE" in out.stderr
