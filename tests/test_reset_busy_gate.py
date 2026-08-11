"""BUSY gate on the reset_*.sh family (op#18941 / [[reset-needs-sustained-idle-and-busy-gate]]).

A /clear discards whatever turn is in flight, so a reset must REFUSE a BUSY body
(exit 5, unless RESET_FORCE=1 loudly overrides). reset_orch.sh and reset_cai.sh
have carried this gate; reset_nazim.sh did NOT — so a console reset fired on a
transient-idle read could race a body that was actually WORKING (op#18941: the
oracle's first real WORKING catch, overridden on a single idle sample -> the
reset didn't take). The shared pane_busy (composer_capture.sh) now also catches
extended-thinking turns (c0818b6), so this gate structurally refuses a thinking
body too — the exact case op#18941 missed.

Layers (mirrors tests/test_reset_self_fire_guard.py):
  * RUNTIME (reset_nazim, reset_cai): drive the script against the tmux STUB with
    an injected BUSY pane ('esc to interrupt') -> exit 5, nothing sent; with
    RESET_FORCE=1 -> the busy gate is overridden LOUDLY (does not exit 5 for busy).
  * STATIC (all three): the pane_busy busy gate is present, keyed to CC_BUSY,
    with a RESET_FORCE escape hatch, placed BEFORE the first send-keys command.

reset_orch is hub-host-ONLY (cd + ORCH_BODY_ROLE=hub gate), unreachable at runtime
here, so it is covered by the static layer + parity with the runtime-verified
siblings — same split as the self-fire test.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUB = REPO / "tests" / "fixtures" / "stub_tmux.sh"

BUSY_TEXT = "some transcript line\n\n  esc to interrupt"  # the foreground-turn marker

# runtime-reachable on the Mini: (script, target-session, argv-tail)
RUNTIME_CASES = {
    "reset_nazim": ("scripts/reset_nazim.sh", "nazim", []),
    "reset_cai":   ("scripts/reset_cai.sh",   "cai",   []),
}
# every reset script that must carry a busy gate
GUARDED_SCRIPTS = ["scripts/reset_nazim.sh", "scripts/reset_cai.sh", "scripts/reset_orch.sh"]


def _run(script, argv_tail, pane_text, sendkeys_log, extra_env=None):
    env = {
        "TM": str(STUB),
        # external caller: a DIFFERENT session, so the self-fire guard passes and
        # execution reaches the busy gate.
        "TMUX_PANE": "%99",
        "STUB_CALLER_SESS": "some-other-session",
        "STUB_CAPTURE_PANE_TEXT": pane_text,
        "STUB_SENDKEYS_LOG": str(sendkeys_log),
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
        "HOME": str(Path.home()),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(REPO / script), *argv_tail],
        capture_output=True, text=True, cwd=str(REPO), env=env,
    )


@pytest.mark.parametrize("name", list(RUNTIME_CASES))
def test_busy_body_is_refused(name, tmp_path):
    """A BUSY pane -> refuse exit 5, send NOTHING (the /clear would discard the turn)."""
    script, target, argv = RUNTIME_CASES[name]
    log = tmp_path / "sendkeys.log"
    log.write_text("")
    r = _run(script, argv, pane_text=BUSY_TEXT, sendkeys_log=log)
    assert r.returncode == 5, f"{name}: expected exit 5 on busy, got {r.returncode}\n{r.stderr}"
    assert "BUSY" in r.stderr, f"{name}: no BUSY refusal on stderr:\n{r.stderr}"
    assert log.read_text() == "", f"{name}: keys SENT into a busy body:\n{log.read_text()}"


@pytest.mark.parametrize("name", list(RUNTIME_CASES))
def test_busy_reset_force_overrides_loudly(name, tmp_path):
    """RESET_FORCE=1 on a busy body -> the busy gate does NOT exit 5; it warns loudly.
    (Later gates may still stop it; we only assert the busy gate was overridden.)"""
    script, target, argv = RUNTIME_CASES[name]
    log = tmp_path / "sendkeys.log"
    log.write_text("")
    r = _run(script, argv, pane_text=BUSY_TEXT, sendkeys_log=log,
             extra_env={"RESET_FORCE": "1", "RESET_DRYRUN": "1"})
    assert "ANYWAY" in r.stderr, f"{name}: RESET_FORCE did not loudly override busy:\n{r.stderr}"
    # the busy gate itself must not be the thing that stopped it
    assert not (r.returncode == 5 and "refusing to clear" in r.stderr.lower()), \
        f"{name}: busy gate refused despite RESET_FORCE=1:\n{r.stderr}"


@pytest.mark.parametrize("script", GUARDED_SCRIPTS)
def test_busy_gate_shape_and_placement(script):
    """Every reset script carries the pane_busy busy gate, keyed to CC_BUSY, with a
    RESET_FORCE escape hatch, placed BEFORE the first send-keys command."""
    text = (REPO / script).read_text()
    assert 'pane_busy "$TM" "$PANE"' in text, f"{script}: missing pane_busy call"
    assert '"$CC_BUSY" = 1' in text, f"{script}: missing CC_BUSY verdict check"
    assert "RESET_FORCE" in text, f"{script}: missing RESET_FORCE override on busy"
    # placement: the busy verdict must be evaluated before the first real send-keys
    busy_i = text.index('pane_busy "$TM" "$PANE"')
    sk = text.find("send-keys -t")
    assert sk != -1 and busy_i < sk, f"{script}: busy gate is AFTER the first send-keys command"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
