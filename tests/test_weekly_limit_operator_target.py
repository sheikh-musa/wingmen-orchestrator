"""weekly_limit_monitor must page the operator via a DELIVERABLE address (audit #5A, Nazim).

The operator-bound threshold/pace pages targeted to_agent='musa' — which has NO live wake
owner (agent_wake.is_wake_eligible_recipient('musa') is False; no agent_status row), so
every URGENT pool-at-90pct P1 dead-lettered unread (21 rows, 7 of them P1, 08-08..09-04).
Retarget to orch-console: the sanctioned operator-facing body (it relays to Musa via
nazim_send) AND a wake-eligible recipient, so the page actually reaches someone who acts.

Prod-clean: import only, no DB.
"""
from nervous_system import weekly_limit_monitor as w
from nervous_system.agent_wake import is_wake_eligible_recipient


def test_operator_target_is_a_deliverable_wake_owner():
    assert is_wake_eligible_recipient(w.OPERATOR_AGENT), (
        f"weekly-limit operator pages target {w.OPERATOR_AGENT!r}, which has no live wake "
        "owner -> they dead-letter unread. Retarget to a wake-eligible body (orch-console)."
    )


def test_operator_target_is_not_the_bare_operator_handle():
    # 'musa' is the operator (a human), not a deliverable agent address — regression guard.
    assert w.OPERATOR_AGENT != "musa"
