"""lane_nudge: resize-redraw before refusing an UNSURE probe on a noborder capture (orch-console #79940).

Field case (2026-10-28 11:22-11:54Z, cc-fleet-health): the stale fragment "...oting without waiting
for an external nudge" (the same residue as #49010) was drawn ON the composer's bottom border of an
80x24 window. The composer itself was EMPTY, but the border no longer parsed (CC_PARTIAL=noborder),
the fragment read as staged text, and the probe could only say `unsure` -> every nudge for ~30 min
was REFUSED. The fixture is that preserved capture, verbatim.

Rule under test: on probe=unsure with a raw CC_PARTIAL=noborder on a STABLE pane, redraw ONCE and
re-evaluate; deliver only if the composer is then confirmed empty (or a re-probe says ghost). Text
that survives the redraw, an unstable pane, or an unsure on a clean border refuses exactly as before.
"""
import os
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NUDGE = REPO / "scripts" / "lane_nudge.sh"
FIX = REPO / "tests" / "fixtures" / "composer"

RESIDUE_TEXT = "oting without waiting for an external nudge"
BEFORE = (FIX / "residue_on_border_79940.e.txt").read_text()
assert RESIDUE_TEXT in BEFORE
# The probe's 1-byte sentinel lands in the (empty) composer; the residue still sits on the border, so
# the after-read is neither "replaced" nor "appended" -> unsure.
PROMPT = "❯\u00a0\n"                                    # the TUI pads the prompt with a NBSP
assert PROMPT in BEFORE
AFTER = BEFORE.replace(PROMPT, "❯\u00a0~\n", 1)
assert AFTER != BEFORE
# What a redraw gives back: the border repainted clean, composer empty (what console saw at 11:55Z).
CLEAN = BEFORE.replace(RESIDUE_TEXT, "─" * len(RESIDUE_TEXT))
WORKING = "some output\n  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents · ctrl+t\n"


def run_nudge(tmp_path, *, before=BEFORE, after=AFTER, postredraw=None, unstable=False, width="80"):
    """`postredraw` = what the pane reads after the resize-back (None = the redraw changes nothing).
    `unstable` = the pane repaints between two samples on its own (a repaint race)."""
    bindir = tmp_path / "bin"; bindir.mkdir()
    st = tmp_path / "state"; st.mkdir()
    for name, val in [("before", before), ("after", after), ("working", WORKING)]:
        (st / name).write_text(val)
    if postredraw is not None:
        (st / "postredraw").write_text(postredraw)
    (st / "ws").write_text("")
    fake = bindir / "tmux"
    fake.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        S="{st}"
        echo "$*" >> "$S/calls"
        sub="$1"; shift
        case "$sub" in
          has-session) exit 0 ;;
          display-message)
            case "$*" in *window_width*) echo '{width}' ;; *) echo '80x24' ;; esac ;;
          show-window-options) cat "$S/ws"; exit 0 ;;
          set-window-option) case "$*" in *-u*window-size*) : > "$S/ws" ;; esac ;;
          resize-window)
            case "$*" in
              *"-x {width}"*) printf manual > "$S/ws"; : > "$S/redrawn"; rm -f "$S/sentinel" "$S/reverted" ;;
              *) printf manual > "$S/ws" ;;
            esac ;;
          capture-pane)
            n=$(cat "$S/ncap" 2>/dev/null || echo 0); echo $((n + 1)) > "$S/ncap"
            if [ -f "$S/delivered" ]; then cat "$S/working"
            elif [ -f "$S/redrawn" ] && [ -f "$S/postredraw" ]; then cat "$S/postredraw"
            elif [ -f "$S/sentinel" ] && [ ! -f "$S/reverted" ]; then cat "$S/after"
            else cat "$S/before"; {'echo "  (repaint $n)"' if unstable else ':'}; fi ;;
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
                         capture_output=True, text=True, env=env, timeout=60)
    calls = (st / "calls").read_text() if (st / "calls").exists() else ""
    return out, logdir, calls


def _log(logdir):
    p = logdir / "lane_nudge_preserved_input.log"
    return p.read_text() if p.exists() else ""


def test_field_capture_without_redraw_fix_is_unsure_noborder(tmp_path):
    # Pin the field shape: with a redraw that changes nothing, this is the 11:5xZ refusal.
    out, logdir, calls = run_nudge(tmp_path, postredraw=None)
    assert out.returncode == 3, out.stderr
    assert "probe=unsure" in out.stderr
    assert "resize-redraw tried first: no-change" in out.stderr
    assert calls.count("resize-window") == 2          # tried exactly once: shrink + restore


def test_residue_on_border_cleared_by_redraw_delivers(tmp_path):
    out, logdir, calls = run_nudge(tmp_path, postredraw=CLEAN)
    assert out.returncode == 0, out.stderr
    assert "resize-window -t testlane -x 78" in calls and "resize-window -t testlane -x 80" in calls
    assert "PROCEEDED after UNSURE+noborder" in _log(logdir)
    assert "a nudge message" in calls


def test_text_that_survives_redraw_still_refuses(tmp_path):
    # Redraw changed the pane but the border still carries non-border text -> still noborder -> refuse.
    still = BEFORE.replace(RESIDUE_TEXT, "real words drawn on the border")
    out, logdir, calls = run_nudge(tmp_path, postredraw=still)
    assert out.returncode == 3, out.stderr
    assert "a nudge message" not in calls
    assert "unreadable(noborder)" in out.stderr


def test_unstable_pane_never_redraws(tmp_path):
    out, logdir, calls = run_nudge(tmp_path, postredraw=CLEAN, unstable=True)
    assert out.returncode == 3, out.stderr
    assert "resize-window" not in calls


def test_unsure_on_clean_border_is_out_of_scope_and_refuses_unchanged(tmp_path):
    # A real staged line in an intact composer whose probe is ambiguous: not a render-residue shape.
    staged = CLEAN.replace(PROMPT, "❯\u00a0half-typed step\n", 1)
    weird_after = staged.replace("half-typed step", "half-typed ~step")
    out, logdir, calls = run_nudge(tmp_path, before=staged, after=weird_after, postredraw=CLEAN)
    assert out.returncode == 3, out.stderr
    assert "resize-window" not in calls
    assert "a nudge message" not in calls
