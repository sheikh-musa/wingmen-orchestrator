"""backlog#135 (orch-console 79249 item 1, 2026-10-28): the post-kickstart deploy probe

must hit the BOUND address (the tailnet IP/port the service actually binds, same as the
rest of deploy_console.sh already uses -- http://100.83.21.34:8787) with a short retry,
and on failure print "COULD NOT MEASURE /api/version" and exit non-zero -- never an empty
health-looking line (fc-v88 deploy logged exactly that on a good deploy: the old code
curled once and echoed whatever came back, blank or not, and always exited 0).

This seam (scripts/lib/console_deploy_probe.sh) is sourced by deploy_console.sh so the
gate's real post-kickstart check and this test call the exact same function.
"""
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PROBE = _ROOT / "scripts" / "lib" / "console_deploy_probe.sh"


def _run(stub_curl_body, attempts=3, delay=0):
    """Source the probe seam with `curl` shadowed by a bash function, then call it."""
    script = f"""
set -uo pipefail
curl() {{
{stub_curl_body}
}}
source "{_PROBE}"
console_deploy_probe "http://100.83.21.34:8787/api/version" "faketoken" {attempts} {delay}
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_probe_prints_version_and_exits_zero_on_first_success():
    out = _run('echo \'{"version":"fc-v93"}\'')
    assert out.returncode == 0, out.stderr
    assert "fc-v93" in out.stdout
    assert out.stderr.strip() == ""


def test_probe_retries_then_succeeds():
    # fails (empty) the first 2 calls, succeeds on the 3rd -- proves the retry loop
    # actually re-invokes curl rather than giving up after one try.
    counter_file = "/tmp/console_deploy_probe_test_counter.$$"
    stub = f"""
n=0
[ -f {counter_file} ] && n=$(cat {counter_file})
n=$((n+1))
echo "$n" > {counter_file}
if [ "$n" -lt 3 ]; then
  echo -n ""
else
  echo '{{"version":"fc-v93"}}'
fi
"""
    try:
        out = _run(stub, attempts=5, delay=0)
        assert out.returncode == 0, out.stderr
        assert "fc-v93" in out.stdout
    finally:
        subprocess.run(["bash", "-c", f"rm -f {counter_file}"])


def test_probe_fails_loud_after_exhausting_retries_never_an_empty_success_line():
    out = _run('echo -n ""', attempts=2, delay=0)
    assert out.returncode != 0
    assert out.stdout.strip() == "", "must never print an empty health-looking success line"
    assert "COULD NOT MEASURE /api/version" in out.stderr
