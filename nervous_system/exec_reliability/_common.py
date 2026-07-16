"""Shared helpers for the Execution-Reliability Layer.

Kept tiny + dependency-light so the runner stays disposable/stateless.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Sequence

# Bus sub_tag suffix. agent_messages enforces sub_tag = from_agent||'-'||<suffix>
# (family-prefix CHECK), so the thread tag is always built from the runner's OWN
# agent_id — attributable by construction (spec §3.6).
BUS_THREAD_SUFFIX = "exec-reliability"


def _thread_tag(from_agent: str) -> str:
    return f"{from_agent}-{BUS_THREAD_SUFFIX}"


def qualified(schema: str, table: str) -> str:
    """Return a schema-qualified identifier (schema is trusted-internal only)."""
    return f"{schema}.{table}"


def make_idempotency_key(grant_ref: str, consumer_type: str, artifact: dict) -> str:
    """Deterministic dedupe key (EXEC-2).

    Same grant + same artifact -> same key, so re-running enqueue is a no-op and
    the relay collapses retries onto one delivery-ledger row.
    """
    payload = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{grant_ref}:{consumer_type}:{digest}"


def post_bus(
    cur,
    *,
    from_agent: str,
    to_agent: Optional[str],
    subject: str,
    body: str,
    message_type: str = "update",
    priority: str = "P2",
    requires_response: bool = False,
    is_test: bool = False,
) -> int:
    """Insert an attributable bus row and return its id.

    Uses the caller's cursor/transaction so a bounce's bus row commits atomically
    with the state flip (EXEC-3: the bus record and the terminal state are one
    unit; there is no window where the item is bounced but silent).
    """
    cur.execute(
        """
        insert into agent_messages
          (from_agent, to_agent, message_type, subject, body,
           requires_response, priority, sub_tag, is_test)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            from_agent,
            to_agent,
            message_type,
            subject,
            body,
            requires_response,
            priority,
            _thread_tag(from_agent),
            is_test,
        ),
    )
    return cur.fetchone()[0]


def compute_lease_scope(
    repos_affected: Optional[Sequence[str]], named_artifact: dict
) -> list[str]:
    """Explicit authority scope (EXEC-4 + CAI-464 c3): NEVER default to unscoped.

    Repo work -> the grant's repos_affected. Non-repo work (e.g. a notification
    relay) -> an explicit channel scope token derived from the artifact. If we
    cannot name a scope, we return [] and the enqueue path refuses the row.
    """
    if repos_affected:
        return [f"repo:{r}" for r in repos_affected if r]
    channel = named_artifact.get("channel")
    target = named_artifact.get("target")
    if channel and target:
        return [f"channel:{channel}:{target}"]
    return []
