"""Three content-shape signal extractors per CAI-RESP-164 R1-AMENDED.

Pure functions over filesystem inputs. Use jsonl_safe_read defensively;
never raise. Each signal returns SignalResult with match (bool|None) and
unobservable (bool). 'unobservable' means >50% of inputs unreadable —
caller treats as no_action (NOT hard_kill, NOT monitored).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from nervous_system.jsonl_safe_read import (
    read_first_user_message,
    safe_file_stats,
)

# Starting thresholds per CAI-RESP-164 R2 (30d calibration window).
SIGNAL_A_MAX_BYTES = 80 * 1024
SIGNAL_B_BAND_LO = 5
SIGNAL_B_BAND_HI = 600
SIGNAL_B_MIN_SPAN_SECONDS = 2 * 60 * 60
MIN_SESSIONS_FOR_SIGNAL = 10
UNOBSERVABLE_THRESHOLD = 0.5


@dataclass(frozen=True)
class SignalResult:
    match: Optional[bool]
    value: Any
    unobservable: bool = False
    sample_count: int = 0


def signal_a_median_size(paths: list[Path]) -> SignalResult:
    """Median file size across last 10 sessions; burn-pattern if < 80KB."""
    if len(paths) < MIN_SESSIONS_FOR_SIGNAL:
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(paths))
    sizes = []
    for p in paths[:MIN_SESSIONS_FOR_SIGNAL]:
        stats = safe_file_stats(p)
        if stats is not None:
            sizes.append(stats.size_bytes)
    if len(sizes) < MIN_SESSIONS_FOR_SIGNAL * (1 - UNOBSERVABLE_THRESHOLD):
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(sizes))
    median = statistics.median(sizes)
    return SignalResult(match=(median < SIGNAL_A_MAX_BYTES), value=median, sample_count=len(sizes))


def signal_b_cadence_band(paths: list[Path]) -> SignalResult:
    """Cadence in [5,600] sustained over >2h (7200s span)."""
    if len(paths) < MIN_SESSIONS_FOR_SIGNAL:
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(paths))
    mtimes = []
    for p in paths:
        stats = safe_file_stats(p)
        if stats is not None:
            mtimes.append(stats.mtime)
    if len(mtimes) < MIN_SESSIONS_FOR_SIGNAL * (1 - UNOBSERVABLE_THRESHOLD):
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(mtimes))
    mtimes.sort()
    span = mtimes[-1] - mtimes[0]
    if span < SIGNAL_B_MIN_SPAN_SECONDS:
        return SignalResult(match=False, value={"span": span}, sample_count=len(mtimes))
    gaps = [mtimes[i + 1] - mtimes[i] for i in range(len(mtimes) - 1)]
    all_in_band = all(SIGNAL_B_BAND_LO <= g <= SIGNAL_B_BAND_HI for g in gaps)
    avg = sum(gaps) / len(gaps)
    return SignalResult(
        match=all_in_band,
        value={"avg_gap": avg, "span": span, "all_in_band": all_in_band},
        sample_count=len(mtimes),
    )


def signal_c_identical_prompts(paths: list[Path]) -> SignalResult:
    """Last 10 sessions all have identical first-user-message content."""
    if len(paths) < MIN_SESSIONS_FOR_SIGNAL:
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(paths))
    prompts: list[str] = []
    for p in paths[:MIN_SESSIONS_FOR_SIGNAL]:
        text = read_first_user_message(p)
        if text is not None:
            prompts.append(text)
    if len(prompts) < MIN_SESSIONS_FOR_SIGNAL * (1 - UNOBSERVABLE_THRESHOLD):
        return SignalResult(match=None, value=None, unobservable=True, sample_count=len(prompts))
    first = prompts[0]
    all_match = all(p == first for p in prompts)
    return SignalResult(
        match=all_match,
        value=first if all_match else None,
        sample_count=len(prompts),
    )
