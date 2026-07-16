"""Fail-closed BOUNCE tests (EXEC-3, spec §3.3 + CAI-464 §8 bright line).

The load-bearing assertion: a bounce produces the bus row + state='bounced' and
NOTHING else — no send, no delivery-ledger row, no op executed.
"""
from __future__ import annotations

import uuid

from nervous_system.exec_reliability import runner

from .conftest import relay_artifact, RUNNER_AGENT
from ._helpers import (
    claim_specific, count_ledger, fetch_item, insert_pending_item,
    max_bus_id, bus_rows_since, cleanup_bus,
)

AGENT = RUNNER_AGENT


class SpySender:
    """Records every send. In a correct bounce it must NEVER be called."""
    def __init__(self):
        self.calls = []

    def __call__(self, cur, channel, target, notice, *, from_agent):
        self.calls.append((channel, target, notice))
        return "spy:1"


def _bounce_and_assert_zero_side_effects(substrate, item_id, idem, expect_reason_sub):
    dsn, schema = substrate["dsn"], substrate["schema"]
    spy = SpySender()
    before = max_bus_id(dsn)

    item = claim_specific(dsn, schema, item_id, AGENT)
    out = runner.process_item(dsn, item, agent_id=AGENT, sender=spy, schema=schema)

    try:
        # 1. Outcome + terminal state.
        assert out["outcome"] == "bounced"
        assert expect_reason_sub in out["reason"]
        assert fetch_item(dsn, schema, item_id)["state"] == "bounced"
        # 2. ZERO side-effects: the sender was never invoked.
        assert spy.calls == []
        # 3. No delivery was recorded.
        assert count_ledger(dsn, schema, idem) == 0
        # 4. Exactly ONE bus row (the bounce notice), and it is a blocker.
        rows = bus_rows_since(dsn, before, AGENT)
        assert len(rows) == 1
        assert rows[0]["message_type"] == "blocker"
        assert "BOUNCE" in rows[0]["subject"]
    finally:
        cleanup_bus(dsn, before, AGENT)


def test_bounce_when_grant_revoked(substrate, granted_decision, runner_agent):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    wid = insert_pending_item(dsn, schema, grant_ref=ref, idempotency_key=idem,
                              named_artifact=relay_artifact(),
                              lease_scope=["channel:bus:cc-orchestrator"])
    # Revoke the grant AFTER enqueue (superseded) -> runner must refuse.
    granted_decision["insert"](status="superseded", exec_artifact=relay_artifact())
    _bounce_and_assert_zero_side_effects(substrate, wid, idem, "revoked")


def test_bounce_when_required_decision_unsettled(substrate, granted_decision, runner_agent):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    # An extra dependency that is NOT settled (does not exist) -> fail closed.
    art = relay_artifact(requires_settled=["EXEC-RL-UNSETTLED-XYZ"])
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    wid = insert_pending_item(dsn, schema, grant_ref=ref, idempotency_key=idem,
                              named_artifact=art,
                              lease_scope=["channel:bus:cc-orchestrator"])
    _bounce_and_assert_zero_side_effects(substrate, wid, idem, "required decision unsettled")


def test_bounce_when_unscoped_authority(substrate, granted_decision, runner_agent):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    # Empty lease_scope -> CAI-464 c3 violation -> bounce, zero side-effects.
    wid = insert_pending_item(dsn, schema, grant_ref=ref, idempotency_key=idem,
                              named_artifact=relay_artifact(), lease_scope=[])
    _bounce_and_assert_zero_side_effects(substrate, wid, idem, "lease_scope")


def test_bounce_when_unknown_channel(substrate, granted_decision, runner_agent):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    art = relay_artifact()
    art["channel"] = "telegram"  # not authorized in MVP
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    wid = insert_pending_item(dsn, schema, grant_ref=ref, idempotency_key=idem,
                              named_artifact=art,
                              lease_scope=["channel:telegram:cc-orchestrator"])
    _bounce_and_assert_zero_side_effects(substrate, wid, idem, "not authorized")
