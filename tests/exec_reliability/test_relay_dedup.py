"""Relay dedup tests (spec §5, CAI-464 §8 c1/c2).

At-least-once WITH dedup:
  * a re-run with the same idempotency_key does NOT double-send (ledger check);
  * a crash-after-send-before-record RE-DELIVERS rather than silently dropping.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from nervous_system.exec_reliability import relay

from .conftest import relay_artifact
from ._helpers import count_ledger, insert_pending_item, fetch_item

AGENT = "cc-exec-runner"


class CountingSender:
    def __init__(self):
        self.count = 0
        self.notices = []

    def __call__(self, cur, channel, target, notice, *, from_agent):
        self.count += 1
        self.notices.append(notice)
        return f"spy:{self.count}"


def _seed_item(substrate, granted_decision):
    dsn, schema = substrate["dsn"], substrate["schema"]
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    idem = f"{ref}:relay:{uuid.uuid4().hex[:6]}"
    wid = insert_pending_item(dsn, schema, grant_ref=ref, idempotency_key=idem,
                              named_artifact=relay_artifact(),
                              lease_scope=["channel:bus:cc-orchestrator"])
    return fetch_item(dsn, schema, wid)


def test_first_delivery_sends_and_records(substrate, granted_decision):
    dsn, schema = substrate["dsn"], substrate["schema"]
    item = _seed_item(substrate, granted_decision)
    sender = CountingSender()
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        out = relay.deliver(cur, item, runner_agent_id=AGENT, sender=sender, schema=schema)
    assert out["status"] == "delivered"
    assert sender.count == 1
    assert count_ledger(dsn, schema, item["idempotency_key"]) == 1


def test_rerun_same_key_does_not_double_send(substrate, granted_decision):
    dsn, schema = substrate["dsn"], substrate["schema"]
    item = _seed_item(substrate, granted_decision)
    sender = CountingSender()
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        relay.deliver(cur, item, runner_agent_id=AGENT, sender=sender, schema=schema)
        out2 = relay.deliver(cur, item, runner_agent_id=AGENT, sender=sender, schema=schema)
    # Second run hit the ledger -> deduped, NO second send.
    assert out2["status"] == "deduped"
    assert sender.count == 1
    assert count_ledger(dsn, schema, item["idempotency_key"]) == 1


def test_crash_before_record_redelivers_never_drops(substrate, granted_decision, monkeypatch):
    dsn, schema = substrate["dsn"], substrate["schema"]
    item = _seed_item(substrate, granted_decision)
    sender = CountingSender()

    # Simulate the crash-after-send-before-record window: the send lands but the
    # ledger write blows up before it commits.
    def boom(*a, **k):
        raise RuntimeError("simulated crash before ledger record")

    monkeypatch.setattr(relay, "_record_delivery", boom)
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(RuntimeError):
            relay.deliver(cur, item, runner_agent_id=AGENT, sender=sender, schema=schema)
    # Send happened, but NO ledger row -> the delivery is NOT silently dropped.
    assert sender.count == 1
    assert count_ledger(dsn, schema, item["idempotency_key"]) == 0

    # Retry (recovered): ledger still empty -> RE-DELIVER (a duplicate is OK).
    monkeypatch.undo()
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        out = relay.deliver(cur, item, runner_agent_id=AGENT, sender=sender, schema=schema)
    assert out["status"] == "delivered"
    assert sender.count == 2  # re-delivered rather than dropped
    assert count_ledger(dsn, schema, item["idempotency_key"]) == 1


def test_notice_is_delivery_not_execution(substrate, granted_decision):
    """CAI-464 c2 bright line: the payload is a NOTICE, not the op itself."""
    item = _seed_item(substrate, granted_decision)
    notice = relay.build_notice(item)
    assert "delivery notice only" in notice
    assert "NOT executed" in notice
