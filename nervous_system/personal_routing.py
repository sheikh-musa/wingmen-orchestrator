"""Routes PRIVATE-FAMILY channel content OUT of the shared substrate and into
wingmen-personal (brrgastulcffamlbggyu), per orch-console's approved design
(bus #47837, conditions C1-C4).

TENANT-RESIDENCY-001: Zahidah's/Musa's family coursework content must land
only in its own designated store (docs/data-store-registry.md already names
wingmen-personal for mamadah), never the shared orchestrator substrate. The
substrate keeps only a content-free ENVELOPE row (id/direction/chat_id/tag/
timestamp + a fixed sentinel in place of `text`) so the existing doorbell/
cursor machinery (operator_messages.unprocessed()/mark_handled(), poll_offset)
keeps working unchanged. Real text + media refs live in wingmen-personal's
`mamadah_messages` table, keyed by the substrate envelope row's id
(substrate_message_id) — the correlation key, not a new substrate column.

C3: the wingmen-personal credential (WINGMEN_PERSONAL_URL /
WINGMEN_PERSONAL_SERVICE_KEY) is intentionally NOT read via load_dotenv() on
the shared orchestrator .env (every lane's launch_dangerous_cc.sh does
`set -a; . .env; set +a`, which would export it fleet-wide). It is read only
from os.environ, populated only by the two call sites that source the
GUARDED ~/.wingmen/private/wingmen_personal.env (WINGMEN_PERSONAL_ALLOWED
sentinel, same shape as write_dsn.env/CAI-1225): scripts/boot_nazim_ingest.sh
(inbound) and scripts/mamadah_send.sh (outbound).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

# Tags whose message CONTENT must never land in the substrate. A tag not in
# this set is untouched by every function below (byte-identical old behavior).
PERSONAL_ROUTED_TAGS = frozenset({"mamadah"})

SENTINEL_TEXT = "[[routed:wingmen-personal]]"

# Fixed, non-text-derived triage payload for a personal-routed tag: the
# channel tag ALONE already fixes the domain deterministically (see
# nervous_system.triage._CHANNEL_TAGS), so running classify() over her actual
# words is unnecessary — and classify()'s governance-fork branch would quote
# a matched phrase from the text into cos_triage (bus #47837 C2: "cos_triage
# (rationale computed from text)" is a named leak path). This skips that
# branch entirely for a personal-routed tag.
SENTINEL_COS_TRIAGE = json.dumps({
    "category": "delegate_domain_agent",
    "route": "mamadah",
    "confidence": 0.95,
    "domain": "second-brain",
    "rationale": "personal-routed channel — triage skipped by design (no text analysis on this tag)",
})


class PersonalRouteError(RuntimeError):
    """Raised when the wingmen-personal write fails. Callers MUST roll back
    the substrate envelope and must NOT advance the channel's poll offset
    (bus #47837 C1) — Telegram redelivers, so nothing is lost."""


def is_personal_routed(tag: str | None) -> bool:
    # Normalized compare (cc-quality PR #240 LOW): a leak gate must fail
    # SAFE on a variant, not fail open — strip/lower so ' Mamadah '/'MAMADAH'
    # still route, rather than silently falling through to the substrate.
    # The DB's channel_tag is deterministic ('mamadah'), so this never changes
    # behavior for the real system; it only removes a theoretical gap.
    if not tag:
        return False
    return tag.strip().lower() in PERSONAL_ROUTED_TAGS


def _rest_config() -> tuple[str, str]:
    url = os.environ.get("WINGMEN_PERSONAL_URL")
    key = os.environ.get("WINGMEN_PERSONAL_SERVICE_KEY")
    if not url or not key:
        raise PersonalRouteError(
            "WINGMEN_PERSONAL_URL/WINGMEN_PERSONAL_SERVICE_KEY not set — source the "
            "guarded ~/.wingmen/private/wingmen_personal.env (WINGMEN_PERSONAL_ALLOWED=1) "
            "before calling write_personal_content()."
        )
    return url, key


def write_personal_content(
    substrate_message_id: int,
    *,
    direction: str,
    channel: str,
    tag: str,
    text: str,
    chat_id=None,
    media_refs=None,
    from_user_id=None,
    from_username=None,
    from_name=None,
    tg_message_id: int | None = None,
) -> int:
    """Writes the REAL content row to wingmen-personal via its PostgREST API
    (service_role key — bypasses RLS; the table has no policies, so this is
    the only writer), keyed by the substrate envelope's id. Raises
    PersonalRouteError on any failure — never best-effort, this write is as
    load-bearing as the envelope row itself (bus #47837 C1)."""
    url, key = _rest_config()
    payload = {
        "substrate_message_id": substrate_message_id,
        "direction": direction,
        "channel": channel,
        "chat_id": str(chat_id) if chat_id is not None else None,
        "tag": tag,
        "text": text,
        "media_refs": media_refs,
        "from_user_id": from_user_id,
        "from_username": from_username,
        "from_name": from_name,
        "tg_message_id": tg_message_id,
    }
    req = urllib.request.Request(
        f"{url}/rest/v1/mamadah_messages",
        data=json.dumps(payload).encode(),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        # Idempotent success, not a retriable failure (bus #49740/#49763): a
        # duplicate-key 23505 on (channel, chat_id, tg_message_id) means this
        # exact message was already safely written in an earlier attempt —
        # most often a redelivered Telegram update_id for the same inner
        # message. Treating it as a failure rolls back the caller's substrate
        # envelope every cycle (ingest.py's C1 rollback-on-raise) and never
        # advances the poll offset, so Telegram keeps redelivering the same
        # update forever — the content is fine, only the envelope is stuck.
        if e.code == 409 and "23505" in body and tg_message_id is not None:
            existing_id = _find_existing_personal_content(
                url, key, channel=channel, chat_id=chat_id, tg_message_id=tg_message_id,
            )
            if existing_id is not None:
                return existing_id
        raise PersonalRouteError(f"wingmen-personal insert failed: {e.code} {body[:300]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PersonalRouteError(f"wingmen-personal insert failed: {type(e).__name__}: {e}") from e
    if not rows:
        raise PersonalRouteError("wingmen-personal insert returned no row")
    return rows[0]["id"]


def _find_existing_personal_content(
    url: str, key: str, *, channel: str, chat_id, tg_message_id: int,
) -> int | None:
    """Looks up the row that already owns (channel, chat_id, tg_message_id)
    after a duplicate-key insert — the row the dup-key error proves exists.
    Returns None (never raises) on any lookup failure, so the caller falls
    back to the original PersonalRouteError instead of masking a genuinely
    different problem as idempotent success."""
    chat_id_str = str(chat_id) if chat_id is not None else None
    params = (
        f"channel=eq.{urllib.parse.quote(str(channel))}"
        f"&tg_message_id=eq.{tg_message_id}"
        f"&select=id"
    )
    if chat_id_str is not None:
        params += f"&chat_id=eq.{urllib.parse.quote(chat_id_str)}"
    else:
        params += "&chat_id=is.null"
    req = urllib.request.Request(
        f"{url}/rest/v1/mamadah_messages?{params}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 — lookup is a best-effort confirmation, not the source of truth
        return None
    if len(rows) == 1:
        return rows[0]["id"]
    return None


def read_personal_content(substrate_message_ids: list[int]) -> dict[int, dict]:
    """Fetches the real content rows for a batch of substrate envelope ids
    (used by scripts/mamadah_reconcile.py — never by the hub/console).
    Returns {substrate_message_id: row}."""
    if not substrate_message_ids:
        return {}
    url, key = _rest_config()
    ids_csv = ",".join(str(i) for i in substrate_message_ids)
    req = urllib.request.Request(
        f"{url}/rest/v1/mamadah_messages?substrate_message_id=in.({ids_csv})",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read().decode())
    return {r["substrate_message_id"]: r for r in rows}
