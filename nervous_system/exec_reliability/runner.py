"""Runner loop (spec §3): disposable, stateless, fresh-context post-gate executor.

The runner borrows authority from a GRANT and acts under its OWN agent_id. It
NEVER decides and NEVER writes a grant/decision (execution_status stays untouched
by this whole layer). Each cycle:

  1. CLAIM one item atomically (SELECT FOR UPDATE SKIP LOCKED) — FIFO.
  2. RE-CHECK the grant is still granted / not-revoked.
  3. FAIL-CLOSED BOUNCE (EXEC-3) if execution would need any decision the grant
     did not settle — state='bounced', post to bus, ZERO side-effects.
  4. EXEC-5 (money/irreversible only): run the NAMED pre-verify gate; fail->bounce.
  5. EXECUTE the exact named_artifact (idempotent via the consumer's dedupe).
  6. RECORD done|failed + result + report on the bus (own agent_id).
  7. Lease is renewable in heartbeat; on expiry the item returns to pending
     (handled by the safety-net reaper — spec §4a).
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

import psycopg

from . import relay
from ._common import post_bus, qualified
from .schema import REVOKED_STATUS, SETTLED_EXECUTION_STATUS

DEFAULT_TTL_SECONDS = 300
DEFAULT_AGENT_ID = os.environ.get("EXEC_RUNNER_AGENT_ID", "cc-exec-runner")

# Consumer registry. An unknown consumer_type is fail-closed (bounced), never
# improvised (spec §3.3).
CONSUMERS: dict[str, Callable[..., dict]] = {
    "relay": relay.deliver,
}

# Artifact classes that require the EXEC-5 pre-verify gate before execution.
MONEY_IRREVERSIBLE_CLASSES = ("money", "irreversible")


def claim_one(
    dsn: str,
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    schema: str = "public",
) -> Optional[dict[str, Any]]:
    """Atomically claim ONE claimable item (spec §3.1).

    Claimable = state 'pending', OR a 'claimed'/'running' row whose lease has
    expired (a dead runner's item — reclaimable). SELECT FOR UPDATE SKIP LOCKED
    guarantees two concurrent runners never claim the same row. FIFO by
    created_at (operator queue doctrine).
    """
    items_t = qualified(schema, "exec_work_items")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            with claimable as (
                select id from {items_t}
                where state = 'pending'
                   or (state in ('claimed','running')
                       and lease_expires_at is not null
                       and lease_expires_at < now())
                order by created_at asc
                for update skip locked
                limit 1
            )
            update {items_t} w
               set state = 'claimed',
                   claimed_by = %s,
                   claimed_at = now(),
                   lease_expires_at = now() + make_interval(secs => %s),
                   attempts = w.attempts + 1
              from claimable c
             where w.id = c.id
            returning w.id, w.grant_ref, w.consumer_type, w.named_artifact,
                      w.idempotency_key, w.lease_scope, w.attempts, w.max_attempts,
                      w.state
            """,
            (agent_id, ttl_seconds),
        )
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None
        cols = [d[0] for d in cur.description]
        conn.commit()
        return dict(zip(cols, row))


def _grant_is_valid(cur, grant_ref: str) -> tuple[bool, str]:
    """Re-check the authorizing grant (spec §3.2). READ-ONLY on strategic_decisions."""
    cur.execute(
        "select execution_status, status from public.strategic_decisions "
        "where decision_ref = %s",
        (grant_ref,),
    )
    row = cur.fetchone()
    if not row:
        return False, f"grant {grant_ref} not found"
    execution_status, status = row
    if status in REVOKED_STATUS:
        return False, f"grant {grant_ref} revoked (status={status})"
    if execution_status not in SETTLED_EXECUTION_STATUS:
        return False, (
            f"grant {grant_ref} not settled "
            f"(execution_status={execution_status!r})"
        )
    return True, ""


def _bounce_reason(cur, item: dict[str, Any]) -> Optional[str]:
    """Return a fail-closed BOUNCE reason (EXEC-3), or None if safe to execute.

    A bounce means: executing this item would require a decision the grant did
    NOT settle. The runner refuses and hands back to the hub — it never improvises.
    """
    art = item["named_artifact"]

    # EXEC-4 / CAI-464 c3: no explicit authority scope -> refuse.
    if not item.get("lease_scope"):
        return "no explicit lease_scope (unscoped authority — CAI-464 c3)"

    # (2) grant itself must still be valid.
    ok, why = _grant_is_valid(cur, item["grant_ref"])
    if not ok:
        return why

    # (3) any extra decisions the artifact says it depends on must be settled.
    for ref in art.get("requires_settled", []):
        ok, why = _grant_is_valid(cur, ref)
        if not ok:
            return f"required decision unsettled: {why}"

    # Unknown consumer -> no authorized executor -> bounce.
    consumer_type = item["consumer_type"]
    if consumer_type not in CONSUMERS:
        return f"no registered consumer for type {consumer_type!r}"

    # Relay-specific: unknown channel is authority the grant did not settle.
    if consumer_type == "relay" and art.get("channel") not in relay.RELAY_CHANNELS:
        return f"relay channel {art.get('channel')!r} not authorized in MVP"

    # EXEC-5: money/irreversible require a NAMED pre-verify gate in the artifact.
    if art.get("class") in MONEY_IRREVERSIBLE_CLASSES and not art.get("pre_verify"):
        return (
            f"class={art.get('class')!r} requires a named pre_verify gate "
            "(EXEC-5) — none provided"
        )

    return None


