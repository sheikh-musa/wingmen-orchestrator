"""op#11774 Phase-1 #2 — WAKE SELF-DRIVE (oracle-gated re-drive of a read-parked
body). This is the DURABLE fix #5 is the interim safety-net under: a body that read
work and parked gets RE-DRIVEN itself, no human nudge — but ONLY when the pane-truth
oracle CONFIRMS it is safe to touch.

This file locks the PURE disposition policy (oracle verdict -> what to do). The live
re-drive wiring (lease-gated, attributable CAI-817 verified-submit, INERT until
per-stage sign) is separate and carries fork-1 reqs A/B.

Policy (conservative — re-drive ONLY on confirmed clean idle; everything else the
oracle can't vouch for goes to a human):
  WORKING       -> SUPPRESS  (the body IS working; VPS-wake-latency case — the exact
                              thing the old blunt read==attending suppression was
                              protecting, now KNOWN not guessed. no noise, no touch.)
  IDLE_EMPTY    -> REDRIVE   (read + clean idle -> wake it to re-act on its inbox)
  STAGED        -> ESCALATE  (half-typed work sits unsubmitted — never blind-submit
                              it; a human decides)
  GHOST_WEDGED  -> ESCALATE  (a ghost blocks the composer; re-drive would no-op —
                              #1 probe-kill is the future resolver, escalate for now)
  UNSURE        -> ESCALATE  (can't tell -> fail-safe to a human, never re-drive blind)
"""
import importlib

sd = importlib.import_module("nervous_system.wake_self_drive")
o = importlib.import_module("nervous_system.body_activity_oracle")


def test_working_body_is_suppressed_no_touch():
    assert sd.disposition(o.WORKING) == sd.SUPPRESS


def test_clean_idle_is_redriven():
    assert sd.disposition(o.IDLE_EMPTY) == sd.REDRIVE


def test_staged_work_escalates_never_blind_submit():
    assert sd.disposition(o.STAGED) == sd.ESCALATE


def test_ghost_wedged_escalates():
    assert sd.disposition(o.GHOST_WEDGED) == sd.ESCALATE


def test_unsure_escalates_failsafe():
    assert sd.disposition(o.UNSURE) == sd.ESCALATE


def test_unknown_verdict_escalates_failsafe():
    # any unrecognized state must fail SAFE to a human, never silently re-drive.
    assert sd.disposition("something-new") == sd.ESCALATE


def test_only_idle_empty_ever_redrives():
    # the whole safety spine: REDRIVE is reachable from EXACTLY one verdict.
    redriving = [v for v in (o.WORKING, o.IDLE_EMPTY, o.STAGED, o.GHOST_WEDGED, o.UNSURE)
                 if sd.disposition(v) == sd.REDRIVE]
    assert redriving == [o.IDLE_EMPTY]
