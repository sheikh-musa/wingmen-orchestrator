"""Server-side invariant lock for op#18542 / op#9088.

The top-bloat GLANCE was widened (client-side, fleet.js) to include coordinators,
but the LANE LIST / context_bloat section must STAY coordinator-excluded so a
coordinator never gets a double card (op#9088). This is a regression guard on the
existing `_context_bloat` exclusion — it passes today; it must keep passing.
"""
from nervous_system.console import app as console_app


def _row(cc_identity, ctx_tokens, age_s=5, sub_tag=None):
    return {"cc_identity": cc_identity, "ctx_tokens": ctx_tokens,
            "age_s": age_s, "sub_tag": sub_tag}


def test_context_bloat_excludes_coordinators_keeps_worker_lanes():
    rows = [
        _row("cc-fleet-health", 880_000),  # coordinator (op#9088) -> excluded
        _row("cai", 110_000),              # coordinator -> excluded
        _row("cc-orchestrator", 760_000),  # coordinator -> excluded
        _row("cc-ihsanos", 300_000),       # worker lane -> kept
    ]
    out = console_app._context_bloat(rows)
    agents = {r["agent"] for r in out}
    assert "cc-ihsanos" in agents, "worker lane must appear in context_bloat"
    assert "cc-fleet-health" not in agents, "coordinator must NOT appear (op#9088)"
    assert "cai" not in agents
    assert "cc-orchestrator" not in agents


def test_context_bloat_worker_lane_carries_pct_and_level():
    """A kept worker lane gets a computed pct/level from the shared thresholds."""
    out = console_app._context_bloat([_row("cc-ihsanos", 300_000)])
    assert len(out) == 1
    assert out[0]["agent"] == "cc-ihsanos"
    assert out[0]["pct"] == 30
    assert out[0]["level"] == "green"
