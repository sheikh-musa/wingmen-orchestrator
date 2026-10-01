"""lane_nudge: resize-redraw before refusing a REVERT-FAIL (orch-console #49010).

Field case (2026-10-01 22:56-23:22Z, cc-fleet-health): a stale render line from the boot
banner was drawn over the composer's bottom separator. The real composer was EMPTY, but the
ghost-probe's byte-identical revert check failed on the residue, so every wake was REFUSED
and the body sat idle with 3 unread. `tmux resize-window -x W-2` then back to W forced the TUI
to redraw, the residue vanished, and the next nudge delivered on try 1.

Rule under test: on a STABLE-pane revert-fail, redraw ONCE (no keystrokes). Re-evaluate ONLY
if the redraw CHANGED the capture (residue is erased by a redraw; real staged text is not):
  - post-redraw composer EMPTY        -> deliver
  - post-redraw re-probe says ghost   -> deliver
  - otherwise / redraw changed nothing -> the existing refuse path, unchanged (exit 3)
The window's window-size option is restored (resize-window sets it to 'manual').

Fake tmux is a state machine like test_lane_nudge_probe_wire.py; `resize-window` back to the
original width flips it into a 'redrawn' state that serves the post-redraw pane.
"""
import os
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NUDGE = REPO / "scripts" / "lane_nudge.sh"
FIX = REPO / "tests" / "fixtures" / "composer"

BRIGHT_POS = (FIX / "real_bright.e.txt").read_text()     # CC_N>0 real-text(not-dim)
_BRIGHT_FLAT = "build the CAI-752 UI"
EMPTY = (FIX / "empty.e.txt").read_text()
# The field residue (#49010): a stale boot-banner fragment left on screen. The revert read carries
# it, so it differs from the clean post-redraw pane — exactly what a redraw erases.
RESIDUE_REVERT = EMPTY + "...oting without waiting for an external nudge\n"
REAL_AFTER = BRIGHT_POS.replace(_BRIGHT_FLAT, _BRIGHT_FLAT + "~")
GHOST_AFTER = BRIGHT_POS.replace(_BRIGHT_FLAT, "~")
WORKING = "some output\n  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents · ctrl+t\n"


