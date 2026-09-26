"""Forced 0-table proof (Nazim #43491/#43499): when a pooler flap makes table-enumeration return
0 tables, daily_backup.sh's per-table loop must SURVIVE the empty array under `set -u` and reach
the fail-loud path — not abort with 'unbound variable' (the op#22417 2026-09-27 backup.err trap,
originally an Aug-28 crash). On macOS bash 3.2 an empty `"${arr[@]}"` under set -u raises; the fix
iterates `${arr[@]+"${arr[@]}"}` (zero iterations when empty). Runs the exact patterns via bash."""
import subprocess

import pytest

# The shipped pattern: empty guard fail-louds (FAILED=1), then the empty-SAFE loop survives.
_SAFE = (
    'set -euo pipefail; TABLES=(); FAILED=0; '
    'if [ "${#TABLES[@]}" -eq 0 ]; then FAILED=1; fi; '
    'for T in ${TABLES[@]+"${TABLES[@]}"}; do echo "$T"; done; '
    'echo "SURVIVED FAILED=$FAILED"'
)
# The OLD crashing pattern (negative control) — only reproduces on bash < 4.4 (macOS 3.2).
_UNSAFE = 'set -euo pipefail; TABLES=(); for T in "${TABLES[@]}"; do echo "$T"; done; echo NOCRASH'


def _bash_lt_44():
    r = subprocess.run(["/bin/bash", "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'],
                       capture_output=True, text=True)
    major, minor = (int(x) for x in r.stdout.split())
    return (major, minor) < (4, 4)


def test_empty_table_loop_survives_and_fails_loud():
    r = subprocess.run(["/bin/bash", "-c", _SAFE], capture_output=True, text=True)
    assert r.returncode == 0, f"empty-safe loop must not abort: {r.stderr}"
    assert "SURVIVED FAILED=1" in r.stdout  # guard fail-louded AND the loop survived the empty array
    assert "unbound variable" not in r.stderr


@pytest.mark.skipif(not _bash_lt_44(),
                    reason="empty-array set-u abort is a bash<4.4 (macOS 3.2) quirk; bash>=4.4 doesn't crash")
def test_old_unsafe_loop_would_have_crashed():
    """Negative control: proves the bug the fix removes actually bites on this bash."""
    r = subprocess.run(["/bin/bash", "-c", _UNSAFE], capture_output=True, text=True)
    assert r.returncode != 0
    assert "unbound variable" in r.stderr
