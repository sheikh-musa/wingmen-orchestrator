"""account_for_fp (scripts/lib/token_file_guard.sh), the reverse fp->account lookup
launch_dangerous_cc.sh uses to LABEL a lane's agent_status row (orch-console #51865).

Drives the ACTUAL shipped bash function via subprocess (gate-test != shipped-path),
not a transcription of its logic -- cc-quality's PR#291 review finding: this file
already pins other static properties of launch_dangerous_cc.sh
(test_boot_scripts_strip_api_key.py) but had no regression test for this new
derivation, so a future edit could silently reintroduce the stale-label bug this
fixed (a lane's auth_account label never matching its actual auth_fp after a
re-token).
"""
import os
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_ROOT, "scripts", "lib", "token_file_guard.sh")


def _account_for_fp(fp, map_path):
    out = subprocess.run(
        ["bash", "-c", 'source "$1"; account_for_fp "$2" "$3"',
         "_", _LIB, fp, map_path],
        capture_output=True, text=True, cwd=_ROOT,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.rstrip("\n")


def _write_map(tmp_path, *rows):
    p = tmp_path / "token_fps.map"
    p.write_text("\n".join("%s %s" % r for r in rows) + "\n")
    return str(p)


def test_known_fp_resolves_to_its_account(tmp_path):
    m = _write_map(tmp_path, ("musa", "68142948c003"), ("musa2", "e1dfa48eec85"), ("syed", "582043088eae"))
    assert _account_for_fp("68142948c003", m) == "musa"
    assert _account_for_fp("e1dfa48eec85", m) == "musa2"
    assert _account_for_fp("582043088eae", m) == "syed"


def test_unmapped_fp_resolves_to_empty_not_a_guess(tmp_path):
    m = _write_map(tmp_path, ("musa", "68142948c003"))
    assert _account_for_fp("deadbeef0000", m) == ""


def test_empty_fp_resolves_to_empty(tmp_path):
    m = _write_map(tmp_path, ("musa", "68142948c003"))
    assert _account_for_fp("", m) == ""


def test_missing_map_fails_closed_to_empty_not_an_exception(tmp_path):
    assert _account_for_fp("68142948c003", str(tmp_path / "no-such-map")) == ""


def test_comment_lines_are_not_matched_as_a_fp(tmp_path):
    """cc-quality's PR#292 mutation-test finding: a space-after-'#' fixture
    ("# musa 68142948c003") stays green even with the comment-skip guard fully
    deleted, because that spacing shifts awk's field 2 off the fp entirely --
    vacuous. The real map convention (its own header row) is NO space after '#'.
    This reproduces cc-quality's exact repro: a commented-out line carrying the
    SAME fp as a real entry, appearing FIRST -- without the guard, awk's
    first-match-wins would return the comment's field 1 ("#musa2") instead of
    the real account below it."""
    p = tmp_path / "token_fps.map"
    p.write_text("#musa2 e1dfa48eec85\nmusa2 e1dfa48eec85\n")
    assert _account_for_fp("e1dfa48eec85", str(p)) == "musa2"


def test_against_the_real_shipped_map():
    """The actual scripts/lib/token_fps.map this fleet runs on, not a fixture --
    pins the exact incident scenario (#51865): a re-tokened lane's fp must resolve
    to its CURRENT account even if a stale $CLAUDE_ACCOUNT_LABEL still says otherwise."""
    real_map = os.path.join(_ROOT, "scripts", "lib", "token_fps.map")
    assert _account_for_fp("e1dfa48eec85", real_map) == "musa2"
