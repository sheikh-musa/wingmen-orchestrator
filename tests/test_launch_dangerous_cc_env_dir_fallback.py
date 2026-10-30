"""launch_dangerous_cc.sh must not drop the OAuth token / DB creds when invoked
via a worktree's own copy of the script (bus #85137, filed by cc-loop).

Root cause: ORCH_DIR self-locates from this script's own invoked path
(dirname of dirname of BASH_SOURCE[0]) -- correct for finding the repo this
copy of the script lives in, but WRONG for .env / the venv / the token-
pointer files, which are gitignored, live-checkout-only, singleton resources
that never exist inside a worktree. When a pilot/loop-role lane launches via
its own orchestrator-worktree copy of this script, `. "$ORCH_DIR/.env"`
silently no-ops (redirected + `|| true`) and the lane_token_resolver CLI call
resolves its pointer-file default against the same wrong root -- the
confirmed failure mode is a launched session whose transcript shows literal
"Not logged in - Please run /login" (scripts/loop/run_pilot.py's own probe,
which works around this at ITS call site, not at the source script).

The fix adds LIVE_ORCH_DIR + ENV_DIR + a venv-existence fallback for VENV_PY.
This test extracts that exact resolution block (kept text-identical to the
script so a future edit there that drifts from this test shows up as a diff,
not a silent divergence) and runs it under both conditions via a real bash
subprocess -- no mocking, this is shell path-resolution logic that a mock
can't exercise meaningfully.
"""
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path


def _make_fake_python3(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "launch_dangerous_cc.sh"


def _extract_resolution_block() -> str:
    src = SCRIPT.read_text()
    start = src.index('LIVE_ORCH_DIR="$HOME/wingmen/orchestrator"')
    end = src.index("\n\n", start)
    return src[start:end]


_RESOLUTION_BLOCK = _extract_resolution_block()


def _run(orch_dir: Path, live_orch_dir: Path) -> dict:
    snippet = (
        f'ORCH_DIR="{orch_dir}"\n'
        + _RESOLUTION_BLOCK.replace(
            'LIVE_ORCH_DIR="$HOME/wingmen/orchestrator"',
            f'LIVE_ORCH_DIR="{live_orch_dir}"',
        )
        + '\necho "ENV_DIR=$ENV_DIR"\necho "VENV_PY=$VENV_PY"\n'
    )
    out = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.strip().splitlines())


def test_resolution_block_is_present_and_well_formed():
    assert "ENV_DIR" in _RESOLUTION_BLOCK
    assert "VENV_PY" in _RESOLUTION_BLOCK
    assert re.search(r'if \[ -f "\$ORCH_DIR/\.env" \]', _RESOLUTION_BLOCK)
    assert re.search(r'if \[ -x "\$ORCH_DIR/\.venv/bin/python3" \]', _RESOLUTION_BLOCK)


def test_worktree_without_env_or_venv_falls_back_to_live_orch_dir():
    """The reported bug: a worktree checkout has neither .env nor .venv."""
    with tempfile.TemporaryDirectory() as worktree, tempfile.TemporaryDirectory() as live:
        live_path = Path(live)
        (live_path / ".env").write_text("DATABASE_URL=postgres://fake\n")
        _make_fake_python3(live_path / ".venv" / "bin" / "python3")

        result = _run(Path(worktree), live_path)

        assert result["ENV_DIR"] == str(live_path), (
            "a worktree with no .env must fall back to LIVE_ORCH_DIR, not silently "
            "no-op on a worktree-relative ENV_DIR that has no .env to source"
        )
        assert result["VENV_PY"] == str(live_path / ".venv" / "bin" / "python3"), (
            "a worktree with no .venv must fall back to the live checkout's "
            "interpreter, not a non-existent worktree-relative one"
        )


def test_live_checkout_with_env_and_venv_is_unaffected():
    """Byte-identical behaviour in the normal (non-worktree) case: ORCH_DIR
    already has its own .env/.venv, so ENV_DIR/VENV_PY must stay ORCH_DIR-
    relative, never redirected to LIVE_ORCH_DIR."""
    with tempfile.TemporaryDirectory() as orch, tempfile.TemporaryDirectory() as live:
        orch_path = Path(orch)
        (orch_path / ".env").write_text("DATABASE_URL=postgres://real\n")
        _make_fake_python3(orch_path / ".venv" / "bin" / "python3")

        result = _run(orch_path, Path(live))

        assert result["ENV_DIR"] == str(orch_path)
        assert result["VENV_PY"] == str(orch_path / ".venv" / "bin" / "python3")


def test_partial_worktree_missing_only_venv_falls_back_for_venv_only():
    """A dir with .env but no .venv (unusual, but the two checks are
    independent) must fall back for VENV_PY alone, leaving ENV_DIR local."""
    with tempfile.TemporaryDirectory() as orch, tempfile.TemporaryDirectory() as live:
        orch_path = Path(orch)
        (orch_path / ".env").write_text("DATABASE_URL=postgres://real\n")
        live_path = Path(live)
        _make_fake_python3(live_path / ".venv" / "bin" / "python3")

        result = _run(orch_path, live_path)

        assert result["ENV_DIR"] == str(orch_path)
        assert result["VENV_PY"] == str(live_path / ".venv" / "bin" / "python3")


# ── STATIC: every singleton-only $ORCH_DIR/.env reference elsewhere in the
# script must have been migrated to $ENV_DIR, or the fallback above is moot ──

def test_no_remaining_orch_dir_dot_env_references_outside_the_resolution_block():
    src = SCRIPT.read_text()
    # The explanatory comment ABOVE the resolution block legitimately quotes
    # the old `. "$ORCH_DIR/.env"` call as the bug being described -- exclude
    # the comment + code together, not just the code.
    block_start = src.index("# LIVE_ORCH_DIR / ENV_DIR (bus #85137")
    resolution_start = src.index('LIVE_ORCH_DIR="$HOME/wingmen/orchestrator"')
    resolution_end = src.index("\n\n", resolution_start)
    rest = src[:block_start] + src[resolution_end:]
    assert "$ORCH_DIR/.env" not in rest, (
        "every post-resolution reference to the live-only .env must go through "
        "$ENV_DIR, not a worktree-relative $ORCH_DIR -- otherwise the fallback "
        "above is dead code for that call site"
    )
    assert "load_dotenv('$ORCH_DIR/.env')" not in rest


def test_lane_token_resolver_call_passes_orch_dir_explicitly():
    src = SCRIPT.read_text()
    assert "lane_token_resolver --session" in src
    idx = src.index("lane_token_resolver --session")
    line_end = src.index("\n", idx)
    assert '--orch-dir "$ENV_DIR"' in src[idx:line_end], (
        "the resolver's own _default_orch_dir() falls back to wherever its "
        "MODULE FILE lives (worktree-relative under -m scripts.lib.X from a "
        "worktree) -- must pass --orch-dir explicitly so it searches the "
        "live-checkout pointer-file directory regardless of which copy of the "
        "module actually runs"
    )
