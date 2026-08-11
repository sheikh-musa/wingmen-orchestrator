"""Tests for scripts/lib/composer_capture.sh — the shared "what is staged in the
agent's composer?" extractor used by every reset/recycle path (reset_lane.sh,
reset_orch.sh, reset_cai.sh, lane_nudge.sh, lane_wedge_watchdog).

WHY THIS EXISTS (2026-08-07, cc-fleet-health FIX-1): the composer preservation
half of the fleet auto-recycle reported the composer EMPTY while real work was
staged, which on an autonomous recycle SILENTLY DROPS that work — the load-bearing
defect that held the autonomy unlock.

Root cause, measured on Claude Code v2.1.224 (the lib was validated against
v2.1.198): the composer now renders real QUEUED/unfocused staged text wrapped in
SGR 2 (dim) — byte-identical framing to the grey placeholder hint the lib used to
key on. So "dim ⟹ placeholder" is no longer true, and `_cc_dim_only` classified
real staged text as an empty placeholder.

The fixtures below are REAL `tmux capture-pane -p -e` bytes captured on v2.1.224
(the dim-queued one is an actual live lane; placeholder_dim reuses that measured
dim framing with hint content). The regression this file locks: a dim-wrapped
real entry must be preserved (CC_EMPTY=0), while a placeholder hint stays empty.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "lib" / "composer_capture.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "composer"


def parse_fixture(name: str) -> dict:
    """Source the bash lib, run composer_parse against a fixture, return the
    globals it sets. Exercises the real shell code, not a reimplementation."""
    fixture = FIXTURES / name
    assert fixture.exists(), f"missing fixture {fixture}"
    snippet = (
        f'. "{LIB}"\n'
        f'composer_parse "$(cat "{fixture}")"\n'
        r'printf "%s|%s|%s|%s|%s" "$CC_EMPTY" "$CC_N" "$CC_PARTIAL" "${CC_GHOST:-0}" "$CC_FLAT"'
    )
    out = subprocess.run(
        ["bash", "-c", snippet],
        capture_output=True, text=True, cwd=str(REPO), check=True,
    ).stdout
    empty, n, partial, ghost, flat = out.split("|", 4)
    return {"empty": empty, "n": n, "partial": partial, "ghost": ghost, "flat": flat}


def test_dim_queued_real_text_is_preserved_not_empty():
    """A real staged entry rendered DIM (queued/unfocused on v2.1.224) must be
    preserved, not reported empty. This is the FIX-1 defect: dim != placeholder.

    Fails before the fix because `_cc_dim_only` treats the dim SGR run as a
    placeholder and sets CC_EMPTY=1, dropping the staged 'poll the bus...' work.
    """
    r = parse_fixture("real_dim_queued.e.txt")
    assert r["empty"] == "0", f"dim real text falsely reported empty: {r}"
    assert r["flat"] == "poll the bus for hub's reply"
    # CC_GHOST (op#18467): this NOVEL dim line (no prior '❯' echo) must NOT be
    # flagged a history-ghost — else it would be wiped on recycle (FIX-1 regression).
    assert r["ghost"] == "0", f"novel dim staged text falsely flagged history-ghost: {r}"


def test_history_ghost_is_flagged_but_not_empty():
    """A dim composer whose EXACT text also appears as a prior submitted '❯' line
    (a history-autosuggestion recall) is flagged CC_GHOST=1 — so the wedge/nudge/
    reset paths can treat it as empty-underneath. CONDITION 1 (op#18467): it must
    keep CC_EMPTY=0 so the reset preserve-log (gated on CC_EMPTY!=1) STILL runs on a
    mis-classified re-type — the ghost signal never silently drops content."""
    r = parse_fixture("history_ghost.e.txt")
    assert r["ghost"] == "1", f"history-recall ghost not flagged: {r}"
    assert r["empty"] == "0", (
        f"CONDITION-1 VIOLATION: ghost collapsed into CC_EMPTY=1 -> reset preserve-log "
        f"would be skipped -> silent data-loss on a mis-classified re-type: {r}")
    assert r["flat"] == "poll the bus for hub's reply"


def test_bright_real_text_is_preserved():
    """A real staged entry rendered bright (freshly typed) stays preserved."""
    r = parse_fixture("real_bright.e.txt")
    assert r["empty"] == "0", f"bright real text falsely reported empty: {r}"
    assert r["flat"] == "build the CAI-752 UI"


def test_empty_composer_is_empty():
    """A genuinely empty composer stays empty (no false-staged fabrication)."""
    r = parse_fixture("empty.e.txt")
    assert r["empty"] == "1", f"empty composer not reported empty: {r}"


def test_dim_placeholder_hint_is_still_empty():
    """A grey placeholder hint (dim + known hint content) must still read empty,
    so the fix does not start fabricating hints as staged text."""
    r = parse_fixture("placeholder_dim.e.txt")
    assert r["empty"] == "1", f"placeholder hint falsely reported as staged: {r}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
