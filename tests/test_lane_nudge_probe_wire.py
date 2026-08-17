"""Step-4 (Nazim promotion, coupled behind the pane_busy collapse): lane_nudge's REFUSE-on-
staged-text branch now consults the mutating ghost-probe before refusing. A dim AUTOSUGGESTION
ghost parses as CC_N>0 real-text(dim) and would be refused forever (the #23536 self-poisoning
loop, #23873: a prior wake nudge re-renders dim and blocks the next wake). The probe types a
1-byte sentinel: if it REPLACES the composer (after==sentinel) the composer was empty -> ghost
-> PROCEED (clear+deliver); if it APPENDS (after==before+sentinel) there is real staged text
-> REFUSE/preserve; revert-fail -> LOUD + refuse, never proceed.

Fake tmux is a STATE MACHINE (not a fixed capture sequence): it answers capture-pane based on
what has been TYPED, so the test is robust to how many times the wrapper captures and has no
race — the field showed ghosts are transient (#23873.4), so a count-indexed fake would be
fragile by construction.
"""
import os
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NUDGE = REPO / "scripts" / "lane_nudge.sh"
FIX = REPO / "tests" / "fixtures" / "composer"

CC_N_POS = (FIX / "real_dim_queued.e.txt").read_text()          # parses CC_N=1 real-text(dim)
_FLAT = "poll the bus for hub's reply"


def _after(replacement: str) -> str:
    # faithful after-sentinel pane: real dim framing, composer content swapped (as test_probe_wrapper)
    return CC_N_POS.replace(_FLAT, replacement)


GHOST_AFTER = _after("~")            # sentinel replaced an empty composer -> ghost
REAL_AFTER = _after(_FLAT + "~")     # sentinel appended to real staged text -> real
WORKING = "some output\n  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ctrl+t\n"


def run_nudge(tmp_path: Path, after_pane: str, revert_pane: str = None):
    """Drive lane_nudge.sh with a state-machine fake tmux. `after_pane` is what the composer
    reads AFTER the sentinel is typed (GHOST_AFTER or REAL_AFTER); `revert_pane` is what it reads
    AFTER the BSpace (defaults to the original `before`, i.e. a clean byte-identical revert).
    Returns (completedprocess, logdir)."""
    bindir = tmp_path / "bin"; bindir.mkdir()
    st = tmp_path / "state"; st.mkdir()
    (st / "before").write_text(CC_N_POS)
    (st / "after").write_text(after_pane)
    (st / "revert").write_text(revert_pane if revert_pane is not None else CC_N_POS)
    (st / "working").write_text(WORKING)
    fake = bindir / "tmux"
    fake.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        S="{st}"
        sub="$1"; shift
        case "$sub" in
          has-session) exit 0 ;;
          display-message) echo '80x40' ;;
          capture-pane)
            if [ -f "$S/delivered" ]; then cat "$S/working"
            elif [ -f "$S/sentinel" ] && [ ! -f "$S/reverted" ]; then cat "$S/after"
            elif [ -f "$S/reverted" ]; then cat "$S/revert"
            else cat "$S/before"; fi ;;
          send-keys)
            # classify what was typed to advance the state machine
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
    # DATABASE_URL="" is load-bearing: it makes _probe_p1_escalate a no-op so a test can NEVER
    # write to the live bus. On 2026-08-16 this test fired 3 real P1 rows to orch-console because
    # the run had DATABASE_URL in its env (#23895). A test must not be able to reach production.
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               LANE_NUDGE_LOG_DIR=str(logdir), FIRE_WINDOW_DIR=str(fwdir),
               BUSY_LIVENESS_S="0", DATABASE_URL="")
    out = subprocess.run(["bash", str(NUDGE), "testlane", "a nudge message"],
                         capture_output=True, text=True, env=env)
    return out, logdir


