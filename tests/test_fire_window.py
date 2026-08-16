"""A recycle must be able to quiesce EVERY keystroke source aimed at its target pane.

WHY THIS EXISTS. `reset_nazim.sh` guards its fire window by `launchctl bootout`-ing one
daemon: `dev.wingmen.nazim-bus-notify`. That daemon is one of at least eight things on
this Mac Mini that can `tmux send-keys` into a body's composer — the operator-ingest
nudger, the fleet-wide wake subscriber, the wedge watchdog, the SLA watchdog, the context
watchdog, backlog_swipe, lane_nudge.sh and the shadow watcher can all type into a pane
that a reset is midway through clearing. A keystroke landing between the composer wipe and
the `/clear` Enter jams the clear, and the body comes back half-initialised holding neither
its old context nor a clean one.

A pause LIST cannot close this: it names the daemons someone remembered, and the next
sender written will not be on it. So the quiesce is a LOCK the senders consult, not a list
the resetter maintains — a new sender is blocked by default rather than blocked if someone
updates a list. The enumeration test below is the half that keeps that true: it fails when
a file learns to send keystrokes without consulting the window.

FAIL-OPEN IS DELIBERATE. An unreadable or expired lock reads as NOT held. A nudge is only a
signal — the payload is always a durable row that survives being missed (Option B) — but a
body that can never be nudged again is silently unreachable, which is the ghost-STAGED
failure that left a lane sitting for five hours on 2026-08-15. Losing a nudge is cheap;
losing a body is not.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.lib import fire_window  # noqa: E402


@pytest.fixture()
def session(tmp_path, monkeypatch):
    """Isolate the lock directory so a test never quiesces a real body."""
    monkeypatch.setattr(fire_window, "STATE_DIR", tmp_path / "fire_window")
    return "test-body"


def test_session_with_no_hold_is_not_held(session):
    assert fire_window.is_held(session) is False


def test_a_fresh_hold_is_held(session):
    fire_window.hold(session, ttl_seconds=60, reason="reset_nazim fire window")
    assert fire_window.is_held(session) is True


def test_a_hold_does_not_cover_a_different_session(session):
    fire_window.hold(session, ttl_seconds=60, reason="reset_nazim fire window")
    assert fire_window.is_held("some-other-body") is False


def test_an_expired_hold_is_not_held(session, monkeypatch):
    fire_window.hold(session, ttl_seconds=60, reason="reset_nazim fire window")
    monkeypatch.setattr(time, "time", lambda: time.time() + 61)
    assert fire_window.is_held(session) is False


def test_release_frees_the_session(session):
    fire_window.hold(session, ttl_seconds=60, reason="reset_nazim fire window")
    fire_window.release(session)
    assert fire_window.is_held(session) is False


def test_release_is_idempotent(session):
    fire_window.release(session)  # never held — must not raise
    assert fire_window.is_held(session) is False


def test_a_corrupt_lock_reads_as_free(session):
    fire_window.hold(session, ttl_seconds=60, reason="reset_nazim fire window")
    (fire_window.STATE_DIR / f"{session}.json").write_text("{not json")
    assert fire_window.is_held(session) is False


def test_ttl_is_clamped_so_a_crashed_reset_cannot_silence_a_body_forever(session):
    fire_window.hold(session, ttl_seconds=99999, reason="runaway")
    payload = json.loads((fire_window.STATE_DIR / f"{session}.json").read_text())
    assert payload["expires_at"] - time.time() <= fire_window.MAX_TTL_SECONDS + 1


def test_hold_records_why_so_a_reader_can_tell_a_live_window_from_a_stale_one(session):
    fire_window.hold(session, ttl_seconds=60, reason="reset_cai fire window")
    assert "reset_cai" in (fire_window.held_reason(session) or "")


def test_cli_check_exits_zero_when_held_and_one_when_free(session, tmp_path):
    env = {"FIRE_WINDOW_DIR": str(fire_window.STATE_DIR), "PATH": "/usr/bin:/bin"}
    cli = [sys.executable, str(_ROOT / "scripts" / "lib" / "fire_window.py")]
    assert subprocess.run(cli + ["check", session], env=env).returncode == 1
    subprocess.run(cli + ["hold", session, "--ttl", "60", "--reason", "cli"], env=env, check=True)
    assert subprocess.run(cli + ["check", session], env=env).returncode == 0
    subprocess.run(cli + ["release", session], env=env, check=True)
    assert subprocess.run(cli + ["check", session], env=env).returncode == 1


# --------------------------------------------------------------------------------------
# The enumeration half: no keystroke sender may exist that does not consult the window.
# --------------------------------------------------------------------------------------

# Files that legitimately send keystrokes WITHOUT consulting the window:
#   * the reset/recycle scripts themselves — they OWN the window; making them honour it
#     would deadlock every recycle against its own hold.
#   * lane_nudge.sh is invoked BY the reset scripts (fleet_model --live, boot paths) as
#     well as by daemons, so it takes the window check in its caller-visible refusal path
#     — it is listed here only if it delegates; see the assertion message.
_WINDOW_OWNERS = {
    "reset_nazim.sh",
    "reset_cai.sh",
    "reset_lane.sh",
    "reset_orch.sh",
    "reset_fleet_health.sh",
    "reset_hub_remote.sh",
    "self_recycle.sh",
}

# Retired bridges: superseded by nervous_system/ingest.py at the 2026-07-03 cutover and no
# longer run by any launchd job. Listed explicitly so the exemption is a decision on the
# record rather than an oversight — if one is ever revived it must take the guard first.
_RETIRED = {"tg_bridge.py", "cai_bridge.py"}

_SEND_KEYS_PY = re.compile(r"""["']send-keys["']""")
_SEND_KEYS_SH = re.compile(r"\btmux\s+send-keys\b")


def _sends_keystrokes(path: Path) -> bool:
    pattern = _SEND_KEYS_SH if path.suffix == ".sh" else _SEND_KEYS_PY
    for line in path.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("*"):
            continue  # a comment describing send-keys is not a send-keys
        if pattern.search(stripped):
            return True
    return False


def _keystroke_senders() -> list:
    out = []
    for d in ("nervous_system", "scripts", "scripts/lib"):
        for path in sorted((_ROOT / d).glob("*.py")) + sorted((_ROOT / d).glob("*.sh")):
            if path.name in _WINDOW_OWNERS or path.name in _RETIRED:
                continue
            if _sends_keystrokes(path):
                out.append(path)
    return out


def test_there_is_at_least_one_keystroke_sender_to_check():
    """Guards the guard: a broken detector would make the test below vacuously pass."""
    assert len(_keystroke_senders()) >= 5


# reset_hub_remote.sh is a pure ssh wrapper: it execs the reset ON the hub, where the pane
# actually lives. A hold taken HERE would protect nothing there — the lock is per-host, so
# the hub's own reset_orch.sh is what quiesces the hub's senders. Exempted deliberately,
# and the residual gap is real and named: a nudge sent from THIS host over ssh into the
# hub's pane is not covered by either lock.
_RESETTERS = [p for p in sorted((_ROOT / "scripts").glob("reset_*.sh"))
              if p.name != "reset_hub_remote.sh"]


@pytest.mark.parametrize("path", _RESETTERS, ids=lambda p: p.name)
def test_every_reset_takes_the_hold_before_its_first_keystroke(path):
    """The ordering is the load-bearing half — a hold taken AFTER the first send-keys
    would satisfy a naive grep while leaving the whole wipe unprotected, which is exactly
    the bug this closes."""
    lines = path.read_text(errors="ignore").splitlines()
    hold_at = next((i for i, ln in enumerate(lines)
                    if "fire_window" in ln and "hold" in ln and not ln.strip().startswith("#")), None)
    send_at = next((i for i, ln in enumerate(lines)
                    if _SEND_KEYS_SH.search(ln) and not ln.strip().startswith("#")), None)
    assert hold_at is not None, (
        f"{path.name} clears a live pane but never takes a fire-window hold, so every "
        f"other keystroke sender on this host is free to type into the pane it is midway "
        f"through clearing. Take the hold before the first send-keys and release it on EXIT."
    )
    if send_at is not None:
        assert hold_at < send_at, (
            f"{path.name} takes the fire-window hold at line {hold_at + 1}, AFTER its first "
            f"send-keys at line {send_at + 1} — the wipe it is meant to protect has already "
            f"happened by then."
        )


@pytest.mark.parametrize("path", _RESETTERS, ids=lambda p: p.name)
def test_every_reset_releases_the_hold_on_exit(path):
    """A hold that outlives a crashed reset would make the body unreachable until the TTL
    expires. The TTL bounds the damage; the trap prevents it."""
    body = path.read_text(errors="ignore")
    assert "fire_window" in body and "release" in body, (
        f"{path.name} must release its fire-window hold from an EXIT trap so a mid-fire "
        f"crash cannot leave the body quiesced for the whole TTL."
    )


@pytest.mark.parametrize("path", _keystroke_senders(), ids=lambda p: p.name)
def test_every_keystroke_sender_consults_the_fire_window(path):
    body = path.read_text(errors="ignore")
    assert "fire_window" in body, (
        f"{path.relative_to(_ROOT)} sends tmux keystrokes into a body's pane but never "
        f"consults the fire window, so it can type into a pane mid-recycle and jam the "
        f"/clear. Check fire_window.is_held(<session>) (or `fire_window.py check`) before "
        f"the send and skip the nudge when the window is held — the payload is a durable "
        f"row, so a skipped nudge costs nothing."
    )
