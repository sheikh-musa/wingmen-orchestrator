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
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from dotenv import load_dotenv  # noqa: E402

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

    server = BrokerServer(
        sock_path=os.environ["EXEC_BROKER_SOCKET"],
        allowed_uids=allowed_uids,
        db_conn_factory=_db_conn_factory,
        token=token,
        mgmt_api_fn=post_to_mgmt_api,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
