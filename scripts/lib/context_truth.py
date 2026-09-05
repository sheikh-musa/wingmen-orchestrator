#!/usr/bin/env python3
"""context_truth — ONE answer to "how full is this body's context", from BOTH signals.

WHY THIS EXISTS. The fleet has two context oracles and each was independently declared the
ground truth, by different agents, after being burned by the other one:

  * THE PANE (`pane_bloat_signal`, op#13050). Adopted BECAUSE the DB gauge freezes on an
    idle lane — prog1 sat at 100% behind an 84h-stale gauge, and cc-irsyad read 8% on the
    gauge while its pane showed 795.9k. Verdict at the time: "the pane is the ground truth
    and the gauge lies when stale/mis-mapped."

  * THE GAUGE (`cc_session_costs.latest_context_tokens`, parsed from Claude Code's own
    transcript `usage` records — the same accounting `/context` prints).

Both conclusions were right about the failure they saw and wrong as general rules, and on
2026-08-15 the fleet paid for treating either as universal.

THE PANE'S BLIND BAND (found 2026-08-15, operator-caught). Claude Code renders the
`/clear to save {N}k` reclaim hint only BELOW a threshold and the `{N}% context used` line
only at/above ~95%. In between it shows NEITHER. Measured the same minute:

    irsyad-prog1        258.2k   hint PRESENT
    irsyad-coord        786.0k   hint PRESENT
    cc-fleet-health     939.9k   hint ABSENT   <- 94%, operator's /context screenshot
                                                  gauge said 939,903. Identical.

`pane_bloat_signal` documents "empirically from ~440k up; by the ~850k fire bar it is
reliably present" and treats an absent hint as NOT-bloated. That observation at 939.9k
falsifies it: a body can be at 94% and read as clean. The exact lower edge of the band is
still UNMEASURED — do not quote 80% as though it were.

So neither signal is sufficient and the failure modes are complementary: the gauge goes
STALE where the pane is live; the pane goes BLIND where the gauge is exact. This module
takes both and returns one answer, plus WHICH signal produced it.

THE RULE THAT MATTERS MOST — an unreadable signal is UNKNOWN, never "fine". Both callers
got this wrong in opposite directions on the same night: `pane_bloat_signal` treats an
absent hint as not-bloated (missing a 94% body), and orch-console nearly shipped a console
change rendering the same absence as green. UNKNOWN is a state a caller must handle, and a
live body that cannot be measured is worth reporting — silence about a body you cannot see
is how the operator ends up being the detector. A measurement whose tooling failed reports
"could not measure", never "clear".

Pure and side-effect free: no tmux, no DB, no I/O. Callers supply the readings.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

# Model context window these bodies run in (1M for the Opus/Sonnet bodies on this fleet).
DEFAULT_WINDOW = 1_000_000

# Beyond this the gauge is describing a world that has moved on. It only advances when a
# body takes a TURN, so an idle body's reading freezes — that is the stale-on-idle failure
# the pane was adopted to fix, and it is why staleness is a hard cutoff and not a warning.
DEFAULT_MAX_GAUGE_AGE_S = 1_800

# Same green/amber/red vocabulary the console and the recycler already use. One vocabulary
# across the fleet: a body described as "amber" must mean the same thing to every reader.
SOFT_PCT = 60
HARD_PCT = 85

# When both signals are readable and disagree by more than this, say so. The pane hint is
# RECLAIMABLE tokens and the gauge is TOTAL context, so they are not the same quantity and
# a modest gap is expected — a large one means something is mis-mapped and we want to hear
# about it rather than silently prefer one. Right after a recycle the gauge briefly carries
# the DEAD session's final reading (cc-fleet-health flagged this, #22617), which shows up
# here as a large disagreement rather than as a phantom red.
DISAGREE_PCT = 25


class ContextTruth(NamedTuple):
    """pct/level of the window, plus provenance. pct is None exactly when unknown."""
    pct: Optional[int]
    level: Optional[str]          # green | amber | red | None when unknown
    source: Optional[str]         # pane-pct | pane-hint | gauge | None
    known: bool
    disagreement: Optional[int]   # |pane - gauge| when both readable, else None
    reason: str                   # human-readable, for logs and operator alerts


def _level(pct: int) -> str:
    return "red" if pct >= HARD_PCT else ("amber" if pct >= SOFT_PCT else "green")


def _pct_from_tokens(tokens, window: int) -> Optional[int]:
    try:
        tokens = float(tokens)
    except (TypeError, ValueError):
        return None
    if tokens <= 0 or window <= 0 or tokens > window:
        return None  # a single turn cannot exceed the window — bad data, not a reading
    return int(round(tokens / window * 100))


def resolve(
    pane_pct=None,
    pane_hint_k=None,
    gauge_tokens=None,
    gauge_age_s=None,
    window: int = DEFAULT_WINDOW,
    max_gauge_age_s: int = DEFAULT_MAX_GAUGE_AGE_S,
) -> ContextTruth:
    """Best available context reading for one body.

    pane_pct      CC's `{N}% context used` line. Exact, and present only near the cliff.
    pane_hint_k   CC's `/clear to save {N}k` hint, in thousands of RECLAIMABLE tokens.
    gauge_tokens  cc_session_costs.latest_context_tokens — total context, transcript-derived.
    gauge_age_s   age of that gauge row.

    Precedence is by trustworthiness where each signal is actually valid:
      1. pane_pct — CC stating its own fill directly. Nothing beats it.
      2. pane_hint_k — live and per-session, so it beats a gauge that may be stale or
         mapped to the wrong instance. It UNDERSTATES (reclaimable < total), which is safe:
         it can make us recycle slightly early, never slightly late.
      3. a FRESH gauge — the only signal that sees into the pane's blind band.
      4. nothing readable -> UNKNOWN. Never green.
    """
    gpct = _pct_from_tokens(gauge_tokens, window)
    gauge_fresh = (
        gpct is not None
        and gauge_age_s is not None
        and gauge_age_s <= max_gauge_age_s
    )

    ppct, psource = _pane_reading(pane_pct, pane_hint_k, window)

    disagreement = abs(ppct - gpct) if (ppct is not None and gauge_fresh) else None

    if ppct is not None:
        reason = f"{psource}={ppct}%"
        if disagreement is not None and disagreement > DISAGREE_PCT:
            # Both readable and far apart. Report it; do NOT silently pick a winner.
            reason += (f"; gauge={gpct}% disagrees by {disagreement}pp "
                       f"(age {gauge_age_s}s) — mis-map, or a just-recycled body whose "
                       f"gauge still carries the dead session's final reading")
        return ContextTruth(ppct, _level(ppct), psource, True, disagreement, reason)

    if gauge_fresh:
        # The pane showed neither signal. That is the blind band, or the body is mid-turn.
        # The gauge is the ONLY thing that can see here, which is the whole point.
        return ContextTruth(
            gpct, _level(gpct), "gauge", True, None,
            f"gauge={gpct}% (age {gauge_age_s}s); pane showed neither signal — "
            f"blind band or mid-turn, so the pane alone would have read this as clean",
        )

    if gpct is not None:
        return ContextTruth(
            None, None, None, False, None,
            f"UNKNOWN — pane showed neither signal and the gauge is stale "
            f"(age {gauge_age_s}s > {max_gauge_age_s}s, last read {gpct}%). "
            f"Report it; a body you cannot measure is not a body that is fine.",
        )

    return ContextTruth(
        None, None, None, False, None,
        "UNKNOWN — no readable signal from either the pane or the gauge. "
        "Report it; do NOT treat as clear.",
    )


def _pane_reading(pane_pct, pane_hint_k, window: int):
    """(pct, source) from the pane: an explicit `{N}% context used` wins, else the
    `/clear to save {N}k` hint mapped through the window. (None, None) if neither is readable."""
    if pane_pct is not None:
        try:
            v = int(pane_pct)
            if 0 < v <= 100:
                return v, "pane-pct"
        except (TypeError, ValueError):
            pass
    if pane_hint_k is not None and _is_num(pane_hint_k):
        v = _pct_from_tokens(float(pane_hint_k) * 1000, window)
        if v is not None:
            return v, "pane-hint"
    return None, None


def lane_fire_reading(
    gauge_tokens=None,
    gauge_age_s=None,
    pane_pct=None,
    pane_hint_k=None,
    window: int = DEFAULT_WINDOW,
    max_gauge_age_s: int = DEFAULT_MAX_GAUGE_AGE_S,
) -> ContextTruth:
    """GAUGE-FIRST reading for the auto-recycle FIRE decision (bus 37752, op#19141).

    The inverse precedence of resolve(): here the FRESH gauge is the single source that
    DECIDES, and the pane is a cross-check that only LOGS disagreement — never decides.
    A stale/unreadable gauge is UNKNOWN: the caller PAGES and never assumes quiet, and a
    live pane reading (however high) does NOT rescue it into a fire verdict — it only gets
    carried into the reason so the page is actionable. This is the operator's rule for the
    recycle path; resolve() (pane-first) stays the reading for the console DISPLAY.

    Failure modes are complementary and both handled: the gauge freezes on an idle lane
    (staleness cutoff => UNKNOWN, not a frozen-low "green"); the pane is blind at ~94%
    (which is exactly why the gauge, not the pane, decides here).
    """
    gpct = _pct_from_tokens(gauge_tokens, window)
    gauge_fresh = (
        gpct is not None
        and gauge_age_s is not None
        and gauge_age_s <= max_gauge_age_s
    )
    ppct, psource = _pane_reading(pane_pct, pane_hint_k, window)

    if gauge_fresh:
        disagreement = abs(ppct - gpct) if ppct is not None else None
        reason = f"gauge={gpct}% (age {gauge_age_s}s)"
        if disagreement is not None and disagreement > DISAGREE_PCT:
            reason += (f"; {psource}={ppct}% disagrees by {disagreement}pp — the gauge decides "
                       f"(pane is a cross-check); large gap = mis-map or a just-recycled body "
                       f"whose gauge still carries the dead session's final reading")
        return ContextTruth(gpct, _level(gpct), "gauge", True, disagreement, reason)

    # Gauge is not a usable current reading -> UNKNOWN. Never fire, never assume quiet.
    if gpct is not None:
        reason = (f"UNKNOWN — gauge is stale (age {gauge_age_s}s > {max_gauge_age_s}s, "
                  f"last read {gpct}%); cannot prove current fill")
    elif gauge_tokens is not None:
        reason = (f"UNKNOWN — gauge is unreadable (bad data: {gauge_tokens} tokens vs "
                  f"{window} window); cannot measure")
    else:
        reason = "UNKNOWN — no gauge reading available; cannot measure"
    if ppct is not None:
        reason += (f". Pane reads {ppct}% ({psource}) — cross-check only, the pane never "
                   f"decides the fire; PAGE this lane rather than recycle it blind")
    else:
        reason += ". PAGE this lane rather than assume it is quiet."
    return ContextTruth(None, None, None, False, None, reason)


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
