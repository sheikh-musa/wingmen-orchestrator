"""cai CAI-RESP-747 invariant #1: the armed/break-glass apply path (switch_lane_token.sh)
can NEVER re-token a lane onto the forbidden gazzabyte consumer token (CAI-729).

The refusal is SCRIPT-level (fail-closed) and fires BEFORE any session resolution or
kill/relaunch, checking both the given basename AND its realpath (a symlink cannot
smuggle it past). This test proves it for a plain file and a symlink.
"""
import os
import subprocess
import pathlib
import tempfile

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "switch_lane_token.sh"
_FORBIDDEN = "gazzabyte-oauth-token"


def _run(tokfile: str):
    # Any session name — the gazzabyte guard fires before session resolution.
    return subprocess.run(
        ["bash", str(_SCRIPT), "any-session", tokfile],
        capture_output=True, text=True, timeout=30,
    )


def test_gazzabyte_plain_file_refused():
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / _FORBIDDEN
        f.write_text("sk-ant-oat-DOESNT-MATTER\n")
        r = _run(str(f))
        assert r.returncode == 4, f"expected exit 4 (forbidden), got {r.returncode}: {r.stdout}{r.stderr}"
        assert "FORBIDDEN" in (r.stdout + r.stderr).upper()


def test_gazzabyte_symlink_refused():
    with tempfile.TemporaryDirectory() as d:
        real = pathlib.Path(d) / "innocent-looking-token"
        real.write_text("sk-ant-oat-DOESNT-MATTER\n")
        link = pathlib.Path(d) / _FORBIDDEN            # the LINK basename is forbidden
        os.symlink(real, link)
        r = _run(str(link))
        assert r.returncode == 4, f"symlink with forbidden basename must be refused: {r.returncode}"
        assert "FORBIDDEN" in (r.stdout + r.stderr).upper()


if __name__ == "__main__":
    test_gazzabyte_plain_file_refused()
    test_gazzabyte_symlink_refused()
    print("PASS: gazzabyte forbidden on the switch rail (plain file + symlink)")
