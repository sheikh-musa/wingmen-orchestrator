"""nazim_send_file.sh (and siblings) silently failed on filenames with commas/semicolons
(bus #44576). Seen live: nazim_send_file.sh on "Batch 20_21 Overview (op22917 fix,
DRAFT v1.0).xlsx" exited 26 with NO output and NO operator_messages row.

TWO bugs, tested separately here:

1. curl's own `-F field=@path` parser reads a literal ',' in `path` as the multi-file
   separator (`@file1,file2`), so a real filename containing one makes curl try to open a
   bogus second "file" and exit 26 (READ_ERROR). Fix (scripts/lib/tg_safe_upload.sh):
   stage the file under a syntax-safe symlink name and restore the real name via an
   explicit `;filename="..."` modifier.

2. `code=$(curl ...)` / `resp=$(curl ...)` under `set -euo pipefail` aborts the whole
   script on curl's non-zero exit, so the durable "--undelivered" log line never runs and
   the caller sees a silent failure. Fix: `... || code="curl_exit_$?"` (or the `resp=`
   equivalent) so a curl failure is captured as data instead of killing the script.
"""
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_LIB = _ROOT / "scripts" / "lib" / "tg_safe_upload.sh"
_SCRIPTS = {
    "nazim_send_file.sh": _ROOT / "scripts" / "nazim_send_file.sh",
    "nazim_send_photo.sh": _ROOT / "scripts" / "nazim_send_photo.sh",
    "tg_send_file.sh": _ROOT / "scripts" / "tg_send_file.sh",
    "irsyad_support_send_file.sh": _ROOT / "scripts" / "irsyad_support_send_file.sh",
    "angullia_send_photo.sh": _ROOT / "scripts" / "angullia_send_photo.sh",
}

_STAGE_CALL = re.compile(r"tg_safe_upload_stage\s+\w+\s+")
_CURL_FAILURE_CAPTURED = re.compile(r'\|\|\s*(code|resp)="?\{?[^\n]*curl_exit_\$\?')
_UNSAFE_FORM_VALUE = re.compile(r'-F\s+"(document|photo)=@\$')


def _code_only(text: str) -> str:
    out = []
    for line in text.splitlines():
        out.append("" if line.lstrip().startswith("#") else line)
    return "\n".join(out)


def test_lib_and_scripts_exist():
    assert _LIB.is_file(), f"missing {_LIB}"
    for name, path in _SCRIPTS.items():
        assert path.is_file(), f"missing {path} ({name})"


@pytest.mark.parametrize("name", _SCRIPTS.keys())
def test_script_sources_the_safe_upload_lib(name):
    code = _code_only(_SCRIPTS[name].read_text())
    assert "scripts/lib/tg_safe_upload.sh" in code, (
        f"{name} no longer sources tg_safe_upload.sh — a raw `-F field=@$VAR` reintroduces "
        "the comma/semicolon curl exit-26 bug (bus #44576)"
    )
    assert _STAGE_CALL.search(code), f"{name} does not call tg_safe_upload_stage"
    assert not _UNSAFE_FORM_VALUE.search(code), (
        f'{name} still builds -F "document=@$VAR"/"photo=@$VAR" directly from an unstaged path'
    )


@pytest.mark.parametrize("name", _SCRIPTS.keys())
def test_script_captures_curl_failure_instead_of_aborting(name):
    code = _code_only(_SCRIPTS[name].read_text())
    assert _CURL_FAILURE_CAPTURED.search(code), (
        f"{name}'s curl call has no `|| code/resp=...curl_exit_$?` fallback — under "
        "`set -euo pipefail` a curl failure aborts the script BEFORE the durable "
        "--undelivered log write ever runs (bus #44576)"
    )


def _run_bash(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], cwd=cwd, capture_output=True, text=True, timeout=10,
    )


