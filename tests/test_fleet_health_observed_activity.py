"""fleet_health observed-activity gate (cc-fleet-health, 2026-08-17; re-derived from #23952
after the routing premise fell — Nazim #24044).

WHY. The sweep marks any agent whose heartbeat is > STALE_MIN stale as offline. But an
on-demand body (e.g. cc-quality, boot_quality.sh has NO heartbeat loop BY DESIGN, CAI-729→733)
goes heartbeat-stale WHILE actively working — so the sweep writes a status that says the
fleet's auditor is offline while it is mid-audit. That is a lie the board tells its readers
(no code acts on it — routing is pane-based, op#11297 — but honest telemetry has value).

THE FIX IS OBSERVED-ACTIVITY, NOT A SKIP-LIST. Adding cc-quality to protected_agents would
also hide a GENUINELY dead cc-quality (Nazim #24044). Instead: before flipping a stale-hb
agent offline, check for OBSERVED activity — a recent bus row FROM it, or a live co-located
tmux session. Any observed => alive, skip the flip. No observed AND stale-hb => genuinely
dead, flip as before. ('observation > the heartbeat column', same principle as the pane_busy
collapse.) These tests pin the PURE predicate; the SQL that resolves `recent_bus` is verified
separately against the live DB.
"""
from scripts.fleet_health import observed_activity


def test_recent_bus_row_is_observed_alive():
    # a bus message FROM the agent within the window proves the identity is doing work,
    # regardless of a stale heartbeat -> observed, do NOT flip.
    observed, reason = observed_activity(recent_bus=True, tmux_session="quality",
                                         live_sessions=set())
    assert observed is True
    assert "bus" in reason.lower(), f"reason should name the bus signal: {reason!r}"


def test_live_colocated_tmux_is_observed_alive():
    # no recent bus row, but the agent's registered tmux session is live on this host
    # -> the pane is up -> observed, do NOT flip.
    observed, reason = observed_activity(recent_bus=False, tmux_session="quality",
                                         live_sessions={"quality", "cai"})
    assert observed is True
    assert "tmux" in reason.lower() or "session" in reason.lower(), \
        f"reason should name the tmux signal: {reason!r}"


def test_no_bus_and_no_live_session_is_not_observed():
    # stale heartbeat, no recent bus row, and the registered session is NOT live here
    # -> genuinely dead (as far as we can observe) -> flip offline as before.
    observed, reason = observed_activity(recent_bus=False, tmux_session="quality",
                                         live_sessions={"cai"})
    assert observed is False, f"unexpectedly observed: {reason!r}"


def test_no_tmux_session_and_no_bus_is_not_observed():
    # a null tmux_session (never registered a pane) with no bus row -> not observed.
    observed, reason = observed_activity(recent_bus=False, tmux_session=None,
                                         live_sessions={"quality"})
    assert observed is False, f"unexpectedly observed: {reason!r}"
