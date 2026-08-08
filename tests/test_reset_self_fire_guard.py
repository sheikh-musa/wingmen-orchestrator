"""Self-fire guard on the reset_*.sh family (CAI-779 Tier-B).

A singleton/lane body may PREP its own recycle (write a handoff, clean its inbox)
but must NEVER fire its own /clear: run from INSIDE the target session, the
send-keys interleave with the caller's live turn and stage a boot-before-clear
half-state (operator-caught twice, op#11269/11271). The guard resolves the
caller's tmux session from $TMUX_PANE and refuses (exit 5) if it IS the target;
external callers (reset button, ssh, another session, non-tmux shell) pass
(fail-open). Verified correct on reset_nazim.sh @ d975d1a (cc-quality); this locks
the same guard across the propagated siblings.

Two layers:
  * RUNTIME (reset_cai, reset_lane): drive the script against a tmux STUB
    (tests/fixtures/stub_tmux.sh) so no real session is touched — inside => exit 5
    with nothing sent; external => guard passes.
  * STATIC (all three): the guard is present with the canonical shape, keyed to
    each script's OWN $SESS, and placed BEFORE the first send-keys.
reset_orch is hub-host-ONLY (it cd's to the repo, sources .env, and refuses with
exit 4 unless ORCH_BODY_ROLE=hub — which the Mini's console .env is not), so its
guard is unreachable at runtime here; it is covered by the static layer + parity
with the runtime-verified siblings.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUB = REPO / "tests" / "fixtures" / "stub_tmux.sh"

# runtime-reachable on the Mini: (script, target-session, argv-tail)
RUNTIME_CASES = {
    "reset_cai":  ("scripts/reset_cai.sh",  "cai",      []),
    "reset_lane": ("scripts/reset_lane.sh", "testlane", ["testlane", "boot instr"]),
}
GUARDED_SCRIPTS = ["scripts/reset_cai.sh", "scripts/reset_orch.sh", "scripts/reset_lane.sh"]


def _run(script, argv_tail, caller_sess, sendkeys_log):
    env = {
        "TM": str(STUB),
        "TMUX_PANE": "%99",               # pretend we're in *some* tmux pane
        "STUB_CALLER_SESS": caller_sess,   # ...which resolves to this session
        "STUB_SENDKEYS_LOG": str(sendkeys_log),
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
        "HOME": str(Path.home()),
    }
    return subprocess.run(
        ["bash", str(REPO / script), *argv_tail],
        capture_output=True, text=True, cwd=str(REPO), env=env,
    )


@pytest.mark.parametrize("name", list(RUNTIME_CASES))
def test_self_fire_from_inside_target_is_refused(name, tmp_path):
    """Invoked from INSIDE the target session -> refuse exit 5, send NOTHING."""
    script, target, argv = RUNTIME_CASES[name]
    log = tmp_path / "sendkeys.log"
    log.write_text("")
    r = _run(script, argv, caller_sess=target, sendkeys_log=log)
    assert r.returncode == 5, f"{name}: expected exit 5, got {r.returncode}\n{r.stderr}"
    assert "SELF-FIRE REFUSED" in r.stderr, f"{name}: no refusal on stderr:\n{r.stderr}"
    assert log.read_text() == "", f"{name}: keys were SENT despite self-fire:\n{log.read_text()}"


@pytest.mark.parametrize("name", list(RUNTIME_CASES))
def test_external_fire_passes_the_guard(name, tmp_path):
    """Invoked from a DIFFERENT session -> guard passes (no refusal). The script
    may still exit later for its own reasons; we only assert the guard let it by."""
    script, target, argv = RUNTIME_CASES[name]
    log = tmp_path / "sendkeys.log"
    log.write_text("")
    r = _run(script, argv, caller_sess="some-other-session", sendkeys_log=log)
    assert "SELF-FIRE REFUSED" not in r.stderr, f"{name}: guard wrongly refused an external fire:\n{r.stderr}"
    assert r.returncode != 5, f"{name}: external fire got exit 5:\n{r.stderr}"


@pytest.mark.parametrize("script", GUARDED_SCRIPTS)
def test_guard_shape_and_placement(script):
    """Every guarded script carries the canonical guard, keyed to its OWN $SESS,
    placed BEFORE the first send-keys (so a self-fire is caught before any key)."""
    text = (REPO / script).read_text()
    # canonical elements (literal substrings shared by all three guards)
    assert 'display-message -p -t "${TMUX_PANE}"' in text, f"{script}: missing caller-session resolve"
    assert '2>/dev/null || echo' in text, f"{script}: missing fail-open (|| echo)"
    assert '[ "$_caller_sess" = "$SESS" ]' in text, f"{script}: guard not keyed to own $SESS"
    assert "SELF-FIRE REFUSED" in text and "exit 5" in text, f"{script}: missing refusal/exit 5"
    assert "RESET_ALLOW_SELF" in text, f"{script}: missing RESET_ALLOW_SELF override"
    # placement: the refusal must appear before the first real send-keys COMMAND
    # ('send-keys -t ...'; a header-comment mention like "send-keys into tmux" has no -t)
    guard_i = text.index("SELF-FIRE REFUSED")
    sk = text.find("send-keys -t")
    assert sk != -1 and guard_i < sk, f"{script}: guard is AFTER the first send-keys command"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