def test_ghost_verdict_proceeds_and_delivers(tmp_path):
    # composer reads CC_N>0 (dim ghost). Probe types sentinel -> composer becomes just "~"
    # (ghost). lane_nudge must PROCEED past the refuse and deliver -> exit 0, not 3.
    out, logdir = run_nudge(tmp_path, GHOST_AFTER)
    assert out.returncode == 0, f"expected PROCEED (exit 0) on ghost, got {out.returncode} (stderr: {out.stderr})"
    # and it must record the proceed-on-ghost decision for the first-~10-firings validation
    log = (logdir / "lane_nudge_preserved_input.log")
    assert log.exists() and "PROCEEDED" in log.read_text() and "ghost" in log.read_text().lower(), \
        f"proceed-on-ghost not logged: {log.read_text() if log.exists() else '<no log>'}"


def test_real_verdict_refuses_and_preserves(tmp_path):
    # sentinel APPENDS to real staged text -> real -> lane_nudge must REFUSE (exit 3), preserve.
    out, logdir = run_nudge(tmp_path, REAL_AFTER)
    assert out.returncode == 3, f"expected REFUSE (exit 3) on real, got {out.returncode} (stderr: {out.stderr})"
    log = (logdir / "lane_nudge_preserved_input.log").read_text()
    assert "REFUSED" in log and _FLAT in log, f"real refuse not preserved: {log!r}"


def test_revert_fail_refuses_loudly_and_never_proceeds(tmp_path):
    # Sentinel APPENDS (real verdict), but the BSpace does NOT restore the composer byte-identical
    # (revert_pane parses to a different composer). cond#2: this must REFUSE (exit 3, never 0) and
    # fail LOUD — a real staged step may be corrupted, so proceeding would compound it.
    empty = (FIX / "empty.e.txt").read_text()   # after_revert parses to "" != before
    out, logdir = run_nudge(tmp_path, REAL_AFTER, revert_pane=empty)
    assert out.returncode == 3, f"revert-fail must REFUSE (exit 3), got {out.returncode} (stderr: {out.stderr})"
    assert "REVERT-FAIL" in out.stderr, f"revert-fail must fail LOUD on stderr: {out.stderr!r}"
    log = (logdir / "lane_nudge_preserved_input.log").read_text()
    assert "revert-fail" in log.lower(), f"revert-fail not recorded: {log!r}"
    # the log must carry the BEFORE content (the text at risk), not the post-revert empty capture
    # (#23895: the P1 reported CC_FLAT='' because CC_FLAT had been overwritten by the revert read).
    assert _FLAT in log, f"revert-fail log must record the before-content at risk, got: {log!r}"


def test_revert_fail_on_busy_pane_is_low_not_p1(tmp_path):
    # Nazim #25506/#25619/#25635: a revert-fail on a MID-TURN pane (busy footer 'esc to interrupt')
    # is the EXPECTED repaint race, not corruption — the probe kept firing false P1s on HEALTHY busy
    # panes. It must STILL refuse (exit 3, never clobber) but DOWN-RANK: LOW log + stderr, no P1.
    # WORKING is a busy footer; as the post-BSpace capture it makes both the revert read (!= before,
    # so the verdict is revert-fail) and the busy re-check see a mid-turn pane.
    out, logdir = run_nudge(tmp_path, REAL_AFTER, revert_pane=WORKING)
    assert out.returncode == 3, f"a busy-pane revert-fail must STILL refuse (exit 3), got {out.returncode} (stderr: {out.stderr})"
    log = (logdir / "lane_nudge_preserved_input.log").read_text()
    assert "revert-fail" in log.lower(), f"revert-fail not recorded: {log!r}"
    assert ("mid-turn" in log.lower()) or ("repaint race" in log.lower()) or ("no p1" in log.lower()), \
        f"a busy-pane revert-fail must be logged LOW/repaint-race, got: {log!r}"
    assert ("MID-TURN" in out.stderr) or ("LOW, no P1" in out.stderr), \
        f"stderr must say the page was down-ranked, got: {out.stderr!r}"
    # and it must NOT claim it escalated a P1 (the whole point — no operator page on a healthy pane)
    assert "escalated P1" not in out.stderr, f"busy-pane revert-fail must NOT escalate P1: {out.stderr!r}"


