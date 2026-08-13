"""Deterministic unit tests for the pure PACE math (op#12617).

All inputs are fixed (a pinned `now`, reset time, used%, prior reading) so every
assertion is exact — no clock, no network, no DB.
"""
import math
from datetime import datetime, timedelta, timezone

import pytest

from nervous_system.pool_pace import (
    elapsed_fraction, pace_ratio, projected_pct, burn_per_day, runway_days,
    days_to_reset, evaluate_page, compute_pool_pace, MIN_ELAPSED_FRAC,
)

UTC = timezone.utc
# A weekly window that resets at a round time; window_start = reset - 7d.
RESET = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)          # window resets here
START = RESET - timedelta(days=7)                         # 2026-08-12 08:00


# --------------------------------------------------------------------------- #
# elapsed_fraction
# --------------------------------------------------------------------------- #
class TestElapsedFraction:
    def test_exact_half(self):
        now = START + timedelta(days=3.5)
        assert elapsed_fraction(now, RESET) == pytest.approx(0.5)

    def test_quarter(self):
        now = START + timedelta(days=1.75)
        assert elapsed_fraction(now, RESET) == pytest.approx(0.25)

    def test_clamps_before_window(self):
        assert elapsed_fraction(START - timedelta(days=1), RESET) == 0.0

    def test_clamps_after_reset(self):
        assert elapsed_fraction(RESET + timedelta(days=2), RESET) == 1.0

    def test_at_start_is_zero(self):
        assert elapsed_fraction(START, RESET) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# pace_ratio  (used% / elapsed%)
# --------------------------------------------------------------------------- #
class TestPaceRatio:
    def test_on_pace(self):
        # 50% used at 50% elapsed == exactly on pace (1.0)
        assert pace_ratio(50.0, 0.5) == pytest.approx(1.0)

    def test_ahead_of_pace(self):
        # 44% used at 22.4% elapsed -> ~1.96 (ahead of pace)
        assert pace_ratio(44.0, 0.224) == pytest.approx(1.9642857, rel=1e-4)

    def test_under_pace(self):
        assert pace_ratio(20.0, 0.5) == pytest.approx(0.4)

    def test_zero_elapsed_returns_none(self):
        assert pace_ratio(10.0, 0.0) is None


# --------------------------------------------------------------------------- #
# projected_pct  (== pace * 100)
# --------------------------------------------------------------------------- #
class TestProjectedPct:
    def test_linear_extrapolation(self):
        assert projected_pct(25.0, 0.5) == pytest.approx(50.0)

    def test_overshoot(self):
        # 44% at 22.4% elapsed projects to ~196%
        assert projected_pct(44.0, 0.224) == pytest.approx(196.4286, rel=1e-4)

    def test_is_pace_times_100(self):
        assert projected_pct(30.0, 0.4) == pytest.approx(pace_ratio(30.0, 0.4) * 100)

    def test_zero_elapsed_returns_none(self):
        assert projected_pct(10.0, 0.0) is None


# --------------------------------------------------------------------------- #
# burn_per_day
# --------------------------------------------------------------------------- #
class TestBurnPerDay:
    def test_10pct_over_24h(self):
        assert burn_per_day(30.0, 20.0, 86400) == pytest.approx(10.0)

    def test_scaled_to_day_from_12h(self):
        # 5% over 12h -> 10%/day
        assert burn_per_day(25.0, 20.0, 43200) == pytest.approx(10.0)

    def test_flat_is_zero(self):
        assert burn_per_day(20.0, 20.0, 86400) == 0.0

    def test_negative_when_dropped(self):
        # window reset (util fell) -> negative burn; runway treats <=0 as not burning
        assert burn_per_day(2.0, 90.0, 86400) < 0

    def test_zero_dt_returns_none(self):
        assert burn_per_day(30.0, 20.0, 0) is None


# --------------------------------------------------------------------------- #
# runway_days
# --------------------------------------------------------------------------- #
class TestRunwayDays:
    def test_basic(self):
        # 40% used, burning 10%/day -> 6 days to 100%
        assert runway_days(40.0, 10.0) == pytest.approx(6.0)

    def test_not_burning_is_infinite(self):
        assert runway_days(40.0, 0.0) == math.inf
        assert runway_days(40.0, None) == math.inf
        assert runway_days(40.0, -5.0) == math.inf

    def test_already_full_is_zero(self):
        assert runway_days(100.0, 10.0) == 0.0
        assert runway_days(120.0, 10.0) == 0.0


# --------------------------------------------------------------------------- #
# days_to_reset
# --------------------------------------------------------------------------- #
class TestDaysToReset:
    def test_two_days_out(self):
        now = RESET - timedelta(days=2)
        assert days_to_reset(now, RESET) == pytest.approx(2.0)

    def test_negative_when_past(self):
        assert days_to_reset(RESET + timedelta(days=1), RESET) == pytest.approx(-1.0)


