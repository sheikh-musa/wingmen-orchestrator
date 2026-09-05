"""tg_send_file.sh must set PYTHONPATH on its operator_log delivery-log call.

WHY THIS EXISTS. tg_send_file.sh logs every outbound file to operator_log with a
bare `-m nervous_system.operator_log` invocation. `-m` resolves the package off
sys.path, and `nervous_system` is a source package in $ORCH_DIR, NOT installed
into the .venv — so the import only succeeds when the caller's CWD happens to be
$ORCH_DIR. From any other CWD it raises `ModuleNotFoundError: No module named
'nervous_system'`, and the call is wrapped in `|| true`, so the delivery log is
SILENTLY DROPPED. A file gets sent to the operator and no operator_log row records
it — the exact "safeguard absent but looks present" class as the reset-dryrun and
nazim_send incidents.

The sibling tg_send.sh:59 already carries the fix (`PYTHONPATH="$ORCH_DIR"` prefix,
ported from the Studio's 25701aa). This was a host-split: the fix landed on one
machine and not the other. tg_send_file.sh is the leg that was never patched —
07-01 audit #11, still open at the 2026-09-05 substrate audit.

The content assertion (below) guards the SHIPPED artifact: the operator_log call
line must carry the PYTHONPATH prefix. The behavioral test proves the guard is
load-bearing — that without it the module genuinely does not resolve from a
foreign CWD, so the assertion is protecting against a real drop and not cosmetics.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
_TG_SEND_FILE = _SCRIPTS / "tg_send_file.sh"
_TG_SEND = _SCRIPTS / "tg_send.sh"

# The delivery-log invocation we care about.
_OPERATOR_LOG_CALL = re.compile(r"-m\s+nervous_system\.operator_log")
# A PYTHONPATH assignment pointing at the orchestrator root must prefix the call
# on the SAME line (bash env-prefix syntax: VAR=val cmd ...).
_PYTHONPATH_PREFIX = re.compile(r'PYTHONPATH=.*ORCH_DIR.*-m\s+nervous_system\.operator_log')


def _code_only(text: str) -> str:
    """The script with comment lines blanked, length-preserving so byte offsets still
    line up. The scripts carry rationale headers that quote the very commands they run;
    matching `-m nervous_system.operator_log` inside a comment would false-pass."""
    out = []
    for line in text.splitlines():
        out.append("" if line.lstrip().startswith("#") else line)
    return "\n".join(out)


def test_scripts_exist():
    # Guard against the paths silently going stale and the suite passing vacuously.
    assert _TG_SEND_FILE.is_file(), f"missing {_TG_SEND_FILE}"
    assert _TG_SEND.is_file(), f"missing {_TG_SEND} (the model that carries the fix)"


def test_sibling_tg_send_already_has_pythonpath():
    # Non-vacuity anchor: the pattern MUST match the known-good sibling. If this fails,
    # the regex is wrong, not tg_send_file.sh — do not "fix" tg_send_file.sh to satisfy it.
    code = _code_only(_TG_SEND.read_text())
    assert _OPERATOR_LOG_CALL.search(code), "tg_send.sh no longer calls operator_log — model changed"
    assert _PYTHONPATH_PREFIX.search(code), (
        "tg_send.sh lost its PYTHONPATH prefix — the reference model regressed"
    )


def test_tg_send_file_operator_log_call_has_pythonpath():
    code = _code_only(_TG_SEND_FILE.read_text())
    assert _OPERATOR_LOG_CALL.search(code), (
        "tg_send_file.sh no longer invokes operator_log in code — test target moved"
    )
    assert _PYTHONPATH_PREFIX.search(code), (
        "tg_send_file.sh calls `-m nervous_system.operator_log` WITHOUT a PYTHONPATH="
        "$ORCH_DIR prefix. Run from any CWD other than $ORCH_DIR the import fails and the "
        "delivery log is silently dropped (|| true). Mirror tg_send.sh:59."
    )


def test_bare_module_invocation_fails_from_foreign_cwd(tmp_path):
    """Load-bearing proof: the bug is real. From a foreign CWD, `import nervous_system`
    fails without PYTHONPATH and succeeds with it — so the PYTHONPATH prefix is what keeps
    the operator_log call from silently ModuleNotFound-ing."""
    py = sys.executable
    # Without PYTHONPATH, from a directory that is NOT $ORCH_DIR: must fail.
    env_no = {"PATH": "/usr/bin:/bin"}
    r_fail = subprocess.run(
        [py, "-c", "import nervous_system"],
        cwd=tmp_path, env=env_no, capture_output=True, text=True,
    )
    assert r_fail.returncode != 0, (
        "expected ModuleNotFoundError importing nervous_system from a foreign CWD with no "
        "PYTHONPATH — if this now passes, the package became installed and the whole class "
        "changed; revisit the fix's necessity"
    )
    assert "ModuleNotFoundError" in r_fail.stderr

    # With PYTHONPATH=$ORCH_DIR: must resolve.
    env_yes = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_ROOT)}
    r_ok = subprocess.run(
        [py, "-c", "import nervous_system"],
        cwd=tmp_path, env=env_yes, capture_output=True, text=True,
    )
    assert r_ok.returncode == 0, (
        f"nervous_system should import with PYTHONPATH={_ROOT}; got: {r_ok.stderr}"
    )
