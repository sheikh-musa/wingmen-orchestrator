#!/usr/bin/env python3
"""pool_pace.py — PURE weekly-spend PACE math (op#12617, operator GO).

The weekly_limit_monitor (op#9658) already reads each pool's `unified-7d`
utilization and warns on absolute thresholds (75% / 90%). Absolute thresholds
answer "how full is the pool NOW"; they do NOT answer "are we on track to blow
the weekly limit BEFORE it resets". Two pools both at 44% are in very different
shape if one is 20% into its week and the other is 90% into it.

This module is the PACE layer. Everything here is PURE (no I/O, no clock, no DB) so
it is deterministic and unit-testable — the caller (weekly_limit_monitor) supplies
`now`, the reading, the reset time, and (optionally) a prior reading for burn.

Definitions (per pool, per weekly window):
  ELAPSED-FRACTION  how far through the weekly window we are, 0..1.
                    window_start = resets_at - window_days; frac = (now-start)/window.
  PACE              used% / elapsed% .  Dimensionless. 1.0 == exactly on budget;
                    >1 == spending AHEAD of pace (on track to overshoot); <1 == under.
  PROJECTED         used% / elapsed-fraction .  Linear end-of-window extrapolation
                    (== pace*100). "If we keep this rate, the week ends at N%."
  BURN/day          (pct_now - pct_prev) / (dt in days), from the TRAILING-24h
                    reading pair. %/day. MUST be computed from two same-window
                    readings (a window reset drops util to ~0 -> a spurious negative
                    burn); the caller passes only a same-window prior.
  RUNWAY            days to hit 100% at the trailing-24h burn: (100-used)/burn.
                    Compared against DAYS-TO-RESET: runway < days_to_reset means the
                    pool exhausts BEFORE the weekly window resets -> page.

PAGE only on the RUNWAY arm:  RUNWAY < days-to-reset  (both finite).

(b) ruling op#12617 (console-approved): the PROJECTION arm is ADVISORY ONLY — it is
computed, stored, and shown, but it NEVER pages on its own. Early in a week
`elapsed-fraction` is tiny, so PROJECTED divides by a near-zero denominator and
over-amplifies a front-loaded spike that has already receded (e.g. a pool reading
~200% purely from a finished marathon) — paging that is a false alarm. The RUNWAY
arm is rate-based (trailing-24h burn), not elapsed-based, so it only fires once a
real, sustained burn will exhaust the pool before its weekly window resets — which
needs ~20h of same-window history to exist first. `MIN_ELAPSED_FRAC` is retained
for back-compat but no longer gates a page.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

WINDOW_DAYS = 7.0
MIN_ELAPSED_FRAC = 0.15  # suppress the projection arm below this (early-window noise)


def elapsed_fraction(now: datetime, resets_at: datetime,
                     window_days: float = WINDOW_DAYS) -> float:
    """How far through the weekly window `now` is, clamped to [0,1]."""
    window_start = resets_at - timedelta(days=window_days)
    total = (resets_at - window_start).total_seconds()
    if total <= 0:
        return 0.0
    frac = (now - window_start).total_seconds() / total
    return max(0.0, min(1.0, frac))


def pace_ratio(used_pct: float, elapsed_frac: float) -> Optional[float]:
    """used% / elapsed% . Dimensionless; 1.0 == on pace. None if no elapsed time."""
    if elapsed_frac <= 0:
        return None
    return (used_pct / 100.0) / elapsed_frac


def projected_pct(used_pct: float, elapsed_frac: float) -> Optional[float]:
    """Linear end-of-window extrapolation: used% / elapsed-fraction (== pace*100)."""
    if elapsed_frac <= 0:
        return None
    return used_pct / elapsed_frac


def burn_per_day(pct_now: float, pct_prev: float, dt_seconds: float) -> Optional[float]:
    """Trailing burn rate in %/day between two SAME-WINDOW readings. None if the
    two readings are not separated in time. May be negative/zero (flat or dropped);
    the runway consumer treats <=0 as 'not burning' -> infinite runway."""
    if dt_seconds <= 0:
        return None
    return (pct_now - pct_prev) / (dt_seconds / 86400.0)


def runway_days(used_pct: float, burn: Optional[float]) -> float:
    """Days to reach 100% at `burn` (%/day). math.inf if not burning (burn None or
    <=0). Never negative (a pool already >=100% has 0 runway)."""
    if burn is None or burn <= 0:
        return math.inf
    return max(0.0, (100.0 - used_pct) / burn)


def days_to_reset(now: datetime, resets_at: datetime) -> float:
    """Days from now until the weekly window resets (can be negative if stale)."""
    return (resets_at - now).total_seconds() / 86400.0


def evaluate_page(projected: Optional[float], runway: float, dtr: float,
                  elapsed_frac: float,
                  min_elapsed_frac: float = MIN_ELAPSED_FRAC) -> Tuple[bool, List[str]]:
    """The page decision + human reasons.

    (b) ruling op#12617 (console-approved): the RUNWAY arm is the SOLE page trigger
    — PAGE only when RUNWAY < days-to-reset (both finite, i.e. only once ~20h of
    trailing history yields a real burn). The PROJECTION arm is ADVISORY ONLY: a
    projected end-of-week >100% is still computed / stored / shown, but it NEVER
    pages on its own, because early in a week it over-amplifies a front-loaded spike
    that has already receded (e.g. Musa reading ~200% purely from a finished
    marathon). `min_elapsed_frac` is retained for signature/back-compat but no longer
    gates a page (projection is display-only). The absolute 75/90% safety path in
    weekly_limit_monitor is entirely separate and always armed.
    """
    reasons: List[str] = []
    # RUNWAY arm — the only trigger. Rate-based: fires once a genuine trailing burn
    # exhausts the pool before its weekly window resets.
    if math.isfinite(runway) and math.isfinite(dtr) and runway < dtr:
        reasons.append(
            f"runway {runway:.1f}d < {dtr:.1f}d to reset "
            f"(exhausts before the weekly window resets)")
    return bool(reasons), reasons


@dataclass
class PaceResult:
    elapsed_frac: float
    pace: Optional[float]
    projected_pct: Optional[float]
    burn_per_day: Optional[float]
    runway_days: float
    days_to_reset: float
    should_page: bool
    reasons: List[str]


def compute_pool_pace(now: datetime, used_pct: float, resets_at: datetime,
                      prior: Optional[Tuple[float, datetime]] = None,
                      window_days: float = WINDOW_DAYS,
                      min_elapsed_frac: float = MIN_ELAPSED_FRAC) -> PaceResult:
    """One-call pace computation for a pool.

    `prior` = (pct_prev, recorded_at_prev) — a SAME-WINDOW trailing reading (the
    caller must not pass a reading from before the last reset). None -> no burn/
    runway yet (first poll of a window); runway is math.inf so it never pages.
    """
    ef = elapsed_fraction(now, resets_at, window_days)
    pc = pace_ratio(used_pct, ef)
    proj = projected_pct(used_pct, ef)
    burn = None
    if prior is not None:
        pct_prev, rec_prev = prior
        burn = burn_per_day(used_pct, pct_prev, (now - rec_prev).total_seconds())
    rw = runway_days(used_pct, burn)
    dtr = days_to_reset(now, resets_at)
    page, reasons = evaluate_page(proj, rw, dtr, ef, min_elapsed_frac)
    return PaceResult(
        elapsed_frac=ef, pace=pc, projected_pct=proj, burn_per_day=burn,
        runway_days=rw, days_to_reset=dtr, should_page=page, reasons=reasons)
