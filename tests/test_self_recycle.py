"""self_recycle must survive a body that keeps being woken.

WHY THIS EXISTS. cai fired self_recycle.sh at 2026-08-16 ~12:5xZ. The detach worked and the
process survived, but when it fired at DELAY the body was mid-turn — a `[wake] new inbox item`
nudge had arrived inside the 60s window — and reset_cai's BUSY gate correctly refused. The tool
was not broken and the gate was not wrong; the SHAPE was. A one-shot "fire once at T+60" assumes
the body will be idle at exactly T+60, and a body on a live bus is woken precisely because it is
the kind of body worth recycling.

Two fixes, and they compose:
  * TAKE THE FIRE-WINDOW HOLD FOR THE WHOLE WAIT, not just inside the reset. The hold stops the
    nudgers typing, so nothing NEW can start a turn while we wait for the current one to end.
    Held early it prevents the collision; held only inside the reset (as reset_*.sh does) it is
    already too late — the busy gate has run by then.
  * WAIT FOR IDLE rather than firing blind. Poll until the pane is idle, then fire, with a bounded
    timeout so a genuinely stuck body fails loudly instead of hanging forever.

The alternative — "recycle me externally from an idle moment" — puts the operator's thumb back on
the button, which is the thing this whole line of work exists to remove.
"""
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "self_recycle.sh"
_SRC = _SCRIPT.read_text()


@pytest.fixture()
def handoff(tmp_path):
    """A FRESH, real-sized restore point — the freshness gate is measured against the clock,
    so a fixture file is the honest way to exercise everything downstream of it."""
    p = tmp_path / "handoff-NOW.md"
    p.write_text("# fresh handoff\n" + ("state line\n" * 200))
    return str(p)


@pytest.fixture()
def boot_file(tmp_path):
    """A worker lane's boot instruction, on disk rather than inline. The boot text is
    multi-line prose containing quotes and backticks; routing it through a FILE keeps it
    out of the string-interpolated subshell that schedules the reset."""
    p = tmp_path / "boot.txt"
    p.write_text('You are cc-storefront, freshly reset. Read `reports/cc-storefront-handoff-NOW.md`\n'
                 'IN FULL — the "FINAL STATE" block first — then reconcile your bus inbox.\n')
    return str(p)


def _run(*args, **kw):
    return subprocess.run(["bash", str(_SCRIPT), *args], capture_output=True, text=True,
                          cwd=str(_ROOT), **kw)


def _write_over_cap_handoff(path):
    """A structured handoff comfortably OVER the 60KB compaction cap, section[0] first."""
    title = "# handoff\n\npreamble\n"
    body = title + "\n".join(f"## SEC-{i} block {i}\n" + ("x" * 3000) + "\n" for i in range(40))
    Path(path).write_text(body)


# ── Item-3 fast-follow (cc-quality F1): the singleton (bash) seam's staged compaction and
# its DEAD-MAN'S-SWITCH were shipped untested. These cover the abort AND the staging on the
# path that guards cai/nazim/fleet-health.

def test_bash_seam_enabled_body_previews_compaction_then_proceeds(tmp_path):
    """An ENABLED singleton (fleet-health) with an over-cap handoff: dry-run PREVIEWS the
    compaction and PROCEEDS past it (gates passed, nothing scheduled) — proving compaction
    does not abort the flow on the happy path."""
    h = tmp_path / "fleet-health-handoff-NOW.md"
    _write_over_cap_handoff(h)
    out = _run("--reset", "scripts/reset_fleet_health.sh", "--handoff", str(h), "--dry-run")
    assert out.returncode == 0, out.stderr
    # F2: the agent id is now passed + preferred in the label (dual-path parity backstop).
    assert "WOULD compact for cc-fleet-health" in out.stdout
    assert "gates passed, NOTHING scheduled" in out.stdout   # proceeded past compaction
    assert h.read_text().startswith("# handoff")             # dry-run wrote nothing


def test_bash_seam_cai_is_held(tmp_path):
    """cai is tier-3 HELD: even an over-cap handoff is SKIPPED on the bash seam (verified on
    BOTH derived --session cai AND derived --agent cai)."""
    h = tmp_path / "cai-handoff-NOW.md"
    _write_over_cap_handoff(h)
    out = _run("--reset", "scripts/reset_cai.sh", "--handoff", str(h), "--dry-run")
    assert out.returncode == 0, out.stderr
    assert "SKIP (tier held: cai)" in out.stdout


