"""Front Door — the Chief of Staff's single-door primitives (CoS Step 2).

See reports/chief-of-staff-one-front-door-spec.md §Step 2 and CAI-RESP-502.

Step 2 makes `nazim-console` the operator's canonical door: the CoS (Nazim) acks
there, relays cross-domain work to the hub, and relays the hub's answer back into
ONE thread. This module is the SAFE FOUNDATION for that — pure, additive, and
defaulting to today's behavior. It does NOT touch the live ingest/tg_out path;
arming the relay (wiring it into ROUTE + the hub's cooperating reply path) is the
next, separately-reviewed increment that needs hub coordination.

MODE (`FRONT_DOOR_MODE`):
  per-channel  -> today's per-channel thread-ownership (DEFAULT — zero change).
  unified      -> the CoS owns the whole operator conversation and relays across
                  the boundary. An unrecognized value falls back to per-channel
                  (never silently enable unified).

CAI-502 SAFETY, encoded here so the wiring can't skip it:
  - A relayed "operator said X" is NOT authority until the underlying inbound
    `operator_message` is verified. `build_relay_to_hub` marks `authority`
    'verified' ONLY when the caller passes `verified=True` AND a real
    `operator_message_id` (the durable inbound row); otherwise 'context-only'.
    Authority rides the durable row, never the relayed prose.
  - A relayed hub/cai answer ALWAYS keeps its source attribution (+ decision_ref).
    `format_relay_back` prefixes the source so the operator always knows WHO
    produced the answer (CoS voice, attributed).
"""

from __future__ import annotations

import os
from typing import Optional

MODE_PER_CHANNEL = "per-channel"
MODE_UNIFIED = "unified"
_VALID_MODES = frozenset({MODE_PER_CHANNEL, MODE_UNIFIED})

# The operator's canonical door under unified mode (ingest logs his @nazim_cto_bot
# DM as tag='nazim-console').
CANONICAL_DOOR_TAG = "nazim-console"


def mode() -> str:
    """Resolve the front-door mode from FRONT_DOOR_MODE. Default (and fallback for
    any unrecognized value) is per-channel — today's behavior, zero change."""
    raw = (os.environ.get("FRONT_DOOR_MODE") or MODE_PER_CHANNEL).strip().lower()
    return raw if raw in _VALID_MODES else MODE_PER_CHANNEL


def is_unified() -> bool:
    """True iff the single-door (unified) mode is armed."""
    return mode() == MODE_UNIFIED


def build_relay_to_hub(
    operator_message_id: Optional[int],
    text: str,
    domain: Optional[str] = None,
    *,
    verified: bool,
) -> dict:
    """Build the attributable payload for a CoS->hub dispatch of a cross-domain
    operator message.

    CAI-502: the relay is `authority='verified'` ONLY when `verified` is True AND
    a real `operator_message_id` (the durable inbound row) is present — authority
    rides the durable row, never the relayed prose. Otherwise it is
    `authority='context-only'`: the hub may act on it as CONTEXT but must not
    treat it as an operator command until it verifies the inbound itself.
    """
    is_authority = bool(verified) and operator_message_id is not None
    return {
        "kind": "cos-operator-relay",
        "operator_message_id": operator_message_id,
        "text": text,
        "domain": domain,
        "authority": "verified" if is_authority else "context-only",
        "provenance": "relayed-by-cos",
    }


def format_relay_back(
    answer: str,
    *,
    source_agent: str,
    domain: Optional[str] = None,
    decision_ref: Optional[str] = None,
) -> str:
    """Format an executor's (hub/cai/domain-agent) answer for relay back onto the
    canonical door, KEEPING its source attribution (CAI-502). The CoS speaks in
    one voice but always names who did the work, e.g. '[hub · storefront] ...' or
    '[cai · CAI-RESP-505] ...'."""
    parts = [source_agent]
    if decision_ref:
        parts.append(decision_ref)
    elif domain:
        parts.append(domain)
    tag = " · ".join(parts)
    return f"[{tag}] {answer}"
