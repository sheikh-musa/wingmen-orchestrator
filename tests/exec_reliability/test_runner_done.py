"""End-to-end happy path (spec §3.5/§3.6): enqueue -> claim -> deliver -> done."""
from __future__ import annotations

import uuid

from nervous_system.exec_reliability import runner

from .conftest import relay_artifact, RUNNER_AGENT
from ._helpers import (
    insert_pending_item, fetch_item, count_ledger, claim_specific,
    max_bus_id, bus_rows_since, cleanup_bus,
)

AGENT = RUNNER_AGENT


class CountingSender:
    def __init__(self):
        self.count = 0

    def __call__(self, cur, channel, target, notice, *, from_agent):
        self.count += 1
        return f"spy:{self.count}"


def test_full_cycle_marks_done_and_records(substrate, granted_decision, runner_agent):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    wid = insert_pending_item(dsn, schema, grant_ref=ref, idempotency_key=idem,
                              named_artifact=relay_artifact(),
                              lease_scope=["channel:bus:cc-orchestrator"])
    sender = CountingSender()
    before = max_bus_id(dsn)
    try:
        item = claim_specific(dsn, schema, wid, AGENT)
        out = runner.process_item(dsn, item, agent_id=AGENT, sender=sender, schema=schema)
        assert out["outcome"] == "done"
        row = fetch_item(dsn, schema, wid)
        assert row["state"] == "done"
        assert row["result"]["status"] == "delivered"
        assert sender.count == 1
        assert count_ledger(dsn, schema, idem) == 1
        # Attributable done-report on the bus, own agent_id.
        done_rows = [r for r in bus_rows_since(dsn, before, AGENT) if "DONE" in r["subject"]]
        assert len(done_rows) == 1
    finally:
        cleanup_bus(dsn, before, AGENT)