def test_tg_safe_upload_stage_sanitizes_comma_and_semicolon_filename(tmp_path):
    src = tmp_path / 'Batch 20_21 Overview (op22917 fix, DRAFT v1.0).xlsx'
    src.write_text("payload")

    script = f'''
set -euo pipefail
source "{_LIB}"
tg_safe_upload_stage document "{src}"
echo "FORM:$TG_SAFE_UPLOAD_FORM"
echo "TMPDIR:$TG_SAFE_UPLOAD_TMPDIR"
cat "$TG_SAFE_UPLOAD_TMPDIR"/upload.xlsx
rm -rf "$TG_SAFE_UPLOAD_TMPDIR"
'''
    r = _run_bash(script, tmp_path)
    assert r.returncode == 0, r.stderr

    form_line = next(l for l in r.stdout.splitlines() if l.startswith("FORM:"))
    tmpdir_line = next(l for l in r.stdout.splitlines() if l.startswith("TMPDIR:"))
    form = form_line[len("FORM:"):]
    tmpdir = tmpdir_line[len("TMPDIR:"):]

    # The @-path (before the first unescaped ';') must carry NO comma/semicolon —
    # that's the part curl's own parser splits on.
    at_path = form.split("=@", 1)[1].split(";filename=", 1)[0]
    assert "," not in at_path and ";" not in at_path, f"staged @-path still unsafe: {at_path!r}"
    assert tmpdir in at_path

    # The real filename must survive, quoted, in the filename= modifier.
    assert 'filename="Batch 20_21 Overview (op22917 fix, DRAFT v1.0).xlsx"' in form

    assert "payload" in r.stdout


def test_curl_exit_26_on_raw_comma_path_vs_staged_form_value(tmp_path):
    """Load-bearing proof the fix is real: curl itself, not a mock, exits 26 on a raw
    comma-containing @-path against an unreachable host, and does NOT on the staged form
    value (it instead fails on the network call, proving the -F value parsed clean)."""
    src = tmp_path / "a,b.txt"
    src.write_text("hi")
    unreachable = "http://127.0.0.1:1"  # port 1 refuses instantly, no live network needed

    raw = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "-F", f"document=@{src}", unreachable, "--max-time", "3"],
        capture_output=True, text=True,
    )
    assert raw.returncode == 26, (
        f"expected curl exit 26 (READ_ERROR) on a raw comma-path -F value — got "
        f"{raw.returncode}; if curl's own -F parsing changed, this test (not the fix) "
        "needs revisiting"
    )

    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    safe = staged_dir / "upload.txt"
    safe.symlink_to(src)
    safe_form = f'document=@{safe};filename="a,b.txt"'

    staged = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "-F", safe_form, unreachable, "--max-time", "3"],
        capture_output=True, text=True,
    )
    assert staged.returncode != 26, (
        f"staged form value still trips curl's -F comma-parsing (exit {staged.returncode})"
    )


def test_forced_curl_failure_reaches_the_undelivered_log_branch(tmp_path):
    """Proof of fix #2 in isolation from ORCH_DIR (these scripts hardcode
    ORCH_DIR="$HOME/wingmen/orchestrator" — a fixed real checkout, not this worktree/repo
    checkout — so actually invoking nazim_send_file.sh etc. here would run a DIFFERENT
    file's code, not this PR's diff; see test_script_captures_curl_failure_instead_of_aborting
    for the static proof that the SHIPPED scripts carry the fix). This test instead proves
    the underlying mechanism: under `set -euo pipefail`, a bare `code=$(curl ...)` aborts the
    script before any later line runs, while `code=$(curl ...) || code="curl_exit_$?"` — the
    exact pattern each script now uses — lets the undelivered-log branch execute."""
    script = '''
set -euo pipefail
marker=""
cleanup() { marker="reached"; }
trap cleanup EXIT
code=$(curl -s -o /dev/null http://127.0.0.1:1 --max-time 2 -w "%{http_code}") || code="curl_exit_$?"
[ "$code" = "200" ] || echo "UNDELIVERED:$code"
'''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert "UNDELIVERED:curl_exit_" in r.stdout, (
        f"the undelivered branch did not run after a captured curl failure: {r.stdout!r}"
    )
