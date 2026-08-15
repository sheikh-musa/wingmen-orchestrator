"""Tests for the ghost-vs-real PROBE decision core in scripts/lib/composer_capture.sh.

WHY (2026-08-15, op#13355 / bus 22395, cc-fleet-health): the non-mutating history-match
ghost test is PARTIAL BY DESIGN — a ghost whose submitted echo scrolled off the visible
transcript slips through (caai, proven at source) and gets classified real-text(dim), so
lane_nudge REFUSES and auto-recovery goes inert. The definitive separator is a self-
reversing PROBE: type a sentinel; a GHOST (autosuggestion in an empty composer) is
REPLACED by the sentinel; REAL staged text has the sentinel APPENDED. This file locks the
PURE decision core (`_probe_verdict`) and revert-verification (`_probe_revert_ok`) — the
mutating tmux orchestration is a thin wrapper over these, and the passive composer_parse
is never touched (it must never wipe real dim staged text — see test_composer_capture.py).
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "lib" / "composer_capture.sh"


def _run(snippet: str) -> str:
    out = subprocess.run(
        ["bash", "-c", f'. "{LIB}"\n{snippet}'],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"snippet errored: {out.stderr}"
    return out.stdout


def verdict(before: str, after: str, sentinel: str = "~") -> str:
    # Pass args positionally so text with spaces/quotes reaches the fn verbatim.
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"\n_probe_verdict "$1" "$2" "$3"', "_",
         before, after, sentinel],
        capture_output=True, text=True,
    ).stdout.strip()


def revert_ok(before: str, after_revert: str) -> int:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"\n_probe_revert_ok "$1" "$2"', "_", before, after_revert],
    ).returncode


def test_ghost_is_replaced_by_sentinel():
    # A ghost occupies an EMPTY composer; typing the sentinel REPLACES it, so the
    # composer content afterward is the sentinel ALONE -> ghost.
    assert verdict(before="[wake] new inbox item — read your bus", after="~", sentinel="~") == "ghost"


def test_real_text_gets_sentinel_appended():
    # Real staged text stays; the sentinel is APPENDED -> real (must be preserved).
    assert verdict(before="poll the bus for hub's reply",
                   after="poll the bus for hub's reply~", sentinel="~") == "real"


def test_ambiguous_after_is_unsure():
    # Neither replaced-alone nor exact-append -> unsure -> caller fails toward preserve.
    assert verdict(before="foo", after="totally different", sentinel="~") == "unsure"


def test_revert_ok_true_when_composer_restored():
    # After the BSpace, the composer must be byte-identical to before -> exit 0 (ok).
    assert revert_ok(before="poll the bus for hub's reply",
                     after_revert="poll the bus for hub's reply") == 0


def test_revert_ok_false_when_not_restored():
    # A failed/partial revert (residue left) must be detected -> nonzero (NOT ok);
    # the caller must then fail LOUD and never submit (condition #2, silent-corruption guard).
    assert revert_ok(before="poll the bus for hub's reply",
                     after_revert="poll the bus for hub's reply~") != 0
