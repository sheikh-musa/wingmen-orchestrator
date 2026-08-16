"""Step-2 (Nazim #23572 / CAI-978, cc-fleet-health): when lane_nudge REFUSES on
apparent real staged text, it must log the RAW `capture-pane -e` bytes it decided on,
ALONGSIDE the preserved text + the verdict fields, so each refusal SELF-DIAGNOSES
(dim/non-dim bytes, the status line the parser read, pane geometry) at the instant of
the verdict. The bytes logged MUST be the same bytes the verdict was made on — a matched
pair (#23648) — so there is no moment-axis gap between "what the parser saw" and "what we
logged". This is additive: the refuse decision + exit code are unchanged.

Fake tmux is PATH-shimmed (lane_nudge calls bare `tmux`); we assert on what lane_nudge
WROTE, never on the fake. fire_window is isolated to a temp dir (reads free), so the
refuse under test is the staged-text branch, not the fire-window guard.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "composer"
NUDGE = REPO / "scripts" / "lane_nudge.sh"


def run_lane_nudge(tmp_path: Path, capture_text: str, session: str = "testlane"):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    capfile = tmp_path / "served_capture.e.txt"
    capfile.write_text(capture_text)
    fake = bindir / "tmux"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'sub="$1"; shift\n'
        "case \"$sub\" in\n"
        "  has-session) exit 0 ;;\n"
        f'  capture-pane) cat "{capfile}" ;;\n'
        "  display-message) echo '120x40' ;;\n"
        "  send-keys) : ;;\n"
        "  *) : ;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    logdir = tmp_path / "logs"
    fwdir = tmp_path / "fw"
    fwdir.mkdir()
    env = dict(
        os.environ,
        PATH=f"{bindir}:{os.environ['PATH']}",
        LANE_NUDGE_LOG_DIR=str(logdir),
        FIRE_WINDOW_DIR=str(fwdir),
    )
    out = subprocess.run(
        ["bash", str(NUDGE), session, "a nudge message"],
        capture_output=True, text=True, env=env,
    )
    return out, logdir


def test_refuse_on_staged_text_logs_matched_raw_capture(tmp_path):
    served = (FIX / "real_dim_queued.e.txt").read_text()  # CC_N=1, dim -> refuse branch
    out, logdir = run_lane_nudge(tmp_path, served)

    # 1. refuse behaviour unchanged: exit 3
    assert out.returncode == 3, f"expected refuse exit 3, got {out.returncode} (stderr: {out.stderr})"

    # 2. one-line preserved log still written, with the staged text preserved verbatim
    preserved = logdir / "lane_nudge_preserved_input.log"
    assert preserved.exists(), "preserved_input.log not written"
    line = preserved.read_text()
    assert "poll the bus for hub's reply" in line, f"staged text not preserved: {line!r}"

    # 3. NEW: the verdict fields the parser used are on the log line (self-diagnosing)
    assert "CC_N=1" in line, f"verdict CC_N missing from log line: {line!r}"
    assert "real-text(dim)" in line, f"verdict basis missing from log line: {line!r}"

    # 4. NEW: the RAW capture is saved, and its bytes are the SAME bytes that were parsed
    #    (matched pair — no moment-axis gap). The log line must point at that file.
    capdir = logdir / "lane_nudge_captures"
    assert capdir.exists(), "lane_nudge_captures/ dir not created"
    caps = list(capdir.glob(f"{'testlane'}-*.e.txt"))
    assert len(caps) == 1, f"expected exactly one raw capture file, got {caps}"
    # command substitution strips TRAILING newlines from both the parsed text and the
    # logged text equally; interior bytes (SGR escapes, the dim markers) are preserved —
    # so the matched-pair guarantee holds modulo trailing-newline normalisation.
    assert caps[0].read_text().rstrip("\n") == served.rstrip("\n"), \
        "logged raw capture != the bytes the verdict was made on"
    assert caps[0].name in line, f"log line does not reference the raw capture file: {line!r}"


def test_capture_failure_is_logged_loud_not_silent(tmp_path):
    # If the raw capture cannot be saved, the miss must be VISIBLE in the log (fail-loud),
    # never a silent gap. We simulate by making the captures dir unwritable.
    served = (FIX / "real_dim_queued.e.txt").read_text()
    logdir = tmp_path / "logs"
    logdir.mkdir()
    capdir = logdir / "lane_nudge_captures"
    capdir.mkdir()
    capdir.chmod(0o500)  # read+exec only -> cannot create the capture file inside
    try:
        # reuse the runner but with the pre-made unwritable logdir
        bindir = tmp_path / "bin"; bindir.mkdir()
        capfile = tmp_path / "served.e.txt"; capfile.write_text(served)
        fake = bindir / "tmux"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            'sub="$1"; shift\n'
            "case \"$sub\" in\n"
            "  has-session) exit 0 ;;\n"
            f'  capture-pane) cat "{capfile}" ;;\n'
            "  display-message) echo '120x40' ;;\n"
            "  *) : ;;\n"
            "esac\n"
        )
        fake.chmod(0o755)
        fwdir = tmp_path / "fw"; fwdir.mkdir()
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                   LANE_NUDGE_LOG_DIR=str(logdir), FIRE_WINDOW_DIR=str(fwdir))
        out = subprocess.run(["bash", str(NUDGE), "testlane", "msg"],
                             capture_output=True, text=True, env=env)
        assert out.returncode == 3
        line = (logdir / "lane_nudge_preserved_input.log").read_text()
        assert "CAPTURE-FAILED" in line, f"capture failure not surfaced loud in log: {line!r}"
    finally:
        capdir.chmod(0o700)  # let tmp cleanup remove it
