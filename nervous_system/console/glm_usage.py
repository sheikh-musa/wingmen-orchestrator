"""GLM Coding Plan (z.ai) quota usage for the fleet console (op#24597).

Shown next to the Claude Max pool cards: the 5-hour window and the weekly window
(used / cap / % / reset) plus the plan level, read from z.ai's own quota endpoint
(the one zai-org/zai-coding-plugins `glm-plan-usage` calls):

    GET https://api.z.ai/api/monitor/usage/quota/limit
    Authorization: <raw key>            (no "Bearer")

Each `data.limits[]` row: `usage` = the CAP, `currentValue` = USED, `remaining`,
`percentage`, `nextResetTime` (epoch ms), `unit`/`number` = window length
(unit 3 = hours -> number 5 = the 5h window; unit 6 = week -> number 1 = weekly).

SECRET HANDLING — the key is read from the fleet vault (`GLM_CODING_KEY`) once per
process and held ONLY in this module's memory. It is never logged, never put in
argv, never returned in a payload, and never interpolated into an error message:
failure paths log the exception CLASS name only (plus an HTTP status code).

LEAK FLAG — the vault refuses nothing itself; a leak-flagged secret is returned with
`leak_flagged=True` and this module fails CLOSED on it, EXCEPT for a name listed in
ACCEPTED_LEAK_FLAG: an explicit, attributable, code-reviewed operator risk acceptance
(never env-driven, never generic). The vault row is NOT touched — its design forbids
clearing a leak flag without rotating — and the card shows a muted `key_warning` line
for as long as the accepted-but-flagged key is in use, so the risk stays visible.

HONESTY — a successful read is cached for CACHE_TTL_S (page loads must not hammer
z.ai or the vault). When a refresh FAILS the card reads "unavailable": stale
numbers are never presented as current and nothing is ever guessed.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional

logger = logging.getLogger("wingmen.console.glm_usage")

QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
VAULT_NAME = "GLM_CODING_KEY"
CACHE_TTL_S = 300          # a good read is reused for 5 min
FAIL_TTL_S = 60            # a failure is re-tried at most once a minute
HTTP_TIMEOUT_S = 5.0
_CONSOLE_AGENT_ID = "fleet-console"   # vault audit identity when the process has none

# Operator-accepted risk: secret name -> attribution. Name-scoped and code-reviewed on
# purpose — adding a name here is a deliberate, reviewable act, not configuration.
# GLM_CODING_KEY was leak-flagged because it was pasted in a Telegram DM (op#24293);
# Musa (owner of the z.ai account) decided to keep using it: "bruh just use it"
# (operator_messages op#24626, 2026-10-01). Remove the entry once the key is rotated.
ACCEPTED_LEAK_FLAG: Mapping[str, str] = MappingProxyType({
    "GLM_CODING_KEY": "Musa op#24626 (2026-10-01): operator accepted continued use of the leak-flagged key",
})
ACCEPTED_LEAK_WARNING = "key leak-flagged — use accepted by Musa (op#24626); rotate when convenient"

# unit code -> (short label, seconds per unit). Only codes verified against the
# live response are mapped; anything else is shown with a neutral label.
_UNITS = {3: ("h", 3600), 6: ("wk", 7 * 86400)}

_lock = threading.Lock()
_key: Optional[str] = None                 # in-memory only — NEVER log / return
_cache: Optional[Dict[str, Any]] = None    # last result (good or unavailable)
_cache_at: float = 0.0
_key_warning: Optional[str] = None         # set when an accepted leak-flagged key is in use


class GlmUsageError(RuntimeError):
    """A failure whose message is safe to log (never contains the key)."""


def _read_key_from_vault() -> str:
    """Fetch GLM_CODING_KEY from the fleet vault.

    The vault reads DATABASE_URL + an identity (AGENT_ID / CC_BASE_AGENT_ID) from
    os.environ. The console is not an agent and the inherited DATABASE_URL can be a
    stale pre-rotation value, so for the duration of this one call we pin the DSN
    FILE-FIRST (scripts/lib/substrate_dsn — op#24342) and supply a console identity
    when none is set, then restore the environment exactly. (vault.get() has no DSN
    parameter — vault._connect() reads os.environ — so the env pin stays until the
    vault API grows one.)

    A leak-flagged secret is refused unless its name is in ACCEPTED_LEAK_FLAG, in
    which case it is used and `_key_warning` is set for the card."""
    global _key_warning
    from nervous_system.vault import vault
    from scripts.lib.substrate_dsn import dsn_from_env_file

    dsn = dsn_from_env_file()
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "AGENT_ID")}
    try:
        os.environ["DATABASE_URL"] = dsn
        if not (os.environ.get("CC_BASE_AGENT_ID") or os.environ.get("AGENT_ID")):
            os.environ["AGENT_ID"] = _CONSOLE_AGENT_ID
        secret = vault.get(VAULT_NAME, reason="fleet console GLM usage card (op#24597)")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    if not secret.value:
        raise GlmUsageError("vault returned an empty GLM_CODING_KEY")
    if secret.leak_flagged:
        accepted = ACCEPTED_LEAK_FLAG.get(VAULT_NAME)
        if not accepted:
            raise GlmUsageError("vault marks GLM_CODING_KEY leak-flagged; not using it")
        logger.warning("glm usage: using leak-flagged %s under operator-accepted risk (%s)",
                       VAULT_NAME, accepted)
        _key_warning = ACCEPTED_LEAK_WARNING
    else:
        _key_warning = None
    return secret.value


# Indirection points so tests can inject a fake key / a mocked HTTP call.
read_key: Callable[[], str] = _read_key_from_vault


def _http_get_json(url: str, key: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": key,
                                               "Accept": "application/json",
                                               "Accept-Language": "en-US,en"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # str(e) carries only code + reason, but keep the message minimal anyway.
        raise GlmUsageError(f"z.ai quota HTTP {e.code}") from None
    except Exception as e:  # noqa: BLE001 — network/JSON: class name only, never args
        raise GlmUsageError(f"z.ai quota fetch failed ({type(e).__name__})") from None


http_get_json: Callable[[str, str], Dict[str, Any]] = _http_get_json


def _iso_from_ms(ms: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_quota(body: Dict[str, Any]) -> Dict[str, Any]:
    """z.ai quota JSON -> the card payload. Raises GlmUsageError on anything that
    is not a well-formed success (so the card says "unavailable", never a guess)."""
    if not isinstance(body, dict) or body.get("code") != 200 or not isinstance(body.get("data"), dict):
        raise GlmUsageError("z.ai quota response not a success")
    data = body["data"]
    windows: List[Dict[str, Any]] = []
    for lim in data.get("limits") or []:
        if not isinstance(lim, dict):
            continue
        cap, used = _num(lim.get("usage")), _num(lim.get("currentValue"))
        if cap is None or used is None or cap <= 0:
            continue
        unit, number = lim.get("unit"), lim.get("number")
        short, unit_s = _UNITS.get(unit, (None, None))
        if short == "wk" and number == 1:
            label = "wk"
        elif short == "h" and isinstance(number, int):
            label = f"{number}h"
        else:
            label = "lim"
        remaining = _num(lim.get("remaining"))
        windows.append({
            "label": label,
            "type": str(lim.get("type") or ""),
            "used": int(used),
            "cap": int(cap),
            "remaining": int(remaining) if remaining is not None else int(max(cap - used, 0)),
            "pct": round(used / cap * 100.0, 1),
            "resets_at": _iso_from_ms(lim.get("nextResetTime")),
            "_len_s": (unit_s * number) if (unit_s and isinstance(number, int)) else 10 ** 9,
        })
    if not windows:
        raise GlmUsageError("z.ai quota response had no usable limits")
    windows.sort(key=lambda w: w.pop("_len_s"), reverse=True)   # wk before 5h (Musa op#25348, matches the Max-pool card template)
    return {"available": True, "level": str(data.get("level") or ""), "windows": windows}


def _unavailable() -> Dict[str, Any]:
    return {"available": False, "level": None, "windows": []}


def _fetch() -> Dict[str, Any]:
    global _key, _key_warning
    if _key is None:
        _key_warning = None   # re-derived by the vault read for the key now in use
        _key = read_key()
    try:
        body = http_get_json(QUOTA_URL, _key)
    except GlmUsageError as e:
        if "HTTP 401" in str(e) or "HTTP 403" in str(e):
            _key = None   # rotated/revoked key: re-read from the vault next refresh
        raise
    out = parse_quota(body)
    out["key_warning"] = _key_warning
    return out


def get_glm_usage(now: Optional[float] = None) -> Dict[str, Any]:
    """The cached card payload. Never raises; never returns the key."""
    global _cache, _cache_at
    now = time.time() if now is None else now
    with _lock:
        if _cache is not None:
            ttl = CACHE_TTL_S if _cache.get("available") else FAIL_TTL_S
            if now - _cache_at < ttl:
                return _with_age(_cache, now)
        try:
            result = _fetch()
            result["fetched_at"] = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        except GlmUsageError as e:
            logger.warning("glm usage unavailable: %s: %s", type(e).__name__, e)
            result = _unavailable()
        except Exception as e:  # noqa: BLE001 — vault/DB errors: class name only
            logger.warning("glm usage unavailable: %s", type(e).__name__)
            result = _unavailable()
        _cache, _cache_at = result, now
        return _with_age(result, now)


def _with_age(result: Dict[str, Any], now: float) -> Dict[str, Any]:
    out = dict(result)
    out["windows"] = [dict(w) for w in result.get("windows") or []]
    out["age_s"] = int(now - _cache_at) if result.get("available") else None
    return out


def _reset_for_tests() -> None:
    global _key, _cache, _cache_at, _key_warning
    with _lock:
        _key, _cache, _cache_at, _key_warning = None, None, 0.0, None
