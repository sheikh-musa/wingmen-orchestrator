#!/usr/bin/env python3
"""wake_self_drive — op#11774 Phase-1 #2: oracle-gated RE-DRIVE of a read-parked body.

The durable fix for the operator's #1 gap ("a body read work then parked, nobody
noticed for hours"). #5 (SLA read-parked -> escalate a human) is the interim safety
net UNDER this; #2 makes the body re-drive ITSELF when the pane-truth oracle confirms
it is safe to touch — no human in the loop for the routine case.

SAFETY SPINE (this file = the PURE policy; the live wiring is separate + INERT until
per-stage sign):
  * RE-DRIVE only on a verdict the oracle can VOUCH for as clean-idle. Every verdict
    it cannot vouch for goes to a human (ESCALATE), never a blind re-drive.
  * WORKING -> SUPPRESS: the body IS working (the VPS-wake-latency case the old blunt
    read==attending suppression protected — now KNOWN from the pane, not guessed).

The live re-drive path (NOT in this increment — pending console design-sign) will
carry fork-1's operator invariants, code-enforced + asserted:
  (A) SRE-LEASE-GATED: only the fleet_health_lease holder may trigger a re-drive;
      a non-holder is refused fail-closed.
  (B) NO PHANTOM INJECTIONS: every re-drive is an attributable, logged CAI-817
      verified-submit (confirms the submit landed) — never a raw/unattributable
      keystroke; and it ships behind its own OFF-by-default enable-gate (observe-first,
      the incident lesson: gate inert BEFORE the action path can run).
"""
from __future__ import annotations

try:  # runtime: nervous_system is on sys.path (bare import); pytest: package import
    from body_activity_oracle import (
        WORKING, IDLE_EMPTY, STAGED, GHOST_WEDGED, UNSURE,
    )
except ImportError:  # pragma: no cover
    from nervous_system.body_activity_oracle import (
        WORKING, IDLE_EMPTY, STAGED, GHOST_WEDGED, UNSURE,
    )

# Dispositions.
SUPPRESS = "SUPPRESS"   # do nothing — the body is working; no noise, no touch
REDRIVE = "REDRIVE"     # wake it to re-act on its inbox (attributable verified-submit)
ESCALATE = "ESCALATE"   # hand to a human (#5) — ambiguous / can't be re-driven safely

# oracle verdict -> disposition. Only IDLE_EMPTY re-drives; the mapping is explicit
# (not a default-to-redrive) so an unrecognized/new verdict fails SAFE to ESCALATE.
_POLICY = {
    WORKING: SUPPRESS,
    IDLE_EMPTY: REDRIVE,
    STAGED: ESCALATE,
    GHOST_WEDGED: ESCALATE,
    UNSURE: ESCALATE,
}


def disposition(verdict_state: str) -> str:
    """Map an oracle verdict state to the re-drive disposition. PURE + total: any
    unknown state -> ESCALATE (fail-safe to a human, never a silent re-drive)."""
    return _POLICY.get(verdict_state, ESCALATE)
