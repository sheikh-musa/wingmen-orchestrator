"""Stateless stuck-message classification for the durable acked inbox
(Tier-1(b) janky-tools audit, op#33149; migrations 117/118).

No persisted state of its own: every call re-reads agent_messages directly, so
there's nothing here that can itself go silently stale the way the audit's
watchdog-needs-its-own-dead-man's-switch pattern describes
(reports/janky-tools-audit-20261023/INVENTORY.md item #4).
"""

import datetime as dt


def classify(delivered_at, acked_at, lease_expires_at, now):
    """PENDING: never attempted. WAITING: delivered, ack not due yet.
    OK: acked (regardless of when). STUCK: delivered, not acked, lease expired.

    The lease is a detection deadline for the reconciler, not a correctness
    deadline for the message -- a late ack after lease expiry still counts
    as OK, not STUCK.
    """
    if delivered_at is None:
        return "PENDING"
    if acked_at is not None:
        return "OK"
    if lease_expires_at is not None and now > lease_expires_at:
        return "STUCK"
    return "WAITING"


def find_stuck_messages(conn, now=None):
    """Query agent_messages directly and return the ids currently STUCK.

    Pure read, idempotent by construction -- calling it twice in a row with
    no state change in between returns the same set.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, delivered_at, acked_at, lease_expires_at FROM agent_messages"
        )
        rows = cur.fetchall()

    stuck = []
    for row_id, delivered_at, acked_at, lease_expires_at in rows:
        if classify(delivered_at, acked_at, lease_expires_at, now) == "STUCK":
            stuck.append(row_id)
    return stuck
