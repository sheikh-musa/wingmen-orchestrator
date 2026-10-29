"""op#892 (bus #82524/#82668): fail-closed regression guard in
launch_dangerous_cc.sh. lane_env_allowlist.sh no longer passes a bare
DATABASE_URL/SUPABASE_DB_URL through to a lane (renamed to
BUS_DATABASE_URL/BUS_SUPABASE_DB_URL -- a bare-named DATABASE_URL in a lane's
scrubbed env would shadow a client lane's own var of the same name,
TENANT-RESIDENCY-001 commingling risk). This guard refuses the boot if either
bare name is EVER present in the computed _LANE_ENV_PAIRS array -- the only way
that can happen is a future regression re-adding it to the KEEP tier or the
per-lane opt-in map. Runs the real guard slice in bash against a synthetic
_LANE_ENV_PAIRS array; no real secrets, no claude binary, no .env involved.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "launch_dangerous_cc.sh"


def _slice() -> str:
    text = LAUNCHER.read_text()
    m = re.search(r"# >>> bus-dsn-leak-guard\n(.*?)# <<< bus-dsn-leak-guard", text, re.S)
    assert m, "launch_dangerous_cc.sh lost its bus-dsn-leak-guard markers"
    return m.group(1)


def _run(pairs_bash_literal: str) -> subprocess.CompletedProcess:
    script = f'_LANE_ENV_PAIRS=({pairs_bash_literal})\n' + _slice() + '\necho SURVIVED\n'
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True, text=True,
    )


def test_refuses_when_bare_database_url_present():
    out = _run('"OTHER=x" "DATABASE_URL=postgresql://fake-substrate"')
    assert out.returncode == 1
    assert "SURVIVED" not in out.stdout
    assert "SECURITY[op#892]" in out.stderr
    assert "REFUSING to boot" in out.stderr


def test_refuses_when_bare_supabase_db_url_present():
    out = _run('"SUPABASE_DB_URL=postgresql://fake-substrate"')
    assert out.returncode == 1
    assert "SURVIVED" not in out.stdout


def test_passes_with_renamed_bus_vars_only():
    out = _run('"BUS_DATABASE_URL=postgresql://fake-substrate" "BUS_SUPABASE_DB_URL=postgresql://fake-substrate" "GH_TOKEN=x"')
    assert out.returncode == 0
    assert "SURVIVED" in out.stdout
    assert out.stderr == ""


def test_passes_with_empty_pairs_array():
    out = _run("")
    assert out.returncode == 0
    assert "SURVIVED" in out.stdout


def test_does_not_false_positive_on_a_value_containing_the_substring():
    """A value that merely CONTAINS 'DATABASE_URL=' (e.g. as part of a longer
    string) must not trip the guard -- it matches on the PAIR'S OWN leading
    NAME= prefix, not a substring search anywhere in the array."""
    out = _run('"SOME_OTHER_VAR=see DATABASE_URL=foo for details"')
    assert out.returncode == 0
    assert "SURVIVED" in out.stdout
