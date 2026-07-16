"""Demoted-watchdog / safety-net tests (spec §4)."""
from __future__ import annotations

import uuid

from nervous_system.exec_reliability import safety_net

from .conftest import relay_artifact, RUNNER_AGENT
from ._helpers import (
    insert_pending_item, fetch_item, max_bus_id, bus_rows_since, cleanup_bus,
)

AGENT = RUNNER_AGENT


def test_expired_lease_is_reaped_to_pending(substrate, granted_decision):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    # A dead runner's item: claimed, lease long expired, within retry budget.
    wid = insert_pending_item(
        dsn, schema, grant_ref=ref, idempotency_key=idem,
        named_artifact=relay_artifact(),
        lease_scope=["channel:bus:cc-orchestrator"],
        state="claimed", claimed_by="dead-runner", attempts=1, max_attempts=5,
        lease_expires_at_sql="now() - interval '10 minutes'")

    reaped = safety_net.reap_expired_leases(dsn, schema=schema)
    assert wid in reaped
    row = fetch_item(dsn, schema, wid)
    assert row["state"] == "pending"
    assert row["claimed_by"] is None
    assert row["lease_expires_at"] is None


def test_exhausted_item_is_not_reaped(substrate, granted_decision):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    # attempts == max_attempts -> the "no infinite auto-retry" boundary.
    wid = insert_pending_item(
        dsn, schema, grant_ref=ref, idempotency_key=idem,
        named_artifact=relay_artifact(),
        lease_scope=["channel:bus:cc-orchestrator"],
        state="claimed", claimed_by="dead-runner", attempts=5, max_attempts=5,
        lease_expires_at_sql="now() - interval '10 minutes'")

    reaped = safety_net.reap_expired_leases(dsn, schema=schema)
    assert wid not in reaped
    assert fetch_item(dsn, schema, wid)["state"] == "claimed"


def test_exhausted_item_alerts_hub_no_retry(substrate, granted_decision, runner_agent):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    wid = insert_pending_item(
        dsn, schema, grant_ref=ref, idempotency_key=idem,
        named_artifact=relay_artifact(),
        lease_scope=["channel:bus:cc-orchestrator"],
        state="claimed", claimed_by="dead-runner", attempts=5, max_attempts=5,
        lease_expires_at_sql="now() - interval '10 minutes'")

    before = max_bus_id(dsn)
    try:
        stuck = safety_net.alert_stuck_items(dsn, from_agent=AGENT, schema=schema,
                                             stuck_threshold_seconds=0)
        assert any(s["id"] == wid for s in stuck)
        rows = bus_rows_since(dsn, before, AGENT)
        stuck_rows = [r for r in rows if "STUCK" in r["subject"]]
        assert len(stuck_rows) >= 1
        assert stuck_rows[0]["message_type"] == "blocker"
        assert "EXHAUSTED" in stuck_rows[0]["body"]
    finally:
        cleanup_bus(dsn, before, AGENT)