def test_bash_seam_compaction_failure_ABORTS_before_scheduling(tmp_path):
    """DEAD-MAN'S-SWITCH: a REAL compaction I/O failure (here: the .bak write fails because the
    handoff's directory is read-only) must make self_recycle exit 6 and NOT schedule the reset.
    No test seam — a genuine PermissionError drives the abort."""
    d = tmp_path / "ro"
    d.mkdir()
    h = d / "fleet-health-handoff-NOW.md"
    _write_over_cap_handoff(h)          # >60KB so compaction actually fires (not a no-op)
    d.chmod(0o500)                      # readable+executable, NOT writable -> .bak write fails
    try:
        out = _run("--reset", "scripts/reset_fleet_health.sh", "--handoff", str(h))  # NON-dry
    finally:
        d.chmod(0o700)                  # restore so pytest can clean up
    assert out.returncode == 6, (out.returncode, out.stdout, out.stderr)
    assert "compaction FAILED" in out.stderr
    assert "SCHEDULED" not in out.stdout          # reset was NEVER scheduled
    assert h.read_text().startswith("# handoff")  # original restore point untouched


# --- worker lanes, not just the four singletons ----------------------------- #
# cc-storefront tried to self-recycle on 2026-08-16 and could not: self_recycle fires the
# reset with NO arguments (`bash "$RST"`), which is fine for reset_nazim/cai/fleet_health/orch
# (each hardcodes its own pane) but never works for scripts/reset_lane.sh, whose signature is
# `reset_lane.sh <session> "<boot>"`. So the tool that exists to stop the operator pushing the
# button worked for exactly four bodies and no worker lane — found by a lane USING it, which is
# the same class as the console assign button that was dead for months.
#
# The dry-run tests above passed throughout, because dry-run returns before the fire path. A
# gate test is not a shipped-path test.

def test_a_lane_reset_without_its_boot_instruction_is_refused(handoff):
    """reset_lane.sh REQUIRES a boot instruction. Scheduling it without one buys a body that
    gets /clear'd and then comes back with no idea who it is — strictly worse than not
    recycling. Refuse at schedule time, where the caller can still fix it."""
    out = _run("--reset", "scripts/reset_lane.sh", "--session", "storefront",
               "--handoff", handoff, "--dry-run")
    assert out.returncode != 0, (
        "a parameterized reset scheduled with no boot instruction must be refused, not queued"
    )
    assert "boot" in (out.stdout + out.stderr).lower()


def test_boot_file_reaches_the_reset_script_as_an_argument(handoff, boot_file):
    """The whole gap: the scheduled command must invoke the reset WITH the lane's session and
    boot text. Asserted against the source of the fire path rather than the dry-run summary,
    because the dry-run is exactly what failed to catch this."""
    assert 'bash "$RST" "$SESS"' in _SRC, (
        "the reset must be invoked WITH the lane's session and boot text; firing it bare is "
        "exactly what made self_recycle singleton-only"
    )
    out = _run("--reset", "scripts/reset_lane.sh", "--session", "storefront",
               "--boot-file", boot_file, "--handoff", handoff, "--dry-run")
    assert out.returncode == 0, out.stderr


# --- paths are resolved from where the CALLER stands ------------------------ #
# self_recycle cd's to the orchestrator dir before it does anything, so a lane that typed the
# path that works in ITS shell — `reports/my-handoff-NOW.md`, sitting in its own worktree — got
# "handoff does not exist". Loud, so not dangerous, but every worker lane keeps its handoff in
# its worktree and would hit it in turn. A trap that each body has to be individually warned
# about is a trap; resolve from the caller's cwd, and when it is genuinely missing say BOTH
# places that were searched rather than just one.

def test_a_handoff_relative_to_the_callers_cwd_is_found(tmp_path):
    """The lane's own worktree is where its handoff lives, and `cd`-ing away from the caller is
    an implementation detail of this script, not something the caller should have to know."""
    p = tmp_path / "reports"
    p.mkdir()
    (p / "lane-handoff-NOW.md").write_text("# fresh\n" + ("state line\n" * 200))
    boot = tmp_path / "boot.txt"
    boot.write_text("You are cc-lane, freshly reset.\n")
    out = subprocess.run(
        ["bash", str(_SCRIPT), "--reset", "scripts/reset_lane.sh", "--session", "somelane",
         "--boot-file", "boot.txt", "--handoff", "reports/lane-handoff-NOW.md", "--dry-run"],
        capture_output=True, text=True, cwd=str(tmp_path))
    assert out.returncode == 0, (out.stdout + out.stderr)


