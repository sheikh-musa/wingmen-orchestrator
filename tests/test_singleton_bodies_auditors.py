"""Auditor/brain singletons must be NAMED members of SINGLETON_BODIES.

CAI-RESP-1392 item (A), affirmed 2026-09-04: cc-quality, cc-storefront, cc-finance
are CAI-500 singletons and must be added to the named invariant. Until then they
were protected only by the emergent "unknown -> singleton" default in some paths,
and — critically — were MISSING from fhb.SINGLETON_BODIES, so worker-recycle paths
that exclude on that set (sre_lane_recycle.discover_lanes, auto_recycle_on_bloat,
lane_selfrecycle_detect, checkpoint_recycle_driver, and the proactive-recycle-nudge
tier) treated them as WORKER LANES. That is why the ~80% [proactive-recycle-nudge]
fired on cc-quality (bus 38900/38902) even though the tier "excludes singletons":
the mechanism excludes SINGLETON_BODIES, but cc-quality was not IN the set.

Adding them makes the exclusion + assert_sre_never_targets_singleton cover them,
defensively (they recycle via their OWN reset_<name>.sh once armed, never the
worker path). Defensive-only; Nazim gates the diff.
"""
import pytest

from scripts.lib import fleet_health_boundaries as fhb

AUDITOR_SINGLETONS = ["cc-quality", "cc-storefront", "cc-finance"]


@pytest.mark.parametrize("agent", AUDITOR_SINGLETONS)
def test_auditor_singleton_is_named_in_singleton_bodies(agent):
    """The named invariant must include the auditor/brain singletons."""
    assert agent in fhb.SINGLETON_BODIES, (
        f"{agent} must be a named SINGLETON_BODY (CAI-RESP-1392 A) so worker-recycle "
        f"paths exclude it and the SRE can never target it")


@pytest.mark.parametrize("agent", AUDITOR_SINGLETONS)
def test_sre_can_never_target_auditor_singleton(agent):
    """assert_sre_never_targets_singleton must fail-closed for these bodies under the
    SRE identity — the same §3(a) protection the other singletons already have."""
    with pytest.raises(fhb.BoundaryViolation):
        fhb.assert_sre_never_targets_singleton(agent, identity=fhb.SRE_AGENT_ID)
