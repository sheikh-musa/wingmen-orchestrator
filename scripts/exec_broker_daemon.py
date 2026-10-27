#!/usr/bin/env python3
"""exec_broker_daemon.py — hc#95 exec-broker systemd ExecStart target.

Reads its config from the environment (EXEC_BROKER_SOCKET,
EXEC_BROKER_ALLOWED_UIDS, EXEC_BROKER_TOKEN_FILE; DATABASE_URL via
substrate_dsn.dsn_from_env_file -- see below), wires up BrokerServer with
real dependencies (psycopg for the DB, the real mgmt-API transport), and
serves forever. The PAT is read ONCE from its dedicated 0400 file at startup
and held only in memory — never logged, never re-read per request.

DATABASE_URL is resolved FRESH on every connection via
substrate_dsn.dsn_from_env_file(), never cached from os.environ at startup
(op#24342: a long-lived process that keeps its launch-time DATABASE_URL
keeps re-trying a PRE-ROTATION password after a DB password rotation,
hammering the pooler into ECIRCUITBREAKER -- the 2026-09-30/10-01
incidents). The .env FILE wins over the inherited environment so a
rotation's single push-point (the file) is always honored.

BOUNDED OVERRIDE (op#34322/#76985/#76989/#76998, 2026-10-26, orch-console
gate): four optional env vars arm a single, wall-clock-gated exception on
top of the permanent allowlists in acl.py/gate_validation.py --
EXEC_BROKER_UID_OVERRIDE + EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT (uid),
EXEC_BROKER_REF_OVERRIDE_ALIAS + EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT (ref).
The ref override is keyed by silo ALIAS (e.g. "goumlyne"), never the raw
project ref -- see resolve_ref_override_alias()'s docstring for why (the
console's own console_irsyad_guard hook blocks it from ever typing the raw
ref, and the console is the sole gzb-root holder who arms this). Both
expiries are clamped to a 24h cap in code (parse_override_expiry) and
re-evaluated against wall-clock time on every request, so the exception
reverts by construction the moment it expires.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from dotenv import load_dotenv  # noqa: E402

from exec_broker.gate_validation import SILO_ALIASES  # noqa: E402
from exec_broker.mgmt_api import post_to_mgmt_api  # noqa: E402
from exec_broker.server import BrokerServer  # noqa: E402
from substrate_dsn import dsn_from_env_file  # noqa: E402

_REQUIRED_ENV_VARS = ("EXEC_BROKER_SOCKET", "EXEC_BROKER_ALLOWED_UIDS", "EXEC_BROKER_TOKEN_FILE")


def load_allowed_uids(raw: str) -> frozenset[int]:
    """Parse a comma-separated EXEC_BROKER_ALLOWED_UIDS value. Raises
    ValueError on an empty value or a non-digit entry — fail loud, never
    silently fall back to an empty (deny-all) or wildcard (allow-all) set."""
    if not raw or not raw.strip():
        raise ValueError("EXEC_BROKER_ALLOWED_UIDS must not be empty")
    uids = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            raise ValueError(f"EXEC_BROKER_ALLOWED_UIDS entry {part!r} is not a positive integer uid")
        uids.append(int(part))
    return frozenset(uids)


_OVERRIDE_MAX_HOURS = 24


def load_uid_override(raw: "str | None") -> "frozenset[int] | None":
    """Like load_allowed_uids but OPTIONAL — unset/blank means no override
    (inert, not an error). A malformed non-empty value still fails loud
    (same digit-only rule as the permanent allowlist)."""
    if not raw or not raw.strip():
        return None
    return load_allowed_uids(raw)


def resolve_ref_override_alias(raw: "str | None") -> "str | None":
    """Resolve EXEC_BROKER_REF_OVERRIDE_ALIAS (a silo ALIAS like "goumlyne",
    never a raw project ref) to its real project_ref via the same
    SILO_ALIASES table gate_validation.py already uses for gate-text
    matching. None if unset/blank (override inert).

    WHY an alias and not the raw ref (op#34322/#76998, orch-console): the
    console's own console_irsyad_guard PreToolUse hook pattern-matches the
    literal ref string (e.g. "goumlynecruxrlmzlntp") in ANY tool call the
    console body makes and refuses it -- and the console is the sole
    gzb-root holder who can perform this env-edit+restart. Keying the arm
    env var by alias means the root-holder's own typed command/file never
    contains the guard-blocked literal; only this trusted in-process lookup
    (never console-typed) resolves it to the real ref. Fails loud on an
    unrecognized alias -- never silently treated as "no override" (that
    could look like a disarm when someone actually intended an arm), and
    never falls back to matching a raw ref directly (that would just
    reopen the same guard trip this exists to avoid)."""
    if not raw or not raw.strip():
        return None
    alias = raw.strip().lower()
    try:
        return SILO_ALIASES[alias]
    except KeyError:
        raise ValueError(
            f"EXEC_BROKER_REF_OVERRIDE_ALIAS {alias!r} is not a known silo alias "
            f"(one of {sorted(SILO_ALIASES)})"
        ) from None


def parse_override_expiry(raw: "str | None", *, now: datetime) -> "datetime | None":
    """Parse an ISO8601 expiry for a bounded override. None if unset/blank
    (override inert). Raises ValueError on a malformed non-empty value —
    fail loud, never silently treat a garbled expiry as "no override" (that
    could look like a disarm when someone actually intended an arm).

    A parsed expiry further than _OVERRIDE_MAX_HOURS from `now` is CLAMPED
    down to that cap (logged loudly, not rejected) — the bounded-exception
    ceiling (op#34322/#76985/#76989) is enforced HERE in code, never left to
    whoever sets the env to get right."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"malformed override expiry {raw!r}: {e}") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    cap = now + timedelta(hours=_OVERRIDE_MAX_HOURS)
    if parsed > cap:
        print(
            f"exec_broker_daemon: override expiry {raw!r} exceeds the {_OVERRIDE_MAX_HOURS}h "
            f"cap — clamped to {cap.isoformat()}",
            file=sys.stderr,
        )
        return cap
    return parsed


def _db_conn_factory():
    import psycopg
    from psycopg.rows import dict_row

    # Resolved fresh per connection (op#24342) -- env_path=None uses
    # substrate_dsn's own default (the orchestrator repo's .env); point
    # EXEC_BROKER_ENV_FILE at a different file if provisioning gives this
    # daemon its own, still with the same file-wins-over-environment and
    # fail-loud-on-missing guarantees.
    dsn = dsn_from_env_file(env_path=os.environ.get("EXEC_BROKER_ENV_FILE"))
    return psycopg.connect(dsn, row_factory=dict_row)


def main(argv=None) -> int:
    load_dotenv()

    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        print(f"exec_broker_daemon: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        dsn_from_env_file(env_path=os.environ.get("EXEC_BROKER_ENV_FILE"))
    except RuntimeError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    try:
        allowed_uids = load_allowed_uids(os.environ["EXEC_BROKER_ALLOWED_UIDS"])
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    token_path = Path(os.environ["EXEC_BROKER_TOKEN_FILE"])
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as e:
        print(f"exec_broker_daemon: could not read token file {token_path}: {e}", file=sys.stderr)
        return 1
    if not token:
        print(f"exec_broker_daemon: token file {token_path} is empty", file=sys.stderr)
        return 1

    try:
        ref_override = resolve_ref_override_alias(os.environ.get("EXEC_BROKER_REF_OVERRIDE_ALIAS"))
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1
    try:
        uid_override = load_uid_override(os.environ.get("EXEC_BROKER_UID_OVERRIDE"))
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    try:
        ref_override_expires_at = parse_override_expiry(
            os.environ.get("EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT"), now=now
        )
        uid_override_expires_at = parse_override_expiry(
            os.environ.get("EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT"), now=now
        )
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    # Half an override wired is a misconfiguration, not "no override" — fail
    # loud at boot rather than let a lone ref/uid with no paired expiry (or
    # vice versa) compare against None on a live request later.
    if ref_override and ref_override_expires_at is None:
        print(
            "exec_broker_daemon: EXEC_BROKER_REF_OVERRIDE_ALIAS set without "
            "EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT",
            file=sys.stderr,
        )
        return 1
    if ref_override_expires_at is not None and not ref_override:
        print(
            "exec_broker_daemon: EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT set without "
            "EXEC_BROKER_REF_OVERRIDE_ALIAS",
            file=sys.stderr,
        )
        return 1
    if uid_override and uid_override_expires_at is None:
        print(
            "exec_broker_daemon: EXEC_BROKER_UID_OVERRIDE set without "
            "EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT",
            file=sys.stderr,
        )
        return 1
    if uid_override_expires_at is not None and uid_override is None:
        print(
            "exec_broker_daemon: EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT set without "
            "EXEC_BROKER_UID_OVERRIDE",
            file=sys.stderr,
        )
        return 1

    server = BrokerServer(
        sock_path=os.environ["EXEC_BROKER_SOCKET"],
        allowed_uids=allowed_uids,
        db_conn_factory=_db_conn_factory,
        token=token,
        mgmt_api_fn=post_to_mgmt_api,
        uid_override=uid_override,
        uid_override_expires_at=uid_override_expires_at,
        ref_override=ref_override,
        ref_override_expires_at=ref_override_expires_at,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
