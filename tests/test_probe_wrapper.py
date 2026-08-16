"""Tests for the MUTATING ghost-probe wrapper `_probe_composer` in composer_capture.sh.

WHY (2026-08-16, bus 22395 + field cases 22866/22907, cc-fleet-health): the pure
decision core (_probe_verdict / _probe_revert_ok, see test_ghost_probe.py) is locked and
field-confirmed on two wild ghosts. This file locks the ORCHESTRATION around it — the thin
mutating wrapper that types a sentinel into a LIVE pane and must obey Nazim's four Stage-2
conditions before promotion:
  #1 use the SHARED pane_is_busy (never a narrow local check)
  #2 verify the revert; on mismatch fail LOUD and NEVER submit
  #3 single-byte sentinel
  #4 re-check busy IMMEDIATELY before the mutating step (fresh, not a stale read)

These are control-flow assertions against a scripted FAKE tmux (dependency injection): the
fake serves a SEQUENCE of capture-pane responses (the pane states) and records every
send-keys invocation. We assert on what the wrapper DID (which keys it sent, its verdict) —
never on the fake. The ghost-vs-real DECISION itself is _probe_verdict, already unit-tested
and field-proven; here we prove the wrapper routes/guards it correctly. The wrapper is INERT
(nothing calls it from lane_nudge.sh yet) until Nazim signs the promotion.
"""
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "lib" / "composer_capture.sh"
FIX = Path(__file__).resolve().parent / "fixtures" / "composer"

# A tmux footer line that pane_is_busy keys on (foreground turn in progress).
BUSY_FOOTER = "  esc to interrupt"


def run_probe(tmpdir: Path, captures: list[str], pane: str = "testpane"):
    """Run `_probe_composer FAKE_TMUX <pane>` with a scripted fake tmux.

    `captures` is the ordered list of texts the fake returns for successive
    `capture-pane` calls (the last one repeats if the wrapper captures more times).
    Returns (cc_probe, keylog_lines): the wrapper's CC_PROBE verdict and every
    send-keys invocation the wrapper issued, in order.
    """
    cap_dir = tmpdir / "caps"
    cap_dir.mkdir(exist_ok=True)
    for i, txt in enumerate(captures):
        (cap_dir / f"{i}").write_text(txt)
    keylog = tmpdir / "keylog"
    keylog.write_text("")
    counter = tmpdir / "counter"
    counter.write_text("0")

    fake = tmpdir / "faketmux"
    fake.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Scripted fake tmux for probe-wrapper tests.
        sub="$1"; shift
        case "$sub" in
          capture-pane)
            i=$(cat "{counter}")
            f="{cap_dir}/$i"
            # last capture repeats once the sequence is exhausted
            if [ ! -f "$f" ]; then last=$(( $(ls "{cap_dir}" | wc -l) - 1 )); f="{cap_dir}/$last"; fi
            cat "$f"
            echo $(( i + 1 )) > "{counter}"
            ;;
          send-keys)
            printf '%s\\n' "$*" >> "{keylog}"
            ;;
          *) : ;;
        esac
    """))
    fake.chmod(0o755)

    script = f'. "{LIB}"\n_probe_composer "{fake}" "{pane}"\necho "CC_PROBE=${{CC_PROBE:-UNSET}}"'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    cc_probe = None
    for line in out.stdout.splitlines():
        if line.startswith("CC_PROBE="):
            cc_probe = line.split("=", 1)[1]
    keylog_lines = [l for l in keylog.read_text().splitlines() if l.strip()]
    return cc_probe, keylog_lines, out


def test_busy_pane_refuses_without_typing(tmp_path):
    # Cond #1 (Nazim's field near-miss #22907): the pane is BUSY. The wrapper must
    # detect it via the SHARED pane_is_busy and refuse — verdict 'busy', and it must
    # NEVER type the sentinel into an active turn.
    busy_pane = "some transcript line\n❯ \n" + BUSY_FOOTER
    cc_probe, keylog, out = run_probe(tmp_path, captures=[busy_pane])
    assert cc_probe == "busy", f"expected verdict 'busy', got {cc_probe!r} (stderr: {out.stderr})"
    assert keylog == [], f"wrapper typed into a BUSY pane — keys sent: {keylog}"


def test_pane_goes_busy_between_read_and_type_refuses(tmp_path):
    # Cond #4 (Nazim's live near-miss #22907): the pane read IDLE, then went BUSY
    # before the mutating step. The wrapper must re-check busy on a FRESH capture
    # IMMEDIATELY before typing and refuse — never type the sentinel into the now-
    # active turn. A wrapper that reads busy ONCE at the top and then acts on that
    # stale read would type here; this test fails that design.
    ghost = (FIX / "history_ghost.e.txt").read_text()   # idle pane, apparent staged text
    busy = "transcript line\n❯ \n" + BUSY_FOOTER         # state flipped to busy
    cc_probe, keylog, out = run_probe(tmp_path, captures=[ghost, ghost, busy])
    assert cc_probe == "busy", f"expected 'busy' (late transition caught), got {cc_probe!r} (stderr: {out.stderr})"
    assert keylog == [], f"wrapper typed after the pane went busy — keys sent: {keylog}"
