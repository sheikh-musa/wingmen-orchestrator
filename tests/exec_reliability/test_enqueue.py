"""Enqueue tests (spec §2): grant -> work-item, idempotent, gate-respecting."""
from __future__ import annotations

from nervous_system.exec_reliability import enqueue

from .conftest import relay_artifact
from ._helpers import fetch_item


def test_granted_decision_enqueues_one_item(substrate, granted_decision):
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    wid = enqueue.enqueue_decision(substrate["dsn"], ref, schema=substrate["schema"])
    assert wid is not None
    item = fetch_item(substrate["dsn"], substrate["schema"], wid)
    assert item["grant_ref"] == ref
    assert item["state"] == "pending"
    assert item["consumer_type"] == "relay"
    # EXEC-4/c3: an explicit non-repo scope was assigned, never unscoped.
    assert item["lease_scope"] == ["channel:bus:cc-orchestrator"]


def test_enqueue_is_idempotent(substrate, granted_decision):
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    first = enqueue.enqueue_decision(substrate["dsn"], ref, schema=substrate["schema"])
    second = enqueue.enqueue_decision(substrate["dsn"], ref, schema=substrate["schema"])
    assert first == second  # same row, no duplicate (EXEC-2 UNIQUE idempotency_key)


def test_ungranted_decision_does_not_enqueue(substrate, granted_decision):
    # execution_status NULL -> not authorized -> no work.
    ref = granted_decision["insert"](execution_status=None,
                                     exec_artifact=relay_artifact())
    assert enqueue.enqueue_decision(substrate["dsn"], ref, schema=substrate["schema"]) is None


def test_open_challenge_window_does_not_enqueue(substrate, granted_decision):
    # granted but challenge NOT closed -> not drainable yet.
    ref = granted_decision["insert"](challenge_status="challenge_window",
                                     exec_artifact=relay_artifact())
    assert enqueue.enqueue_decision(substrate["dsn"], ref, schema=substrate["schema"]) is None


def test_decision_without_artifact_not_drained(substrate, granted_decision):
    # No exec_artifact -> we never invent one (would be improvising work).
    ref = granted_decision["insert"](exec_artifact=None)
    assert enqueue.enqueue_decision(substrate["dsn"], ref, schema=substrate["schema"]) is None
