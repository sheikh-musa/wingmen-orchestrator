"""Atomic-claim tests (spec §3.1): concurrent runners never double-claim."""
from __future__ import annotations

import concurrent.futures

from nervous_system.exec_reliability import runner

from .conftest import relay_artifact
from ._helpers import insert_pending_item


def _seed(substrate, granted_decision, n):
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    for i in range(n):
        insert_pending_item(
            substrate["dsn"], substrate["schema"],
            grant_ref=ref,
            idempotency_key=f"{ref}:relay:{i}",
            named_artifact=relay_artifact(),
            lease_scope=["channel:bus:cc-orchestrator"],
        )
    return ref


def test_concurrent_claims_do_not_double_claim(substrate, granted_decision):
    n_items = 6
    n_workers = 12
    _seed(substrate, granted_decision, n_items)

    def claim():
        item = runner.claim_one(
            substrate["dsn"], agent_id="cc-exec-runner-test",
            ttl_seconds=300, schema=substrate["schema"])
        return item["id"] if item else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as ex:
        results = [f.result() for f in
                   [ex.submit(claim) for _ in range(n_workers)]]

    claimed = [r for r in results if r is not None]
    # Every item claimed exactly once; no id appears twice (SKIP LOCKED).
    assert len(claimed) == n_items
    assert len(set(claimed)) == n_items
    # The extra workers found nothing rather than double-claiming.
    assert results.count(None) == n_workers - n_items


def test_claim_is_fifo_oldest_first(substrate, granted_decision):
    ref = granted_decision["insert"](exec_artifact=relay_artifact())
    first = insert_pending_item(
        substrate["dsn"], substrate["schema"], grant_ref=ref,
        idempotency_key=f"{ref}:relay:a", named_artifact=relay_artifact(),
        lease_scope=["channel:bus:cc-orchestrator"])
    insert_pending_item(
        substrate["dsn"], substrate["schema"], grant_ref=ref,
        idempotency_key=f"{ref}:relay:b", named_artifact=relay_artifact(),
        lease_scope=["channel:bus:cc-orchestrator"])
    got = runner.claim_one(substrate["dsn"], schema=substrate["schema"])
    assert got["id"] == first  # oldest created_at claimed first
