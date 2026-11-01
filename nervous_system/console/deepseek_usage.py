"""DeepSeek pool card for the fleet console (op#35897, Musa bus #83369/#83385).

Shown next to the Max-pool / GLM cards: a live BALANCE (never "spend" --
DeepSeek's own balance endpoint has no spend figure, op#35703/orch-console
#83385 condition 2) per DISTINCT vault key in scripts/lib/deepseek_key_map.json
(op#35814: per-client keys, one row per client/family group that shares a key),
plus its change since the last poll:

    GET https://api.deepseek.com/user/balance
    Authorization: Bearer <raw key>

Confirmed against DeepSeek's own docs (api-docs.deepseek.com/api/get-user-balance,
orch-console #83385 condition 1): {is_available, balance_infos: [{currency,
total_balance, granted_balance, topped_up_balance}]}, decimal strings. Same shape
scripts/deepseek_balance_poll.py already reads successfully in production.

HONESTY (orch-console #83369/#83385): a key that fails to read -- missing from
the vault (the common case tonight: the 5 per-client names are mapped in
deepseek_key_map.json but not yet provisioned, confirmed live 2026-10-29) or a
network/HTTP error -- reports available=False / "could not read", NEVER 0 and
NEVER the last-known balance re-served as current. The delta-since-last-poll
baseline is a single persisted {key_name: {balance_usd, at}} JSON file (not a
history log -- the card needs "what changed since last time", not a trend), so
a console restart (routine after every deploy_console.sh run) doesn't lose the
baseline and show a misleading "first ever read" on every redeploy.

SECRET HANDLING -- mirrors glm_usage.py: each PER-CLIENT key is read from the
fleet vault once per process (file-first DSN pin, same op#24342 pattern), held
only in this module's memory for the one HTTP call, never logged, never put in
argv, never returned in any payload, and never interpolated into an error
message (failure paths log the exception CLASS name / HTTP status only).

SHARED KEY (revised 2026-10-30, orch-console #83560): DEEPSEEK_CODING_KEY is
vault leak_flagged (set 2026-10-29). vault.py's own contract is that the
caller decides whether to proceed on a leak-flagged key; this module decided:
refuse (_read_key_from_vault below). Rather than hold/call a leak-flagged key
from the console server, the shared row reads scripts/deepseek_balance_poll.py's
own hourly history file instead -- that poller (cc-fleet-health) already reads
this key successfully every hour. No vault call, no HTTP call, no secret in
this process for the shared row -- see get_shared_key_usage().
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("wingmen.console.deepseek_usage")

BALANCE_URL = "https://api.deepseek.com/user/balance"
_KEY_MAP_FILE = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib" / "deepseek_key_map.json"
_ORCH_DIR = Path(__file__).resolve().parent.parent.parent
_STATE_PATH = _ORCH_DIR / "state" / "deepseek_card_balance_history.json"

CACHE_TTL_S = 300          # a good read is reused for 5 min (same as GLM's card)
FAIL_TTL_S = 60            # a failure is re-tried at most once a minute
HTTP_TIMEOUT_S = 5.0
_CONSOLE_AGENT_ID = "fleet-console"

# deepseek-flash is the only model confirmed live tonight (op#35897); api-docs.
# deepseek.com's pricing page names it DeepSeek-V4.1-Flash-1017. Unmapped model
# ids fall back to the raw string -- never a guessed display name.
MODEL_LABELS: Dict[str, str] = {
    "deepseek-flash": "DeepSeek-V4.1-Flash-1017",
}


class DeepseekUsageError(RuntimeError):
    """A failure whose message is safe to log (never contains a key)."""


class DeepseekKeyNotProvisioned(DeepseekUsageError):
    """The vault has no secret under this name yet -- op#35814 mapped the per-
    client key NAMES in deepseek_key_map.json before provisioning the actual
    secrets (orch-console #83397, confirmed live 2026-10-29: SecretNotFoundError
    on all 5). A DIFFERENT, more honest state than a key that exists but fails
    to read -- the card says "key not created yet", never "could not read"."""


# DEEPSEEK_CODING_KEY: the pre-op#35814 shared key. NOT in deepseek_key_map.json
# (it's the thing the per-client keys replace), but today it's where every
# lane's actual spend lands (orch-console #83397) -- shown as its own row.
SHARED_KEY_NAME = "DEEPSEEK_CODING_KEY"


def model_label(model: Optional[str]) -> Optional[str]:
    if not model:
        return model
    return MODEL_LABELS.get(model, model)


def _key_map() -> Dict[str, str]:
    with open(_KEY_MAP_FILE) as f:
        return json.load(f)


def distinct_key_rows() -> List[Dict[str, Any]]:
    """One row per DISTINCT vault key name, families that share a key grouped
    onto it (deepseek_key_map.json is the single source -- never re-derived)."""
    by_key: Dict[str, List[str]] = {}
    for family, key_name in _key_map().items():
        by_key.setdefault(key_name, []).append(family)
    return [{"key_name": k, "families": sorted(v)} for k, v in sorted(by_key.items())]


def _read_key_from_vault(name: str) -> str:
    """Fetch `name` from the fleet vault. Mirrors glm_usage._read_key_from_vault's
    file-first DSN pin (op#24342) so this works from any process regardless of
    what DATABASE_URL it inherited."""
    from nervous_system.vault import SecretNotFoundError, vault
    from scripts.lib.substrate_dsn import dsn_from_env_file

    dsn = dsn_from_env_file()
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "AGENT_ID")}
    try:
        os.environ["DATABASE_URL"] = dsn
        if not (os.environ.get("CC_BASE_AGENT_ID") or os.environ.get("AGENT_ID")):
            os.environ["AGENT_ID"] = _CONSOLE_AGENT_ID
        secret = vault.get(name, reason="fleet console DeepSeek balance card (op#35897)")
    except SecretNotFoundError:
        raise DeepseekKeyNotProvisioned(f"vault has no secret named {name}") from None
    except Exception as e:  # noqa: BLE001 -- other vault errors: never the raw name's value
        raise DeepseekUsageError(f"vault read failed for {name}: {type(e).__name__}") from None
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    if not secret.value:
        raise DeepseekUsageError(f"vault returned an empty secret for {name}")
    if secret.leak_flagged:
        raise DeepseekUsageError(f"vault marks {name} leak-flagged; not using it")
    return secret.value


# Indirection points so tests inject a fake key / a mocked HTTP call.
read_key: Callable[[str], str] = _read_key_from_vault


def _http_get_json(url: str, key: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DeepseekUsageError(f"deepseek balance HTTP {e.code}") from None
    except Exception as e:  # noqa: BLE001 -- network/JSON: class name only, never args
        raise DeepseekUsageError(f"deepseek balance fetch failed ({type(e).__name__})") from None


http_get_json: Callable[[str, str], Dict[str, Any]] = _http_get_json


def parse_balance(body: Dict[str, Any]) -> float:
    """DeepSeek /user/balance JSON -> the USD total_balance. Raises
    DeepseekUsageError on anything that isn't a well-formed success."""
    if not isinstance(body, dict):
        raise DeepseekUsageError("deepseek balance response not a dict")
    infos = body.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        raise DeepseekUsageError("deepseek balance response had no balance_infos")
    usd = next((i for i in infos if isinstance(i, dict) and i.get("currency") == "USD"), infos[0])
    try:
        return float(usd["total_balance"])
    except (KeyError, TypeError, ValueError) as e:
        raise DeepseekUsageError(f"deepseek balance response malformed ({type(e).__name__})") from None


# ---- persisted delta baseline (survives a console restart) ----------------

_state_lock = threading.Lock()


def _load_state() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def _record_and_delta(key_name: str, balance_usd: float, now_iso: str) -> Optional[float]:
    """Compares `balance_usd` to the last PERSISTED good balance for `key_name`
    (None if there isn't one yet -- never a fabricated 0 baseline), then
    persists the new value for next time."""
    with _state_lock:
        state = _load_state()
        prev = state.get(key_name)
        delta = (balance_usd - prev["balance_usd"]) if prev else None
        state[key_name] = {"balance_usd": balance_usd, "at": now_iso}
        _save_state(state)
        return delta


# ---- per-key fetch + cache --------------------------------------------------

_lock = threading.Lock()
_keys_in_memory: Dict[str, str] = {}          # key_name -> raw secret, process-lifetime only
_cache: Dict[str, Dict[str, Any]] = {}        # key_name -> last result (good or unavailable)
_cache_at: Dict[str, float] = {}


def _unavailable(now_iso: str, reason: str = "could not read") -> Dict[str, Any]:
    return {"available": False, "balance_usd": None, "delta_usd": None,
            "reason": reason, "fetched_at": now_iso}


def _fetch_one(key_name: str, now: Optional[float] = None) -> Dict[str, Any]:
    """One key's card payload. Never raises; never returns the key."""
    now = time.time() if now is None else now
    now_iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    try:
        key = _keys_in_memory.get(key_name)
        if key is None:
            key = read_key(key_name)
            _keys_in_memory[key_name] = key
        body = http_get_json(BALANCE_URL, key)
        balance_usd = parse_balance(body)
    except DeepseekKeyNotProvisioned as e:
        logger.warning("deepseek key not provisioned for %s: %s", key_name, e)
        _keys_in_memory.pop(key_name, None)
        return _unavailable(now_iso, reason="key not created yet")
    except DeepseekUsageError as e:
        logger.warning("deepseek usage unavailable for %s: %s", key_name, e)
        _keys_in_memory.pop(key_name, None)   # re-read next time (rotated key)
        return _unavailable(now_iso)
    except Exception as e:  # noqa: BLE001 -- vault/DB errors: class name only
        logger.warning("deepseek usage unavailable for %s: %s", key_name, type(e).__name__)
        _keys_in_memory.pop(key_name, None)
        return _unavailable(now_iso)
    delta = _record_and_delta(key_name, balance_usd, now_iso)
    return {"available": True, "balance_usd": balance_usd, "delta_usd": delta, "fetched_at": now_iso}


def _cached_fetch(key_name: str, now: float) -> Dict[str, Any]:
    """Caller must hold `_lock`. The cached (or freshly fetched) payload for
    ONE key, with `age_s` stamped -- the shared per-key TTL bookkeeping both
    get_deepseek_usage() and get_shared_key_usage() reuse."""
    cached = _cache.get(key_name)
    cached_at = _cache_at.get(key_name, 0.0)
    ttl = CACHE_TTL_S if cached and cached.get("available") else FAIL_TTL_S
    if cached is not None and (now - cached_at) < ttl:
        result = cached
    else:
        result = _fetch_one(key_name, now=now)
        _cache[key_name] = result
        _cache_at[key_name] = now
        cached_at = now
    out = dict(result)
    out["age_s"] = int(now - cached_at) if result.get("available") else None
    return out


def get_deepseek_usage(now: Optional[float] = None) -> Dict[str, Any]:
    """The cached card payload for every distinct PER-CLIENT key (never the
    shared key -- see get_shared_key_usage()). Never raises."""
    now = time.time() if now is None else now
    keys_out: List[Dict[str, Any]] = []
    with _lock:
        for row in distinct_key_rows():
            out = _cached_fetch(row["key_name"], now)
            out["key_name"] = row["key_name"]
            out["families"] = row["families"]
            keys_out.append(out)
    return {"keys": keys_out}


# ---- shared key (revised 2026-10-30, orch-console #83560): -----------------
# DEEPSEEK_CODING_KEY is vault leak_flagged (set 2026-10-29 04:45 UTC).
# vault.py's own documented contract is that get() surfaces leak_flagged and
# the CALLER decides whether to proceed -- _read_key_from_vault (above)
# decided: refuse. Rather than have the console server hold or call a
# leak-flagged key to get a number, the shared row instead reads
# scripts/deepseek_balance_poll.py's own hourly-polled history file directly
# -- that poller (cc-fleet-health, launchd dev.wingmen.deepseek-balance-poll)
# already reads this same key successfully every hour; the console doesn't
# need its own read of it. This removes DEEPSEEK_CODING_KEY from the console
# server process entirely for the shared row (per-client rows are unaffected
# -- still vault-based per key, and none of those 5 is leak-flagged).
_SHARED_HISTORY_PATH = (
    Path.home() / "wingmen" / "fleet-health" / "state" / "deepseek_balance_history.jsonl"
)
_SHARED_STALE_AFTER_S = 3 * 3600  # poller runs hourly; 3 misses is worth flagging, not silently shown


def _read_shared_history() -> List[Dict[str, Any]]:
    try:
        text = _SHARED_HISTORY_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    for line in text.strip().splitlines():
        try:
            row = json.loads(line)
            rows.append({"ts": row["ts"], "balance_usd": float(row["balance_usd"])})
        except (ValueError, KeyError, TypeError):
            continue
    return rows


def get_shared_key_usage(now: Optional[float] = None) -> Dict[str, Any]:
    """The shared row's payload, read from deepseek_balance_poll.py's own
    hourly history file -- never the vault, never an HTTP call, so the
    console server never holds DEEPSEEK_CODING_KEY. Never raises."""
    now = time.time() if now is None else now
    now_iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    rows = _read_shared_history()
    if not rows:
        out = _unavailable(now_iso)
        out["age_s"] = None
    else:
        last = rows[-1]
        try:
            last_ts = datetime.fromisoformat(last["ts"]).timestamp()
        except ValueError:
            last_ts = now
        age_s = now - last_ts
        if age_s > _SHARED_STALE_AFTER_S:
            out = _unavailable(now_iso)
            out["age_s"] = None
        else:
            prev = rows[-2] if len(rows) >= 2 else None
            delta = (last["balance_usd"] - prev["balance_usd"]) if prev else None
            out = {"available": True, "balance_usd": last["balance_usd"], "delta_usd": delta,
                   "fetched_at": last["ts"], "age_s": int(age_s)}
    out["key_name"] = SHARED_KEY_NAME
    return out


# ---- spend, per key, per PROVIDER day (op#37213, orch-console #90015) ------
# The balance endpoint is account-level and carries no cost figure, so the only
# measured cost source is deepseek_console.py's `usage-export` JSONL (written
# every 2h by dev.wingmen.deepseek-usage-export). Two properties of it matter
# and both are easy to get wrong:
#
#  1. Its `date` is a PROVIDER day, not a local one. The console states "All
#     dates and times are GMT+8" and the exporter builds the day in UTC+8
#     (_CONSOLE_TZ in scripts/deepseek_console.py), so a day boundary here is
#     16:00Z. It is also NOT the operator's local day -- Musa is in Abu Dhabi,
#     UTC+4 (orch-console #90026, after this module's own earlier wording
#     wrongly implied "his day"). Every label this feeds must say "provider day
#     (UTC+8)" and nothing else.
#  2. It has NO lane dimension. Cost is attributed to an api_key_name, and
#     several lanes share one key, so per-LANE dollars cannot be derived from
#     it at all. get_spend() therefore reports per FAMILY only, and callers
#     must not manufacture a per-lane split from it.
_USAGE_EXPORT_PATH = (
    Path.home() / "wingmen" / "fleet-health" / "state" / "deepseek_usage_by_key.jsonl"
)
PROVIDER_TZ = timezone(timedelta(hours=8))
BUDGET_LINE_USD = 7.0   # Musa op#36779: under this DeepSeek beats another Claude plan
BUDGET_WARN_USD = 5.0
SPEND_DAYS = 7
# The exporter runs every 2h. Two consecutive misses is worth flagging rather
# than silently presenting an old figure as today's.
_USAGE_STALE_AFTER_S = 4 * 3600


def provider_day(now: Optional[float] = None) -> str:
    """The provider's (UTC+8) calendar day for `now`, as YYYY-MM-DD."""
    now = time.time() if now is None else now
    return datetime.fromtimestamp(now, tz=PROVIDER_TZ).date().isoformat()


def _read_usage_export() -> List[Dict[str, Any]]:
    """Rows from the usage export. Malformed lines are skipped, never zeroed --
    a row we cannot parse must not contribute a fabricated cost."""
    try:
        text = _USAGE_EXPORT_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            name = str(row["api_key_name"]).strip().lower()
            day = str(row["date"])[:10]
            cost = float(row["cost_usd"])
        except (ValueError, KeyError, TypeError):
            continue
        if not name or len(day) != 10:
            continue
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        out.append({"family": name, "date": day, "cost_usd": cost,
                    "requests": int(usage.get("request_count") or 0)})
    return out


def get_spend(now: Optional[float] = None) -> Dict[str, Any]:
    """Per-family cost for the current PROVIDER day plus the 7-day window.

    Never raises. An unreadable/absent export reports available=False with a
    reason -- never 0.00, which would read as "we spent nothing" when the truth
    is "we cannot see". A figure older than _USAGE_STALE_AFTER_S is still
    returned (it is real, just old) but flagged stale so the caller can label
    it rather than pass it off as current.
    """
    now = time.time() if now is None else now
    day = provider_day(now)
    today_d = datetime.fromtimestamp(now, tz=PROVIDER_TZ).date()
    window = [(today_d - timedelta(days=i)).isoformat() for i in range(SPEND_DAYS)]
    window_set = set(window)

    rows = _read_usage_export()
    if not rows:
        return {"available": False, "day": day, "day_label": "provider day (UTC+8)",
                "reason": "usage export not readable", "budget_line_usd": BUDGET_LINE_USD,
                "budget_warn_usd": BUDGET_WARN_USD, "by_family": {}}

    by_family: Dict[str, Dict[str, Any]] = {}
    total_today = 0.0
    total_7d = 0.0
    for r in rows:
        fam = by_family.setdefault(
            r["family"],
            {"today_usd": 0.0, "last7_usd": 0.0, "today_requests": 0, "last7_requests": 0},
        )
        if r["date"] == day:
            fam["today_usd"] += r["cost_usd"]
            fam["today_requests"] += r["requests"]
            total_today += r["cost_usd"]
        if r["date"] in window_set:
            fam["last7_usd"] += r["cost_usd"]
            fam["last7_requests"] += r["requests"]
            total_7d += r["cost_usd"]

    # Freshness comes from the export's own updated_at, which is UTC.
    updated_at = None
    try:
        text = _USAGE_EXPORT_PATH.read_text(encoding="utf-8")
        stamps = []
        for line in text.splitlines():
            try:
                stamps.append(json.loads(line).get("updated_at"))
            except (ValueError, AttributeError):
                continue
        stamps = [s for s in stamps if s]
        updated_at = max(stamps) if stamps else None
    except OSError:
        pass
    age_s = None
    if updated_at:
        try:
            age_s = int(now - datetime.fromisoformat(updated_at).timestamp())
        except ValueError:
            age_s = None

    return {
        "available": True,
        "day": day,
        "day_label": "provider day (UTC+8)",
        "days": window,
        "total_today_usd": round(total_today, 4),
        "total_7d_usd": round(total_7d, 4),
        "budget_line_usd": BUDGET_LINE_USD,
        "budget_warn_usd": BUDGET_WARN_USD,
        "updated_at": updated_at,
        "age_s": age_s,
        "stale": bool(age_s is not None and age_s > _USAGE_STALE_AFTER_S),
        "by_family": by_family,
    }


def family_to_key() -> Dict[str, str]:
    """{family: vault key name} -- the shipped map, verbatim. Public so the
    console can join per-family spend onto its per-KEY rows without reaching
    into _key_map()."""
    return dict(_key_map())


def _reset_for_tests(clear_state_file: bool = True) -> None:
    global _keys_in_memory, _cache, _cache_at
    with _lock:
        _keys_in_memory, _cache, _cache_at = {}, {}, {}
    if clear_state_file:
        try:
            _STATE_PATH.unlink()
        except OSError:
            pass