def test_revert_fail_on_idle_pane_still_escalates_p1(tmp_path):
    # The reserved case: a revert-fail on an IDLE pane (no busy footer) keeps the P1 path — a genuine
    # byte anomaly still surfaces to the operator. empty.e.txt is an idle/empty composer, not busy.
    empty = (FIX / "empty.e.txt").read_text()
    out, logdir = run_nudge(tmp_path, REAL_AFTER, revert_pane=empty)
    assert out.returncode == 3
    assert "REVERT-FAIL" in out.stderr and "escalated P1" in out.stderr, \
        f"an idle-pane revert-fail must keep the P1 path, got: {out.stderr!r}"


def test_ghost_log_verdict_fields_are_the_matched_pair_not_postrevert(tmp_path):
    # DEFECT-1 (matched-pair, broader than "basis only"): _probe_composer re-parses the pane
    # 3x (before/after-sentinel/after-revert) and composer_parse UNCONDITIONALLY overwrites
    # CC_N/CC_EMPTY/CC_PARTIAL/CC_GHOST/CC_PH_BASIS each call. So _log_probe_capture reads the
    # AFTER-REVERT values, not the bytes the refuse-branch decision was made on. It only LOOKS
    # right in production because a dim autosuggestion re-renders on the empty buffer. But a
    # ghost is transient (#23873.4): after the BSpace it may NOT have re-rendered, so the
    # after-revert capture reads EMPTY (CC_N=0 / no-content) — and the logged verdict fields
    # then CONTRADICT the raw .e.txt file saved alongside them (which is CC_RAWCAP = the ghost).
    # The fields must describe CC_RAWCAP (the saved file, the decision), a true matched pair.
    empty = (FIX / "empty.e.txt").read_text()   # after-revert reads empty (ghost not re-rendered yet)
    out, logdir = run_nudge(tmp_path, GHOST_AFTER, revert_pane=empty)
    assert out.returncode == 0, f"ghost still PROCEEDS regardless of the transient revert read, got {out.returncode} (stderr: {out.stderr})"
    line = (logdir / "lane_nudge_preserved_input.log").read_text()
    # the fields must match the SAVED raw capture (real_dim_queued: CC_N=1, real-text(dim)),
    # never the transient post-revert empty capture (CC_N=0 / no-content)
    assert "CC_N=1" in line, f"CC_N must match the before/raw capture, got: {line!r}"
    assert "CC_EMPTY=0" in line, f"CC_EMPTY must match the before/raw capture, got: {line!r}"
    assert "basis=real-text(dim)" in line, f"basis must match the before/raw capture, got: {line!r}"
    assert "no-content" not in line, f"transient post-revert 'no-content' leaked into the matched-pair log: {line!r}"


def test_catchall_refuse_label_carries_probe_class(tmp_path):
    # #23970.2/.3 observability: the `*)` catch-all logged ONE fixed label "REFUSED, preserved
    # staged" for real|unsure|busy|locked|'' alike, so a probe SKIP (busy/locked) or ERROR ('')
    # read in the label EXACTLY like a genuine real-text preserve — a human scanning labels could
    # not tell "the probe found real text" from "the probe never ran". The label must carry the
    # CC_PROBE class. This drives the reachable `real` case (a real staged step present); busy /
    # '' / locked take the IDENTICAL printf template ("REFUSED [probe=<class>: ...]") so they are
    # distinguished by construction once this proves the label is CC_PROBE-derived, not fixed.
    # (NOTE: a HELD fire window is caught by the EARLY guard at lane_nudge.sh:45 -> exit 4, before
    # the probe, so CC_PROBE=locked at this catch-all is only reachable via a narrow race; it is
    # not worth a moment-aware harness to force here.)
    out, logdir = run_nudge(tmp_path, REAL_AFTER)
    assert out.returncode == 3
    line = (logdir / "lane_nudge_preserved_input.log").read_text()
    label = line.split("| verdict:")[0]
    assert "probe=real" in label, f"catch-all LABEL must carry the CC_PROBE class, got label: {label!r}"
