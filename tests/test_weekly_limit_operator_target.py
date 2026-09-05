"""weekly_limit_monitor's operator target MUST match what weekly_alert_relay consumes.

WHY (audit #5 erratum, Nazim 37739). weekly_limit_monitor emits operator warnings to
to_agent=OPERATOR_AGENT. Those rows are NOT dead-letters even though 'musa' has no
agent_wake owner: they are consumed by nervous_system/weekly_alert_relay.py — Nazim's
launchd daemon that watches to_agent='musa' by a durable CURSOR and delivers each ⚠️ row
to the operator's phone via nazim_send.sh. agent_wake eligibility is NOT deliverability
(a cursor relay is invisible to it); retargeting OPERATOR_AGENT to orch-console (b72a6fe)
orphaned the relay and was reverted.

This pins the PRODUCER↔CONSUMER contract: the address weekly_limit emits to must be the
exact address the relay watches, so the two can never silently drift apart again.

Prod-clean: import only, no DB (both modules are import-clean).
"""
from nervous_system import weekly_limit_monitor as w
from nervous_system import weekly_alert_relay as relay


def test_operator_target_matches_the_relay_watched_address():
    # The relay's _WHERE selects `to_agent = '<addr>'`; the producer must emit to that
    # exact addr, or the relay stops delivering (the b72a6fe regression).
    assert f"to_agent = '{w.OPERATOR_AGENT}'" in relay._WHERE, (
        f"weekly_limit emits to {w.OPERATOR_AGENT!r} but weekly_alert_relay watches a "
        f"different address (_WHERE={relay._WHERE!r}) — the operator would stop getting "
        "direct-to-phone weekly warnings."
    )


def test_operator_target_is_musa_the_relay_channel():
    # Guards against a silent flip back to a non-relayed address (e.g. orch-console).
    assert w.OPERATOR_AGENT == "musa"
