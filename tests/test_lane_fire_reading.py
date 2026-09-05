"""lane_fire_reading — the GAUGE-FIRST reading that drives the self-recycle FIRE decision.

WHY a second resolver (bus 37752, op#19141). resolve() is PANE-FIRST — right for the console
DISPLAY, but the wrong precedence for deciding whether to auto-recycle a lane. Today exams sat
at 941k while the pane-based detector logged "FIRING NOTHING": the pane's blind band
(context_truth.py:18-30) reads a 94% body as clean, and the pane hint UNDERSTATES total fill.
For the FIRE decision the operator's rule (Nazim 37752) is the inverse of resolve():

  * the FRESH gauge (cc_session_costs.latest_context_tokens) is the single source that DECIDES;
  * the pane is a cross-check that only LOGS disagreement — it never decides;
  * a gauge that is STALE or unreadable is UNKNOWN => the caller PAGES, never "assumes quiet"
    and never fires blind on the pane.

These tests lock that contract. lane_fire_reading returns the same ContextTruth shape as
resolve() so callers share one vocabulary. Pure/side-effect-free: callers supply the readings.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import context_truth as ct  # noqa: E402


# ── the fresh gauge is the decider ────────────────────────────────────────────
def test_fresh_gauge_decides():
    # exams' 941k, the case that started this. Fresh gauge => it decides, source=gauge.
    t = ct.lane_fire_reading(gauge_tokens=941_000, gauge_age_s=120)
    assert t.known and t.pct == 94 and t.level == "red" and t.source == "gauge"


def test_fresh_gauge_below_soft_is_green():
    t = ct.lane_fire_reading(gauge_tokens=420_000, gauge_age_s=60)
    assert t.known and t.pct == 42 and t.level == "green" and t.source == "gauge"


# ── a stale gauge is UNKNOWN, and the pane does NOT rescue it ──────────────────
def test_stale_gauge_is_unknown_not_quiet():
    # Idle-frozen gauge past the freshness cutoff. Must be UNKNOWN, never a low/green reading
    # that the caller treats as quiet.
    t = ct.lane_fire_reading(gauge_tokens=940_000, gauge_age_s=7200)
    assert not t.known and t.pct is None and t.level is None
    assert "UNKNOWN" in t.reason and "stale" in t.reason.lower()


def test_stale_gauge_with_a_live_pane_is_still_unknown_but_pane_informs_the_page():
    # The pane is NOT the decider (Nazim 37752): even a high pane_pct cannot turn a stale gauge
    # into a fire verdict. It IS surfaced in the reason so the page is actionable.
    t = ct.lane_fire_reading(gauge_tokens=200_000, gauge_age_s=9000, pane_pct=96)
    assert not t.known and t.pct is None
    assert "UNKNOWN" in t.reason
    assert "96" in t.reason  # pane reading carried into the page text


def test_no_gauge_at_all_is_unknown():
    t = ct.lane_fire_reading(gauge_tokens=None, gauge_age_s=None)
    assert not t.known and t.pct is None
    assert "UNKNOWN" in t.reason


def test_over_window_gauge_is_unknown_bad_data():
    # A single turn cannot exceed the window — treat as unmeasurable, never as 100%+.
    t = ct.lane_fire_reading(gauge_tokens=1_500_000, gauge_age_s=60)
    assert not t.known and t.pct is None
    assert "UNKNOWN" in t.reason


# ── the pane is a LOGGED cross-check, never the decider ───────────────────────
def test_pane_disagreement_is_logged_but_gauge_still_decides():
    # Fresh gauge=88% decides; pane hint reads far lower (understates). Verdict stays the
    # gauge's; the disagreement is reported (mis-map / just-recycled dead-session guard).
    t = ct.lane_fire_reading(gauge_tokens=880_000, gauge_age_s=120, pane_pct=40)
    assert t.known and t.pct == 88 and t.source == "gauge"
    assert t.disagreement == 48
    assert "disagree" in t.reason.lower() and "40" in t.reason


def test_pane_agreeing_does_not_change_the_gauge_verdict():
    t = ct.lane_fire_reading(gauge_tokens=820_000, gauge_age_s=90, pane_pct=80)
    assert t.known and t.pct == 82 and t.source == "gauge"
    # small gap, under the disagreement threshold — no noise in the reason
    assert "disagree" not in t.reason.lower()
