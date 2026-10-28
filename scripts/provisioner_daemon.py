#!/usr/bin/env python3
"""provisioner_daemon.py — ceayj tenant-provisioner launchd ExecStart target.

Reads config from the environment (PROVISIONER_SOCKET,
PROVISIONER_ALLOWED_UIDS, PROVISIONER_STORE_DSN_FILE; bus DATABASE_URL via
substrate_dsn.dsn_from_env_file), wires ProvisionerServer with real
dependencies, serves forever.

DSN resolution, both fresh-per-use (op#24342: a long-lived process that
keeps its startup DSN keeps re-trying a pre-rotation password after a
rotation):
  - bus (substrate): substrate_dsn.dsn_from_env_file() — the .env FILE wins
    over the inherited environment; the row_factory is dict_row because the
    gate re-read names columns (from_agent/message_type/is_past/...).
  - store (ceayj): read from a 0600 file minted by orch-console. The lane
    that ships this daemon is residency-barred from the ceayj admin write
    DSN — the daemon reads it at request time, nothing logs it, and
    encode_error strips DSN-shaped text before anything reaches the wire.

A missing store DSN file is a per-request UPSTREAM_ERROR (the daemon stays
up); a malformed environment at BOOT fails loud — never a silent deny-all
or wildcard allowlist.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from provisioner.peer import peer_uid  # noqa: E402
from provisioner.server import ProvisionerServer  # noqa: E402
from substrate_dsn import dsn_from_env_file  # noqa: E402

_REQUIRED_ENV_VARS = ("PROVISIONER_SOCKET", "PROVISIONER_ALLOWED_UIDS", "PROVISIONER_STORE_DSN_FILE")


class DaemonConfigError(Exception):
    """Boot-time configuration failure. Fails loud — never a silent
    deny-all or wildcard-allow fallback."""


def load_allowed_uids(raw: str) -> frozenset[int]:
    """Parse comma-separated PROVISIONER_ALLOWED_UIDS. Raises ValueError on
    an empty value or a non-digit entry."""
    if not raw or not raw.strip():
        raise ValueError("PROVISIONER_ALLOWED_UIDS must not be empty")
    uids = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            raise ValueError(f"PROVISIONER_ALLOWED_UIDS entry {part!r} is not a positive integer uid")
        uids.append(int(part))
    return frozenset(uids)


def read_store_dsn(path: Path) -> str:
    """Read the ceayj admin write DSN fresh from its file. Refuses a
    group/world-readable file (the DSN is the store credential) and an
    empty one."""
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise DaemonConfigError(
            f"{path} mode is {oct(mode)} — the store DSN file must be 0600"
        )
    dsn = path.read_text().strip()
    if not dsn:
        raise DaemonConfigError(f"{path} is empty — no store DSN")
    return dsn


def build_bus_conn_factory(dsn: str):
    def factory():
        return psycopg.connect(dsn, row_factory=dict_row)

    return factory


def build_store_conn_factory(dsn_path: Path):
    def factory():
        return psycopg.connect(read_store_dsn(dsn_path))

    return factory


def main() -> None:
    for var in _REQUIRED_ENV_VARS:
        if not os.environ.get(var):
            raise SystemExit(f"missing required env var {var}")
    allowed = load_allowed_uids(os.environ["PROVISIONER_ALLOWED_UIDS"])
    store_dsn_file = Path(os.environ["PROVISIONER_STORE_DSN_FILE"])

    server = ProvisionerServer(
        sock_path=os.environ["PROVISIONER_SOCKET"],
        allowed_uids=allowed,
        bus_conn_factory=build_bus_conn_factory(dsn_from_env_file()),
        store_conn_factory=build_store_conn_factory(store_dsn_file),
        peer_uid_fn=peer_uid,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
