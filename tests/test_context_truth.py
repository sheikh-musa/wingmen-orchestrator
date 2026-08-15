"""context_truth: neither oracle alone is sufficient, and neither may report "fine" blind.

The two live failures this locks, both real and both from 2026-08-15:

  BLIND BAND (operator-caught). cc-fleet-health at 939.9k/1M showed NO reclaim hint and no
  pct line — the hint disappears once a session is full enough. pane_bloat_signal treats an
  absent hint as not-bloated, so a 94% body read as clean and the operator found it by
  typing /context himself. The gauge said 939,903 — identical to /context.

  STALE GAUGE (op#13050). The gauge only advances on a turn, so an idle lane freezes behind
  it: prog1 at 100% with an 84h-stale reading, cc-irsyad reading 8% against a pane showing
  795.9k. That is why the pane was adopted as truth in the first place.

Each signal is blind exactly where the other is sharp, so the tests below are mostly about
which one wins when, and about the case both callers got wrong in opposite directions:
unreadable must be UNKNOWN, never green.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import context_truth as ct  # noqa: E402


# ── the blind band: the gauge is the only signal that can see here ────────────
def test_blind_band_is_caught_by_the_gauge():
    # THE OPERATOR-CAUGHT CASE, exactly. Pane shows nothing at all; gauge says 939,903.
    t = ct.resolve(pane_pct=None, pane_hint_k=None, gauge_tokens=939_903, gauge_age_s=300)
    assert t.known and t.pct == 94 and t.level == "red" and t.source == "gauge"


def test_blind_band_would_have_read_clean_on_the_pane_alone():
    # Same body, pane-only: no signal at all. Must be UNKNOWN — never a green verdict.
    t = ct.resolve(pane_pct=None, pane_hint_k=None)
    assert not t.known and t.pct is None and t.level is None
    assert "UNKNOWN" in t.reason


# ── the stale gauge: the pane is the only signal that can see here ────────────
def test_stale_gauge_loses_to_a_live_pane_hint():
    # cc-irsyad measured live: gauge 8%, pane 795.9k. The pane was right.
    t = ct.resolve(pane_hint_k=795.9, gauge_tokens=80_000, gauge_age_s=41 * 3600)
    assert t.pct == 80 and t.source == "pane-hint"


def test_stale_gauge_alone_is_unknown_not_a_reading():
    # prog1's 84h-stale gauge must not be presented as a current measurement.
    t = ct.resolve(gauge_tokens=500_000, gauge_age_s=84 * 3600)
    assert not t.known and t.pct is None
    assert "stale" in t.reason


# ── precedence ───────────────────────────────────────────────────────────────
def test_pct_line_beats_everything():
    t = ct.resolve(pane_pct=97, pane_hint_k=200.0, gauge_tokens=500_000, gauge_age_s=10)
    assert t.pct == 97 and t.source == "pane-pct" and t.level == "red"


def test_fresh_pane_hint_beats_a_fresh_gauge():
    # Both live: prefer the pane. It is per-session, so it cannot be mapped to a sibling
    # instance, and it understates rather than overstates — early beats late.
    t = ct.resolve(pane_hint_k=650.0, gauge_tokens=600_000, gauge_age_s=60)
    assert t.pct == 65 and t.source == "pane-hint" and t.level == "amber"


# ── disagreement is reported, not silently resolved ──────────────────────────
def test_large_disagreement_is_surfaced():
    # The just-recycled case cc-fleet-health warned about (#22617): the gauge still carries
    # the dead session's final reading, so a fresh body reads red on the gauge alone.
    t = ct.resolve(pane_hint_k=60.0, gauge_tokens=960_000, gauge_age_s=120)
    assert t.pct == 6 and t.source == "pane-hint"
    assert t.disagreement == 90 and "disagrees" in t.reason


def test_small_disagreement_is_not_noise():
    # Hint is RECLAIMABLE, gauge is TOTAL — they are different quantities, so a modest gap
    # is expected and must not cry wolf.
    t = ct.resolve(pane_hint_k=640.0, gauge_tokens=660_000, gauge_age_s=60)
    assert t.disagreement == 2 and "disagrees" not in t.reason


# ── unreadable is UNKNOWN, never green — the rule both callers broke ──────────
def test_nothing_readable_is_unknown():
    t = ct.resolve()
    assert not t.known and t.level is None and "do NOT treat as clear" in t.reason


def test_impossible_gauge_value_is_not_a_reading():
    # A single turn cannot exceed the window. Bad data must not become a 100% red page.
    t = ct.resolve(gauge_tokens=5_000_000, gauge_age_s=10)
    assert not t.known and t.pct is None


def test_zero_and_negative_are_rejected_not_read_as_empty():
    for bad in (0, -1):
        assert not ct.resolve(gauge_tokens=bad, gauge_age_s=10).known


def test_garbage_types_do_not_crash_or_pass_as_a_reading():
    t = ct.resolve(pane_pct="n/a", pane_hint_k="???", gauge_tokens="oops", gauge_age_s=10)
    assert not t.known


# ── thresholds are the one fleet vocabulary ──────────────────────────────────
def test_levels_match_the_fleet_vocabulary():
    assert ct.resolve(gauge_tokens=590_000, gauge_age_s=1).level == "green"
    assert ct.resolve(gauge_tokens=600_000, gauge_age_s=1).level == "amber"
    assert ct.resolve(gauge_tokens=850_000, gauge_age_s=1).level == "red"
