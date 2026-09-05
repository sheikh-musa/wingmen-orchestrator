"""fleet_health SURFACES (never reaps) unread rows whose to_agent has no live wake owner.

WHY (substrate audit #5B). Operator/non-agent addresses like 'musa' and 'substrate' are NOT
wake-eligible recipients (agent_wake never delivers to them), so messages sent there
dead-letter unread forever — and step-4's archive deliberately SPARES them, because reaping
would hide a real misroute. This detector makes them VISIBLE: one coalesced surface to
orch-console per (to_agent) per day, NEVER reaped. Deliverable identities (cc-* lanes, cai,
console, and the hub on its P0/P1 floor) are NOT flagged — a dead-but-once-live cc lane is
step-4's job, not this detector's.

Prod-clean: pure classification only, no DB (importing fleet_health must not load .env under
pytest — the module guards load_dotenv on PYTEST_CURRENT_TEST).
"""
from scripts import fleet_health as fh


def test_operator_and_non_address_are_undeliverable():
    assert fh._undeliverable("musa"), "the operator handle has no live wake owner"
    assert fh._undeliverable("substrate"), "'substrate' is a non-address, no wake owner"


def test_identities_with_a_wake_owner_are_not_flagged():
    assert not fh._undeliverable("cc-quality"), "a worker lane is a deliverable identity"
    assert not fh._undeliverable("cai")
    assert not fh._undeliverable("orch-console")
    # the hub is eligible on its narrow P0/P1 floor — the detector uses that floor, so it is
    # NEVER treated as an undeliverable dead-letter sink.
    assert not fh._undeliverable("cc-orchestrator")


def test_none_target_is_not_flagged():
    # a NULL to_agent is filtered by the query; the predicate must not crash on it either.
    assert not fh._undeliverable(None)
