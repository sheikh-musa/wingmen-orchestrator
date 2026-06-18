"""SSE broadcaster (CAI-RESP-264 condition 5: process isolation / slow-consumer-safe).

A published row reaches a subscribed client; a stalled (full-queue) client is
dropped/resynced and NEVER blocks other consumers or the publisher.
"""
import asyncio

import pytest

from nervous_system.console.feed import Broadcaster


async def test_published_event_reaches_subscriber():
    b = Broadcaster(queue_size=8)
    sub = b.subscribe()
    await b.publish({"id": 1, "subject": "hello"})
    evt = await asyncio.wait_for(sub.get(), timeout=1.0)
    assert evt["id"] == 1
    b.unsubscribe(sub)


async def test_two_subscribers_both_receive():
    b = Broadcaster(queue_size=8)
    s1 = b.subscribe()
    s2 = b.subscribe()
    await b.publish({"id": 7})
    e1 = await asyncio.wait_for(s1.get(), timeout=1.0)
    e2 = await asyncio.wait_for(s2.get(), timeout=1.0)
    assert e1["id"] == 7 and e2["id"] == 7


async def test_slow_consumer_does_not_block_publisher():
    b = Broadcaster(queue_size=2)
    slow = b.subscribe()  # never drains
    fast = b.subscribe()
    received = []
    # Publish more than the slow consumer's queue can hold; the fast consumer
    # drains concurrently so it keeps receiving real events.
    for i in range(20):
        # Must return promptly; never block on the full slow queue.
        await asyncio.wait_for(b.publish({"id": i}), timeout=0.5)
        try:
            received.append(fast.get_nowait())
        except asyncio.QueueEmpty:
            pass
    # The fast (draining) consumer received real id-bearing events.
    assert any("id" in e for e in received)
    # The slow consumer was marked dropped (resync signalled) rather than blocking.
    assert b.dropped_count >= 1


async def test_unsubscribe_removes_consumer():
    b = Broadcaster(queue_size=4)
    s = b.subscribe()
    assert b.subscriber_count == 1
    b.unsubscribe(s)
    assert b.subscriber_count == 0
    # Publishing after unsubscribe is a no-op (no error).
    await b.publish({"id": 99})