def _set_state(cur, items_t: str, item_id: int, state: str, **fields) -> None:
    sets = ["state = %s"]
    params: list[Any] = [state]
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        params.append(json.dumps(v) if k in ("result", "pre_verify_result", "post_proof") else v)
    params.append(item_id)
    cur.execute(f"update {items_t} set {', '.join(sets)} where id = %s", params)


def process_item(
    dsn: str,
    item: dict[str, Any],
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    sender: Optional[relay.Sender] = None,
    schema: str = "public",
) -> dict[str, Any]:
    """Run steps 2-6 for an already-claimed item. Single transaction per outcome."""
    items_t = qualified(schema, "exec_work_items")
    if sender is None:
        sender = relay.default_bus_sender

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # 3 + 4: fail-closed gate. On bounce: state flip + bus row commit together,
        # and NOTHING else happens (no send, no ledger write) -> ZERO side-effects.
        reason = _bounce_reason(cur, item)
        if reason is not None:
            _set_state(cur, items_t, item["id"], "bounced", last_error=reason)
            post_bus(
                cur,
                from_agent=agent_id,
                to_agent="cc-orchestrator",
                subject=f"exec BOUNCE: work_item {item['id']} ({item['grant_ref']})",
                body=(
                    f"Fail-closed bounce (EXEC-3). Runner refused to execute and "
                    f"returned to hub.\nReason: {reason}\n"
                    f"Zero side-effects: no op executed, no notice delivered."
                ),
                message_type="blocker",
                priority="P1",
                requires_response=True,
            )
            conn.commit()
            return {"outcome": "bounced", "reason": reason, "item_id": item["id"]}

        # 5: EXECUTE. state -> running, then dispatch to the named consumer.
        _set_state(cur, items_t, item["id"], "running")
        handler = CONSUMERS[item["consumer_type"]]
        try:
            result = handler(
                cur, item, runner_agent_id=agent_id, sender=sender, schema=schema
            )
        except Exception as exc:  # execution failure -> bounded retry, never infinite
            attempts = item.get("attempts", 1)
            max_attempts = item.get("max_attempts", 5)
            retryable = attempts < max_attempts
            new_state = "pending" if retryable else "failed"
            _set_state(
                cur, items_t, item["id"], new_state, last_error=str(exc),
                claimed_by=None if retryable else item.get("claimed_by"),
                lease_expires_at=None if retryable else None,
            )
            post_bus(
                cur,
                from_agent=agent_id,
                to_agent="cc-orchestrator",
                subject=f"exec {'RETRY' if retryable else 'FAILED'}: work_item {item['id']}",
                body=(
                    f"Execution error on attempt {attempts}/{max_attempts}: {exc}\n"
                    + ("Re-queued to pending (retryable)." if retryable
                       else "Max attempts reached — alerting hub, NO auto-retry.")
                ),
                message_type="blocker",
                priority="P1" if not retryable else "P2",
                requires_response=not retryable,
            )
            conn.commit()
            return {
                "outcome": "retry" if retryable else "failed",
                "error": str(exc),
                "item_id": item["id"],
            }

        # 6: RECORD done + result + attributable bus report.
        _set_state(cur, items_t, item["id"], "done", result=result, last_error=None)
        post_bus(
            cur,
            from_agent=agent_id,
            to_agent="cc-orchestrator",
            subject=f"exec DONE: work_item {item['id']} ({item['grant_ref']})",
            body=f"Consumer={item['consumer_type']} result={json.dumps(result)}",
            message_type="update",
            priority="P2",
        )
        conn.commit()
        return {"outcome": "done", "result": result, "item_id": item["id"]}


def run_once(
    dsn: str,
    *,
    agent_id: str = DEFAULT_AGENT_ID,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    sender: Optional[relay.Sender] = None,
    schema: str = "public",
) -> Optional[dict[str, Any]]:
    """One full cycle: claim + process one item. Returns None if queue is empty."""
    item = claim_one(dsn, agent_id=agent_id, ttl_seconds=ttl_seconds, schema=schema)
    if item is None:
        return None
    return process_item(dsn, item, agent_id=agent_id, sender=sender, schema=schema)


if __name__ == "__main__":  # pragma: no cover - operational entrypoint (hub-wired)
    from dotenv import load_dotenv

    load_dotenv(os.path.expanduser("~/wingmen/orchestrator/.env"))
    _dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    print(run_once(_dsn))