def run_nudge(tmp_path, *, postredraw=None, probe2_after=None, width="120", had_window_size=""):
    """before=BRIGHT_POS, probe 1: sentinel appends, BSpace 'restores' to EMPTY+residue -> revert-fail
    on a stable idle non-dim pane (the P1 path today). `postredraw` = what the pane reads after
    the redraw (None = redraw changes nothing). `probe2_after` = after-sentinel read on a 2nd probe."""
    bindir = tmp_path / "bin"; bindir.mkdir()
    st = tmp_path / "state"; st.mkdir()
    for name, val in [("before", BRIGHT_POS), ("after", REAL_AFTER), ("revert", RESIDUE_REVERT),
                      ("working", WORKING)]:
        (st / name).write_text(val)
    if postredraw is not None:
        (st / "postredraw").write_text(postredraw)
    if probe2_after is not None:
        (st / "after2").write_text(probe2_after)
    (st / "ws").write_text(had_window_size)
    fake = bindir / "tmux"
    fake.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        S="{st}"
        echo "$*" >> "$S/calls"
        sub="$1"; shift
        case "$sub" in
          has-session) exit 0 ;;
          display-message)
            case "$*" in *window_width*) echo '{width}' ;; *) echo '80x40' ;; esac ;;
          show-window-options) cat "$S/ws"; exit 0 ;;
          set-window-option) case "$*" in *-u*window-size*) : > "$S/ws" ;; esac ;;
          resize-window)
            case "$*" in
              *"-x {width}"*) printf manual > "$S/ws"; : > "$S/redrawn"; rm -f "$S/sentinel" "$S/reverted" ;;
              *) printf manual > "$S/ws" ;;
            esac ;;
          capture-pane)
            if [ -f "$S/delivered" ]; then cat "$S/working"
            elif [ -f "$S/redrawn" ]; then
              if [ -f "$S/sentinel" ] && [ ! -f "$S/reverted" ] && [ -f "$S/after2" ]; then cat "$S/after2"
              elif [ -f "$S/postredraw" ]; then cat "$S/postredraw"
              elif [ -f "$S/reverted" ]; then cat "$S/revert"
              else cat "$S/before"; fi
            elif [ -f "$S/sentinel" ] && [ ! -f "$S/reverted" ]; then cat "$S/after"
            elif [ -f "$S/reverted" ]; then cat "$S/revert"
            else cat "$S/before"; fi ;;
          send-keys)
            for a in "$@"; do
              case "$a" in
                '~') : > "$S/sentinel" ;;
                BSpace) : > "$S/reverted" ;;
                Enter) [ -f "$S/typed_msg" ] && : > "$S/delivered" ;;
                'a nudge message') : > "$S/typed_msg" ;;
              esac
            done ;;
          *) : ;;
        esac
    """))
    fake.chmod(0o755)
    logdir = tmp_path / "logs"
    fwdir = tmp_path / "fw"; fwdir.mkdir()
    # DATABASE_URL="" is load-bearing: no test may reach the live bus (#23895).
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", LANE_NUDGE_LOG_DIR=str(logdir),
               FIRE_WINDOW_DIR=str(fwdir), BUSY_LIVENESS_S="0", DATABASE_URL="",
               PROBE_INSTABILITY_SAMPLE_S="0", LANE_NUDGE_REDRAW_SETTLE_S="0")
    out = subprocess.run(["/bin/bash", str(NUDGE), "testlane", "a nudge message"],
                         capture_output=True, text=True, env=env)
    calls = (st / "calls").read_text() if (st / "calls").exists() else ""
    return out, logdir, calls, (st / "ws").read_text()


def _log(logdir):
    p = logdir / "lane_nudge_preserved_input.log"
    return p.read_text() if p.exists() else ""


def test_residue_cleared_by_redraw_to_empty_delivers(tmp_path):
    out, logdir, calls, _ = run_nudge(tmp_path, postredraw=EMPTY)
    assert out.returncode == 0, out.stderr
    assert "resize-window -t testlane -x 118" in calls and "resize-window -t testlane -x 120" in calls
    assert "redraw" in _log(logdir).lower()
    assert "escalated P1" not in out.stderr


def test_redraw_then_reprobe_ghost_delivers(tmp_path):
    out, _, _, _ = run_nudge(tmp_path, postredraw=BRIGHT_POS.replace(_BRIGHT_FLAT, "stale residue"),
                             probe2_after=GHOST_AFTER)
    assert out.returncode == 0, out.stderr


def test_redraw_then_reprobe_real_still_refuses(tmp_path):
    other = BRIGHT_POS.replace(_BRIGHT_FLAT, "real text after redraw")
    out, logdir, _, _ = run_nudge(tmp_path, postredraw=other,
                                  probe2_after=other.replace("real text after redraw", "real text after redraw~"))
    assert out.returncode == 3, out.stderr


def test_redraw_that_changes_nothing_keeps_existing_p1_refusal(tmp_path):
    out, logdir, calls, _ = run_nudge(tmp_path, postredraw=None)
    assert out.returncode == 3
    assert "REVERT-FAIL" in out.stderr and "escalated P1" in out.stderr
    assert "resize-window" in calls      # the redraw WAS tried
    # and only once: one shrink + one restore
    assert calls.count("resize-window") == 2


def test_window_size_option_restored_when_previously_unset(tmp_path):
    _, _, calls, ws = run_nudge(tmp_path, postredraw=EMPTY, had_window_size="")
    assert ws == "", f"window-size left as {ws!r}; resize-window pins it to manual"
    assert "set-window-option -t testlane -u window-size" in calls


def test_window_size_option_left_alone_when_previously_set(tmp_path):
    _, _, calls, _ = run_nudge(tmp_path, postredraw=EMPTY, had_window_size="manual")
    assert "-u window-size" not in calls


def test_unreadable_width_skips_redraw_and_refuses(tmp_path):
    out, _, calls, _ = run_nudge(tmp_path, postredraw=EMPTY, width="")
    assert out.returncode == 3
    assert "resize-window" not in calls


def test_no_keystrokes_between_redraw_and_reparse(tmp_path):
    # The redraw itself must not type anything: no send-keys between the shrink and the restore.
    _, _, calls, _ = run_nudge(tmp_path, postredraw=EMPTY)
    lines = calls.splitlines()
    i = next(n for n, l in enumerate(lines) if "resize-window" in l and "-x 118" in l)
    j = next(n for n, l in enumerate(lines) if "resize-window" in l and "-x 120" in l)
    assert not any(l.startswith("send-keys") for l in lines[i:j + 1])


# cc-quality #49035: composer_parse reports CC_EMPTY=1 BOTH for a genuinely blank composer
# (CC_PARTIAL=ok) AND for a capture where no prompt/border was found at all (noprompt/noborder,
# e.g. a mid-repaint frame right after the resize-back). The delivery loop starts with C-u,
# so treating "could not read" as "confirmed empty" would erase a real unsent message.
MIDREPAINT = "some scrollback output\nstill repainting, no prompt glyph yet\n"   # no ❯, no border


def test_postredraw_unreadable_frame_does_not_deliver(tmp_path):
    out, logdir, calls, _ = run_nudge(tmp_path, postredraw=MIDREPAINT)
    assert out.returncode == 3, f"a noprompt post-redraw frame must REFUSE, got {out.returncode}: {out.stderr}"
    assert "a nudge message" not in calls, "must never type the nudge after an unreadable redraw"
    # and must NOT re-probe (type a sentinel) into a pane it cannot read
    after_redraw = calls.split("-x 120", 1)[1]
    assert "send-keys" not in after_redraw, f"no keystrokes after an unreadable redraw: {after_redraw!r}"
    assert "unreadable" in out.stderr


def test_genuinely_empty_composer_still_reports_ok_partial():
    # Guard the fix's premise: the empty fixture parses CC_EMPTY=1 AND CC_PARTIAL=ok.
    lib = REPO / "scripts" / "lib" / "composer_capture.sh"
    r = subprocess.run(["/bin/bash", "-c", 'source "$1"; composer_parse "$(cat "$2")"; echo "$CC_EMPTY $CC_PARTIAL"',
                        "_", str(lib), str(FIX / "empty.e.txt")], capture_output=True, text=True)
    assert r.stdout.split() == ["1", "ok"], r.stdout + r.stderr
