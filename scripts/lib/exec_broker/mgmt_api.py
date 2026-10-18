#!/usr/bin/env python3
"""mgmt_api.py — the broker's own call to the Supabase Management API
(contract: "the broker itself makes the mgmt-API call").

Same URL template and curl-based transport as ihsanos-irsyad's
scripts/db/exec_prod.py (_post_to_mgmt_api) for behavioral parity with
today's direct path. Never echoes a raw upstream error (stderr, host/port)
into a raised message or a log line -- same leak discipline as exec_prod.py's
_fetch_gate_message.
"""
from __future__ import annotations

import json
import subprocess

MGMT_QUERY_URL = "https://api.supabase.com/v1/projects/{ref}/database/query"

# Broker's own round-trip budget: leaves headroom under the contract's 30s
# exec_prod-side timeout so the broker can still finalize the vault_access_log
# row and respond within that window.
_CURL_MAX_TIME = "25"
_SUBPROCESS_TIMEOUT = 28.0


class UpstreamError(Exception):
    """The mgmt-API call failed -- network, timeout, malformed response, or an
    error-shaped body. Maps to §5's UPSTREAM_ERROR."""


def post_to_mgmt_api(guarded_sql: str, project_ref: str, token: str, timeout: float = _SUBPROCESS_TIMEOUT) -> list:
    """POST guarded_sql to the mgmt API. Returns the 'rows' list on success.
    Raises UpstreamError (fixed, token/stderr-free message) on any failure."""
    payload = json.dumps({"query": guarded_sql})
    try:
        proc = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                MGMT_QUERY_URL.format(ref=project_ref),
                "-H", f"Authorization: Bearer {token}",
                "-H", "Content-Type: application/json",
                "-H", "User-Agent: Mozilla/5.0",
                "--max-time", _CURL_MAX_TIME,
                "-d", "@-",
            ],
            input=payload, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise UpstreamError("mgmt-API call timed out") from e
    except OSError as e:
        raise UpstreamError(f"mgmt-API call failed to run (errno {e.errno})") from e

    if proc.returncode != 0:
        raise UpstreamError(f"mgmt-API call failed (curl exit {proc.returncode})")

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise UpstreamError("mgmt-API returned a non-JSON response") from e

    if not isinstance(parsed, list):
        raise UpstreamError("mgmt-API returned an error-shaped response")

    return parsed