def test_a_genuinely_missing_handoff_names_everywhere_it_looked(tmp_path):
    """A gate that says only 'not found' sends the reader hunting. Print both roots."""
    out = subprocess.run(
        ["bash", str(_SCRIPT), "--reset", "scripts/reset_lane.sh", "--session", "somelane",
         "--boot-file", "nope-boot.txt", "--handoff", "reports/nope-NOW.md", "--dry-run"],
        capture_output=True, text=True, cwd=str(tmp_path))
    assert out.returncode != 0
    blob = out.stdout + out.stderr
    assert str(tmp_path) in blob and str(_ROOT) in blob, (
        "the refusal must name BOTH the caller's cwd and the orch dir, so the reader can see "
        "where it actually searched"
    )


def test_a_missing_boot_file_is_caught_before_anything_is_scheduled(handoff):
    out = _run("--reset", "scripts/reset_lane.sh", "--session", "storefront",
               "--boot-file", "/nope/does-not-exist.txt", "--handoff", handoff, "--dry-run")
    assert out.returncode != 0
    assert "boot" in (out.stdout + out.stderr).lower()


def test_dry_run_reports_the_session_it_would_quiesce_and_wait_on(handoff):
    """The caller must be able to SEE which pane this will act on before it fires — a
    self_recycle aimed at the wrong session is a body cleared that never asked."""
    out = _run("--reset", "scripts/reset_cai.sh",
               "--handoff", handoff, "--dry-run")
    assert out.returncode == 0, out.stderr
    assert re.search(r"session[: ]+cai", out.stdout + out.stderr, re.I), (
        "dry-run must name the target session (derived from the reset script or --session)"
    )


def test_explicit_session_flag_overrides_the_derivation(handoff, boot_file):
    out = _run("--reset", "scripts/reset_lane.sh", "--session", "irsyad-prog2",
               "--boot-file", boot_file, "--handoff", handoff, "--dry-run")
    assert out.returncode == 0, out.stderr
    assert "irsyad-prog2" in out.stdout + out.stderr


def test_unknown_session_is_refused_rather_than_guessed(handoff):
    """reset_lane.sh takes its target as an argument, so there is nothing to derive. Guessing
    a session name here would aim a /clear at whatever happened to match."""
    out = _run("--reset", "scripts/reset_lane.sh",
               "--handoff", handoff, "--dry-run")
    assert out.returncode != 0
    assert "session" in (out.stderr + out.stdout).lower()


def test_it_holds_the_fire_window_before_waiting_not_only_inside_the_reset():
    assert "fire_window" in _SRC, (
        "self_recycle must take the fire-window hold itself. reset_*.sh takes its hold AFTER the "
        "busy gate, which is too late: a wake that lands during the delay starts a turn and the "
        "gate then refuses — exactly what happened to cai."
    )
    hold_at = _SRC.index("fire_window")
    fire_at = _SRC.index("env -u TMUX_PANE bash")
    assert hold_at < fire_at, "the hold must be taken before the reset is invoked, not after"


def test_the_hold_is_kept_ACROSS_the_fire_not_released_just_before_it():
    """cc-storefront, 2026-08-16 14:08Z: self_recycle logged "storefront is idle after 0s —
    firing reset_lane", and reset_lane then refused with "'storefront' is BUSY — foreground turn
    in progress". Both were telling the truth about different instants.

    The wait loop released the fire-window hold and THEN invoked the reset. A wake landing in
    that gap starts a turn, and the reset's own busy gate — correctly — refuses to clear a
    working body. The hold exists precisely to stop a new turn starting while we act, so
    dropping it one line before the act is the one place it must not be dropped.

    Releasing was never necessary: reset_lane.sh TAKES the hold (fire_window_hold), it does not
    refuse on an existing one, so there is no self-deadlock to avoid. The EXIT trap still
    releases on every path.
    """
    fire_block = _SRC[_SRC.index("is idle after"):_SRC.index("env -u TMUX_PANE bash")]
    assert not re.search(r"^\s*free\s*$", fire_block, re.M), (
        "the fire-window hold must NOT be released before invoking the reset — that gap is "
        "exactly where a wake starts the turn that makes the reset's busy gate refuse"
    )
    assert "trap free EXIT" in _SRC, "the hold must still be released on every exit path"


def test_it_waits_for_idle_instead_of_firing_once_and_hoping():
    assert re.search(r"esc to interrupt", _SRC), (
        "the scheduled job must re-check the pane for the busy marker and wait, rather than "
        "firing blind at DELAY"
    )
    assert re.search(r"MAX_WAIT|max-wait", _SRC), (
        "the wait must be bounded — a genuinely stuck body should fail loudly, not hang"
    )
