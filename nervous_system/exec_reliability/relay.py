"""First consumer: the dispatch / notification RELAY (spec §5, CAI-464 §8).

The relay drains authorized work-items whose named_artifact is a notification and
DELIVERS the NOTICE that an authorized op exists. It is deliberately the lowest-
blast-radius consumer: worst case is a duplicate notice, never a wrong action.

The three CAI-464 (§8) hardening conditions are enforced here:

  c1  AT-LEAST-ONCE-WITH-DEDUP (not exactly-once). We CHECK the delivery ledger
      BEFORE sending and RECORD AFTER sending. The UNIQUE idempotency_key makes a
      double-record impossible; a concurrent double-send collapses to one. The
      crash-after-send-before-record window RE-DELIVERS (a rare duplicate) — it
      NEVER swallows. Bias to re-deliver, never drop.

  c2  BRIGHT LINE — delivery != execution. `build_notice()` produces only a
      human/agent-readable NOTICE describing that an authorized op exists. The
      relay has NO path to the underlying op's executor. It cannot run the op.

  c3  Explicit non-repo authority scope: enforced upstream at enqueue
      (compute_lease_scope) and re-asserted by the runner before dispatch.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ._common import post_bus, qualified

# MVP delivery channels. Unknown channel -> the runner fail-closed BOUNCEs (it is
# authority the grant did not settle), never a best-effort guess.
RELAY_CHANNELS = ("bus",)

# A sender delivers the notice and returns a provider message id. It takes the
# caller's cursor so the send participates in the same transaction where needed.
Sender = Callable[..., str]


def build_notice(item: dict[str, Any]) -> str:
    """The NOTICE text (CAI-464 c2). Describes that an authorized op EXISTS.

    This is the whole of what the relay delivers. It intentionally does not, and
    structurally cannot, execute the underlying op — it only announces it.
    """
    art = item["named_artifact"]
    grant = item["grant_ref"]
    summary = art.get("summary", "(no summary)")
    return (
        f"[exec-relay] Authorized op available under grant {grant}: {summary}\n"
        f"(This is a delivery notice only — the underlying op is NOT executed by "
        f"the relay. work_item={item['id']}, idempotency_key={item['idempotency_key']})"
    )


def default_bus_sender(
    cur, channel: str, target: str, notice: str, *, from_agent: str
) -> str:
    """Default sender: deliver the notice as an attributable bus row.

    Only the 'bus' channel is wired in the MVP. The runner has already validated
    the channel before we get here.
    """
    if channel != "bus":  # defensive; runner bounces unknown channels first
        raise ValueError(f"relay MVP does not support channel {channel!r}")
    msg_id = post_bus(
        cur,
        from_agent=from_agent,
        to_agent=target,
        subject="exec-relay: authorized op available",
        body=notice,
        message_type="update",
    )
    return f"bus:{msg_id}"


def _find_delivery(cur, schema: str, idempotency_key: str) -> Optional[dict]:
    ledger_t = qualified(schema, "exec_delivery_ledger")
    cur.execute(
        f"select id, provider_msg_id, delivered_at from {ledger_t} "
        f"where idempotency_key = %s",
        (idempotency_key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "provider_msg_id": row[1], "delivered_at": row[2]}


def _record_delivery(
    cur,
    schema: str,
    *,
    idempotency_key: str,
    work_item_id: int,
    channel: str,
    target: str,
    provider_msg_id: str,
) -> bool:
    """Write the durable delivery proof. Returns False on a concurrent duplicate.

    Split out as its own function so tests can simulate the crash-after-send-
    before-record window by making this step fail.
    """
    ledger_t = qualified(schema, "exec_delivery_ledger")
    cur.execute(
        f"""
        insert into {ledger_t}
            (idempotency_key, work_item_id, channel, target, provider_msg_id)
        values (%s, %s, %s, %s, %s)
        on conflict (idempotency_key) do nothing
        returning id
        """,
        (idempotency_key, work_item_id, channel, target, provider_msg_id),
    )
    return cur.fetchone() is not None


def deliver(
    cur,
    item: dict[str, Any],
    *,
    runner_agent_id: str,
    sender: Sender,
    schema: str = "public",
) -> dict[str, Any]:
    """Execute the relay work-item: deliver the NOTICE, exactly-once-with-dedup.

    Returns a result dict. Raises only on genuine send failure (so the runner can
    mark the item retryable) — and by construction a raise AFTER the send but
    BEFORE the ledger record leaves NO ledger row, so the retry RE-DELIVERS.
    """
    art = item["named_artifact"]
    channel = art["channel"]
    target = art["target"]
    idem = item["idempotency_key"]

    # c1: CHECK the ledger BEFORE sending. Already delivered -> dedup, no re-send.
    existing = _find_delivery(cur, schema, idem)
    if existing is not None:
        return {
            "status": "deduped",
            "provider_msg_id": existing["provider_msg_id"],
            "note": "delivery ledger hit — notice already delivered, no re-send",
        }

    # c2: SEND only the notice. This is the whole action; no op is executed.
    notice = build_notice(item)
    provider_msg_id = sender(
        cur, channel, target, notice, from_agent=runner_agent_id
    )

    # c1: RECORD after send. A crash between the two lines re-delivers on retry
    # (ledger still empty) rather than dropping. Concurrent send -> unique
    # conflict -> recorded=False, still exactly one logical delivery.
    recorded = _record_delivery(
        cur,
        schema,
        idempotency_key=idem,
        work_item_id=item["id"],
        channel=channel,
        target=target,
        provider_msg_id=provider_msg_id,
    )
    return {
        "status": "delivered",
        "provider_msg_id": provider_msg_id,
        "recorded": recorded,
        "channel": channel,
        "target": target,
    }
