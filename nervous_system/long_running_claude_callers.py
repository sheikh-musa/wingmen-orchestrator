"""long_running_claude_callers — registry helper per CAI-RESP-161 Phase A.

Public API:
  - register(supabase, ...) — async; insert or refresh a caller's registry row
  - heartbeat(supabase, caller_name) — async; update last_seen_at
  - revoke(supabase, caller_name, reason) — async; soft-delete + notification_log audit
  - parse_manifest(path) — sync; parse YAML/JSON to Manifest dataclass
  - sweep_manifests(dir) — sync; read all *.yaml/*.json under dir
  - derive_auto_kill_policy(identity) — sync; default policy lookup

Per CAI-RESP-160: callers MUST register on start if they meet long-running criteria
(sessions_24h > 50 OR cadence-bounded; threshold bound to CAI-RESP-157 [A]).

Phase B (separate PR) wires CAI-RESP-157 [B] watchdog-kill to consult this registry.
Phase A is visibility-only.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("wingmen.long_running_claude_callers")


_VALID_IDENTITIES = {"operator", "cc_family", "substrate"}
_VALID_KILL_POLICIES = {"soft_alert", "hard_kill", "no_kill"}
_IDENTITY_TO_DEFAULT_POLICY = {
    "operator": "soft_alert",
    "cc_family": "soft_alert",
    "substrate": "no_kill",
}

_REQUIRED_MANIFEST_FIELDS = (
    "caller_name", "cmd", "expected_cadence_seconds", "expected_tokens_per_day",
    "ratified_by_decision_ref", "registered_by_identity", "purpose",
)

# Notification-log source-enum values per CAI-RESP-161 Q7. These are referenced
# by Phase B watchdog wire-in (separate PR). Phase A uses CALLER_REGISTERED and
# CALLER_REVOKED only; the others are forward-compat for Phase B.
NOTIFICATION_SOURCE_WATCHDOG_HARD_KILL = "watchdog_hard_kill"
NOTIFICATION_SOURCE_WATCHDOG_SOFT_ALERT = "watchdog_soft_alert"
NOTIFICATION_SOURCE_CALLER_SELF_KILL = "caller_self_kill"
NOTIFICATION_SOURCE_CALLER_REGISTERED = "caller_registered"
NOTIFICATION_SOURCE_CALLER_REVOKED = "caller_revoked"


@dataclass(frozen=True)
class Manifest:
    caller_name: str
    cmd: str
    expected_cadence_seconds: int
    expected_tokens_per_day: int
    ratified_by_decision_ref: str
    registered_by_identity: str
    purpose: str
    max_tokens_per_day: Optional[int] = None
    auto_kill_policy: Optional[str] = None  # None → derived from identity default


def derive_auto_kill_policy(registered_by_identity: str) -> str:
    """Per CAI-RESP-161 Q6: identity-derived default."""
    if registered_by_identity not in _VALID_IDENTITIES:
        raise ValueError(
            f"invalid registered_by_identity={registered_by_identity!r}; "
            f"expected one of {sorted(_VALID_IDENTITIES)}"
        )
    return _IDENTITY_TO_DEFAULT_POLICY[registered_by_identity]


def parse_manifest(path: Path) -> Manifest:
    """Parse a YAML or JSON manifest file. Raises on missing/invalid fields."""
    path = Path(path)
    text = path.read_text()
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"manifest at {path} must be a YAML/JSON object, got {type(data).__name__}")
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in data]
    if missing:
        raise KeyError(f"manifest at {path} missing required fields: {missing}")
    if data["registered_by_identity"] not in _VALID_IDENTITIES:
        raise ValueError(
            f"manifest at {path}: invalid registered_by_identity={data['registered_by_identity']!r}; "
            f"expected one of {sorted(_VALID_IDENTITIES)}"
        )
    if data.get("auto_kill_policy") is not None and data["auto_kill_policy"] not in _VALID_KILL_POLICIES:
        raise ValueError(
            f"manifest at {path}: invalid auto_kill_policy={data['auto_kill_policy']!r}; "
            f"expected one of {sorted(_VALID_KILL_POLICIES)}"
        )
    return Manifest(
        caller_name=data["caller_name"],
        cmd=data["cmd"],
        expected_cadence_seconds=data["expected_cadence_seconds"],
        expected_tokens_per_day=data["expected_tokens_per_day"],
        ratified_by_decision_ref=data["ratified_by_decision_ref"],
        registered_by_identity=data["registered_by_identity"],
        purpose=data["purpose"],
        max_tokens_per_day=data.get("max_tokens_per_day"),
        auto_kill_policy=data.get("auto_kill_policy"),
    )


def sweep_manifests(manifests_dir: Path | str | None = None) -> list[Manifest]:
    """Read all *.yaml/*.json files under the manifests dir; parse each.

    Returns list of Manifests. Skips files that fail to parse (logs warning).
    Default dir: <repo-root>/manifests/long_running_callers/.
    """
    if manifests_dir is None:
        manifests_dir = Path(__file__).parent.parent / "manifests" / "long_running_callers"
    manifests_dir = Path(manifests_dir)
    results: list[Manifest] = []
    if not manifests_dir.exists():
        return results
    for path in sorted(manifests_dir.iterdir()):
        if path.suffix.lower() not in (".yaml", ".yml", ".json"):
            continue
        if path.name.startswith("."):
            continue
        try:
            results.append(parse_manifest(path))
        except Exception as e:
            logger.warning(f"failed to parse manifest {path}: {e}")
    return results


async def register(
    supabase,
    *,
    caller_name: str,
    cmd: str,
    expected_cadence_seconds: int,
    expected_tokens_per_day: int,
    ratified_by_decision_ref: str,
    registered_by_identity: str,
    purpose: str,
    parent_pid: Optional[int] = None,
    max_tokens_per_day: Optional[int] = None,
    auto_kill_policy: Optional[str] = None,
    operator_authored: Optional[bool] = None,
) -> None:
    """Register (or refresh) a caller in long_running_claude_callers.

    Upsert on caller_name. last_seen_at + cmd + parent_pid refreshed each call.
    """
    if registered_by_identity not in _VALID_IDENTITIES:
        raise ValueError(f"invalid registered_by_identity={registered_by_identity!r}")
    if auto_kill_policy is None:
        auto_kill_policy = derive_auto_kill_policy(registered_by_identity)
    if auto_kill_policy not in _VALID_KILL_POLICIES:
        raise ValueError(f"invalid auto_kill_policy={auto_kill_policy!r}")
    if operator_authored is None:
        operator_authored = (registered_by_identity == "operator")
    now = datetime.now(timezone.utc).isoformat()
    await supabase.table("long_running_claude_callers").upsert({
        "caller_name": caller_name,
        "cmd": cmd,
        "parent_pid": parent_pid,
        "started_at": now,
        "expected_cadence_seconds": expected_cadence_seconds,
        "expected_tokens_per_day": expected_tokens_per_day,
        "max_tokens_per_day": max_tokens_per_day,
        "ratified_by_decision_ref": ratified_by_decision_ref,
        "registered_by_identity": registered_by_identity,
        "auto_kill_policy": auto_kill_policy,
        "purpose": purpose,
        "operator_authored": operator_authored,
        "last_seen_at": now,
    }).execute()
    logger.info(
        f"long_running_caller: registered {caller_name} "
        f"(identity={registered_by_identity}, policy={auto_kill_policy})"
    )


async def heartbeat(supabase, caller_name: str) -> None:
    """Refresh last_seen_at for an existing caller. No-op if caller not registered."""
    now = datetime.now(timezone.utc).isoformat()
    result = await (
        supabase.table("long_running_claude_callers")
        .update({"last_seen_at": now})
        .eq("caller_name", caller_name)
        .execute()
    )
    if not result.data:
        logger.warning(
            f"long_running_caller: heartbeat for unregistered caller {caller_name!r} "
            f"— call register() first"
        )


async def revoke(supabase, caller_name: str, reason: str) -> None:
    """Mark a caller as revoked (soft-delete via revoked_at).

    Audit row written to notification_log per CAI-RESP-161 Q7.
    """
    now = datetime.now(timezone.utc).isoformat()
    await (
        supabase.table("long_running_claude_callers")
        .update({"revoked_at": now})
        .eq("caller_name", caller_name)
        .execute()
    )
    await supabase.table("notification_log").insert({
        "source": NOTIFICATION_SOURCE_CALLER_REVOKED,
        "decision_ref": "CC-LONG-CALLER-REGISTRY-001",
        "channel": "long_running_callers",
        "recipient": caller_name,
        "message_text": json.dumps({"reason": reason, "revoked_at": now}),
    }).execute()
    logger.info(f"long_running_caller: revoked {caller_name} (reason={reason})")
