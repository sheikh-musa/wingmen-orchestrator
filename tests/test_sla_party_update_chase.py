"""Tests for the party-update-chase net (op#34349/34354 Phase 2,
docs/superpowers/plans/2026-10-31-nothing-goes-dark-phase2.md Task 2):
detection + pure target selection for operator-facing "gone dark" items.

Mirrors tests/test_sla_asks_chase.py's structure (same dead-man's-switch
shape, same per-scan cap) for the new net scripts/priority_sla_watchdog.py
adds alongside client_asks_chase/captured_triage.
"""
import scripts.priority_sla_watchdog as w
from scripts.priority_sla_watchdog import (
    party_update_chase_targets,
    PARTY_UPDATE_CHASE_EVERY_MIN,
    MAX_PARTY_UPDATE_CHASE_PER_RUN,
)

NOW = 1_800_000_000.0  # arbitrary fixed epoch seconds


def test_client_waiting_party_is_never_a_target():
    rows = [dict(id=1, kind="commitment", waiting_party="client",
                 overdue=True, overdue_reason="quiet", owner="cc-irsyad-coord")]
    assert party_update_chase_targets(rows, now=NOW, chase_state={},
                                       active_turn_check=lambda a: False) == []


def test_fired_commitment_is_excluded_sweeper_already_owns_it():
    rows = [dict(id=2, kind="commitment", waiting_party="operator",
                 overdue=True, overdue_reason="fired", owner="orch-console")]
    assert party_update_chase_targets(rows, now=NOW, chase_state={},
                                       active_turn_check=lambda a: False) == []


def test_quiet_operator_row_with_no_prior_update_is_a_target():
    rows = [dict(id=3, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: False)
    assert [t["id"] for t in out] == [3]


def test_active_turn_owner_is_suppressed():
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-irsyad-3")]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: a == "cc-irsyad-3")
    assert out == []


def test_within_cadence_of_last_chase_is_suppressed():
    state = {"4": {"last_chase_ts": NOW - 60}}  # just chased, well under the 240m cadence
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    assert party_update_chase_targets(rows, now=NOW, chase_state=state,
                                       active_turn_check=lambda a: False) == []


def test_at_cadence_boundary_second_chase_fires():
    state = {"4": {"last_chase_ts": NOW - PARTY_UPDATE_CHASE_EVERY_MIN * 60}}
    rows = [dict(id=4, kind="ask", waiting_party="operator",
                 overdue=True, overdue_reason="quiet", owner="cc-scholar")]
    out = party_update_chase_targets(rows, now=NOW, chase_state=state,
                                      active_turn_check=lambda a: False)
    assert [t["id"] for t in out] == [4]


def test_chase_is_capped_per_scan():
    rows = [dict(id=i, kind="ask", waiting_party="operator", overdue=True,
                 overdue_reason="quiet", owner="cc-scholar") for i in range(5)]
    out = party_update_chase_targets(rows, now=NOW, chase_state={},
                                      active_turn_check=lambda a: False,
                                      max_targets=MAX_PARTY_UPDATE_CHASE_PER_RUN)
    assert len(out) == MAX_PARTY_UPDATE_CHASE_PER_RUN


# ---- chase_party_update: the action (Task 3) --------------------------------

def test_chase_dry_run_sends_nothing_and_stamps_nothing():
    calls = []
    n, sent = w.chase_party_update(
        [dict(id=5, owner="cc-scholar", title="x")], dry=True, now=NOW,
        chase_state={}, send_chase=lambda o, t: calls.append((o, t)) or True,
        max_chases=3)
    assert n == 0 and calls == [] and sent == []


def test_chase_stamps_state_only_on_successful_send():
    state = {}
    n, sent = w.chase_party_update(
        [dict(id=6, owner="cc-scholar", title="x")], dry=False, now=NOW,
        chase_state=state, send_chase=lambda o, t: False, max_chases=3)
    assert n == 0 and "6" not in state


def test_chase_targets_owner_never_operator_or_console_fallback():
    seen = []
    w.chase_party_update(
        [dict(id=7, owner="cc-scholar", title="x")], dry=False, now=NOW,
        chase_state={}, send_chase=lambda o, t: seen.append(o) or True, max_chases=3)
    assert seen == ["cc-scholar"]


def test_party_update_chase_message_type_is_constraint_valid():
    # same guard as test_asks_chase_message_type_is_constraint_valid —
    # the bus message_type used by _send_party_update_chase must be one of
    # the agent_messages_message_type_check allowed values, never 'escalation'
    import inspect
    src = inspect.getsource(w._send_party_update_chase)
    assert "mtype=\"blocker\"" in src or "mtype='blocker'" in src
