"""Operator-token auth + access audit (CAI-RESP-264 condition 3).

- Token comes from env CONSOLE_TOKEN.
- Accept ONLY `Authorization: Bearer <token>` — token in URL/query is rejected.
- Fail-closed: if CONSOLE_TOKEN is unset/empty, every request is rejected.
- Constant-time comparison (no timing oracle on the secret).
- Audit-log every access (timestamp / client / path / outcome) to
  logs/console_access.log (override via CONSOLE_ACCESS_LOG).

Stdlib-only. The token itself is NEVER written to the access log.
"""
from __future__ import annotations

import hmac
import os
import pathlib
from datetime import datetime, timezone
from typing import Mapping

_DEFAULT_LOG = (
    pathlib.Path(__file__).resolve().parents[2] / "logs" / "console_access.log"
)


def _configured_token() -> str:
    return os.environ.get("CONSOLE_TOKEN", "") or ""


def _header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup."""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return ""


def check_bearer(headers: Mapping[str, str], query: Mapping[str, object]) -> bool:
    """Return True iff a valid `Authorization: Bearer <token>` is present.

    Header-only by construction: a token supplied via *query* can never
    authenticate, even if it is the correct secret. Fail-closed when no token
    is configured.
    """
    configured = _configured_token()
    if not configured:
        return False  # fail-closed: no secret configured

    raw = _header(headers, "Authorization").strip()
    prefix = "Bearer "
    if not raw.startswith(prefix):
        return False
    presented = raw[len(prefix):].strip()
    if not presented:
        return False
    return hmac.compare_digest(presented, configured)


def _log_path() -> pathlib.Path:
    override = os.environ.get("CONSOLE_ACCESS_LOG")
    return pathlib.Path(override) if override else _DEFAULT_LOG


def audit(client: str, path: str, outcome: str) -> None:
    """Append one access record. Never raises (auditing must not break serving)."""
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts}\t{client}\t{path}\t{outcome}\n"
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        # Auditing failure must not take down the read-only surface.
        pass