# --------------------------------------------------------------------------- #
# evaluate_page — the page decision
# --------------------------------------------------------------------------- #
class TestEvaluatePage:
    def test_no_page_when_on_track(self):
        page, reasons = evaluate_page(projected=80.0, runway=math.inf,
                                      dtr=3.0, elapsed_frac=0.5)
        assert page is False and reasons == []

    def test_projection_over_100_alone_does_NOT_page(self):
        # (b) ruling op#12617: the PROJECTION arm is ADVISORY ONLY — a projected
        # end-of-week >100% with NO runway signal must NOT page (it over-amplifies a
        # receding early-week spike, e.g. Musa at ~200% from a marathon that ended).
        page, reasons = evaluate_page(projected=150.0, runway=math.inf,
                                      dtr=3.0, elapsed_frac=0.5)
        assert page is False
        assert reasons == []

    def test_projection_huge_early_still_no_page(self):
        page, reasons = evaluate_page(projected=500.0, runway=math.inf,
                                      dtr=6.0, elapsed_frac=0.05)
        assert page is False and reasons == []

    def test_projection_at_floor_still_advisory_only(self):
        # even past the old elapsed floor, projection alone never pages now
        page, reasons = evaluate_page(projected=150.0, runway=math.inf,
                                      dtr=6.0, elapsed_frac=MIN_ELAPSED_FRAC)
        assert page is False and reasons == []

    def test_page_on_runway_shorter_than_reset(self):
        # runway 1.5d but 3d until reset -> exhausts first -> page (RUNWAY arm)
        page, reasons = evaluate_page(projected=80.0, runway=1.5,
                                      dtr=3.0, elapsed_frac=0.5)
        assert page is True
        assert any("runway" in r for r in reasons)

    def test_no_page_when_runway_beats_reset(self):
        page, reasons = evaluate_page(projected=80.0, runway=5.0,
                                      dtr=3.0, elapsed_frac=0.5)
        assert page is False

    def test_both_arms_runway_drives_the_page(self):
        # projection >100 AND runway<dtr -> pages, but the RUNWAY arm is what drives
        # it; the page reason set is runway-only (projection never contributes a page
        # reason).
        page, reasons = evaluate_page(projected=150.0, runway=1.0,
                                      dtr=3.0, elapsed_frac=0.5)
        assert page is True
        assert any("runway" in r for r in reasons)
        assert not any("projected" in r for r in reasons)


# --------------------------------------------------------------------------- #
# compute_pool_pace — the one-call integration surface
# --------------------------------------------------------------------------- #
class TestComputePoolPace:
    def test_on_pace_no_page(self):
        now = START + timedelta(days=3.5)            # 50% elapsed
        r = compute_pool_pace(now, used_pct=50.0, resets_at=RESET)
        assert r.elapsed_frac == pytest.approx(0.5)
        assert r.pace == pytest.approx(1.0)
        assert r.projected_pct == pytest.approx(100.0)
        assert r.runway_days == math.inf            # no prior -> no burn
        assert r.should_page is False

    def test_ahead_of_pace_is_advisory_not_a_page(self):
        # Musa-shaped: 44% used ~22% into the week -> projects ~196%. Under the (b)
        # ruling this is ADVISORY: projected_pct is still computed/shown, but with NO
        # runway signal (no prior) it must NOT page — the receding-spike false alarm
        # the console rejected.
        now = START + timedelta(days=7 * 0.224)
        r = compute_pool_pace(now, used_pct=44.0, resets_at=RESET)
        assert r.projected_pct > 100.0        # still computed for display
        assert r.runway_days == math.inf      # no prior -> no burn -> no runway arm
        assert r.should_page is False         # advisory only
        assert r.reasons == []

    def test_runway_arm_with_prior(self):
        # 60% used, prior 35% 24h ago -> 25%/day burn -> runway 1.6d,
        # but ~5d to reset -> exhausts first -> page on runway.
        now = START + timedelta(days=2)              # 5 days to reset
        prior = (35.0, now - timedelta(hours=24))
        r = compute_pool_pace(now, used_pct=60.0, resets_at=RESET, prior=prior)
        assert r.burn_per_day == pytest.approx(25.0)
        assert r.runway_days == pytest.approx((100 - 60) / 25.0)   # 1.6d
        assert r.days_to_reset == pytest.approx(5.0)
        assert r.should_page is True
        assert any("runway" in x for x in r.reasons)

    def test_flat_burn_no_runway_page(self):
        # usage flat over 24h -> not burning -> infinite runway -> no runway page
        now = START + timedelta(days=2)
        prior = (30.0, now - timedelta(hours=24))
        r = compute_pool_pace(now, used_pct=30.0, resets_at=RESET, prior=prior)
        assert r.burn_per_day == pytest.approx(0.0)
        assert r.runway_days == math.inf
        # 30% at ~28.6% elapsed -> projected ~105% (just over) but ADVISORY: flat
        # burn -> no runway arm -> no page at all.
        assert r.should_page is False
        assert r.reasons == []

    def test_window_reset_prior_does_not_false_page_runway(self):
        # A cross-reset prior (util was 90%, now 3%) -> negative burn -> inf runway,
        # never a runway page. (Caller should not pass this, but math is safe.)
        now = START + timedelta(days=1)
        prior = (90.0, now - timedelta(hours=24))
        r = compute_pool_pace(now, used_pct=3.0, resets_at=RESET, prior=prior)
        assert r.runway_days == math.inf
        assert not any("runway" in x for x in r.reasons)
