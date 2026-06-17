"""In-process async SSE broadcaster + bus feeder (CAI-RESP-264 condition 5).

Slow-consumer-safe by construction:
  * Each subscriber has its OWN bounded asyncio.Queue.
  * publish() NEVER blocks: if a subscriber's queue is full, its oldest event is
    dropped and a {"_resync": true} marker is enqueued so the browser re-pulls
    /api/messages — the slow consumer degrades gracefully instead of stalling
    the publisher or the other consumers.
  * A console fault (client storm, stalled EventSource) therefore cannot back-
    pressure the feeder, and the feeder lives in the *console* process — never
    in the orchestrator — so it cannot touch coordination/#111.

The feeder polls agent_messages for new ids (the console connects with a SELECT-
only role and must not depend on the orch's Realtime WS). Poll cadence is cheap
and bounded; sub-second is achieved at a low interval.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("wingmen.console.feed")

DEFAULT_QUEUE_SIZE = 64
DEFAULT_POLL_INTERVAL = 1.0  # seconds


class Broadcaster:
    """Fan-out new-message events to connected SSE clients."""

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subs: Set[asyncio.Queue] = set()
        self.dropped_count = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, event: Dict[str, Any]) -> None:
        """Deliver *event* to every subscriber without ever blocking.

        Full queue -> drop oldest, enqueue a resync marker. Returns promptly.
        """
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped_count += 1
                # Drop the oldest, signal the client to resync from the API.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait({"_resync": True})
                except asyncio.QueueFull:
                    # Still full after a drain: leave it; client will catch up.
                    pass


async def feeder(
    broadcaster: Broadcaster,
    fetch_since,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Poll for new bus rows and publish them. Runs in the CONSOLE process only.

    *fetch_since(last_id)* is an async-or-sync callable returning a list of new
    message rows (id-ascending) with id > last_id. Errors are logged and the
    loop continues — the feeder must be crash-tolerant.
    """
    last_id = 0
    # Establish a baseline so we don't replay history on boot.
    try:
        baseline = await _maybe_await(fetch_since(None))
        if baseline:
            last_id = max(r["id"] for r in baseline)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("feeder baseline failed: %s", e)

    while not (stop_event and stop_event.is_set()):
        try:
            rows: List[dict] = await _maybe_await(fetch_since(last_id))
            for row in rows:
                last_id = max(last_id, row["id"])
                await broadcaster.publish(row)
        except Exception as e:
            logger.warning("feeder poll failed: %s", e)
        try:
            if stop_event:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            else:
                await asyncio.sleep(poll_interval)
        except asyncio.TimeoutError:
            pass


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
