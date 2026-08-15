"""Stage-2 TRIGGER-INVERSION requirement test (op#13323/op#13377, bus 22415).

Nazim, reading auto_recycle_on_bloat.py at source, found the operator's point stated in
code: classify_pane's bloat check (`if not pbs.is_bloated_k(...): return NOT-BLOATED`) is
an EARLY-RETURN GATE standing in FRONT of the idle check — so a worker lane that is idle,
done, and sitting at 30% is never even evaluated for recycle/stand-down. The requirement
is a structural INVERSION, not a threshold tweak:

  * PRIMARY trigger : idle AND nothing queued -> act, REGARDLESS of context %.
  * BACKSTOP trigger: >= PANE_FIRE_K for a lane actively working and climbing.
  * INDEPENDENT     : neither may gate the other. Today's early-return makes bloat a
                      precondition for the idle check — that is the bug.

This test encodes the PRIMARY trigger at the classify_pane level: an idle+empty worker
lane WELL BELOW the bloat bar must be PICKED UP (some act verdict), not skipped as
NOT-BLOATED. It is marked strict-xfail so it fails visibly on today's shape (proving the
gap is real) while keeping the suite green; when the inversion lands (after probe ->
pending-plan detector), remove the xfail and it goes green — the cleanest proof to the
operator that the gap was real and is now closed.

SEQUENCING: this test is the RED for the inversion; the IMPLEMENTATION waits on the probe
(the idle/done read must be trustworthy first — #22393 showed the current reader calls a
scrolled-off ghost 'real', so a done-and-idle trigger built on it today would recycle
mid-thought lanes) and the pending-plan detector (done-nothing-queued vs idle-with-a-
pending-plan). Do NOT implement the inversion here.
"""
import pytest

from scripts import auto_recycle_on_bloat as arb
from scripts.lib import fleet_health_boundaries as bnd


@pytest.mark.xfail(strict=True, reason="Stage-2 trigger inversion pending (bus 22415): "
                   "bloat early-return gate (line ~222) skips idle+done lanes below the bar. "
                   "Sequence: probe -> pending-plan detector -> this inversion -> arm-sign.")
def test_idle_done_worker_below_bloat_bar_is_picked_up():
    # An idle+empty (done) WORKER lane at ~30% of a 1M window (300k << PANE_FIRE_K=850k).
    # Inversion requirement: idle+done is the PRIMARY trigger, independent of bloat, so
    # this must be EVALUATED (an act verdict), not short-circuited as NOT-BLOATED.
    d = arb.classify_pane(
        session="wlane",
        base="cc-testworker",
        singletons=frozenset(bnd.SINGLETON_BODIES),
        worker_bases={"cc-testworker"},
        bloat_k=300.0,
        verdict_state="IDLE_EMPTY",
    )
    assert d.verdict != "NOT-BLOATED", (
        f"idle+done worker at 30% must be picked up (recycle/stand-down), "
        f"got verdict={d.verdict!r} — the bloat gate short-circuited the idle check"
    )
