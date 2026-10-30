#!/usr/bin/env python3
"""Consistency check: every vault key NAME referenced by
scripts/lib/deepseek_key_map.json must actually exist in vault_secrets.

WHY (orch-console bus #84005/#84011, 2026-10-30): PR#636 shipped the map and
the fail-closed resolver, but several entries named a per-client vault key
that was never actually provisioned (DEEPSEEK_KEY_INTERNAL for hc95/fleet;
cc-loop then hit the same class with family=loop missing a row entirely).
The map's own JSON was never wrong-looking -- it read as a normal mapping --
so the gap stayed invisible until a lane actually tried to launch under one
of those families. This script makes that gap loud instead of silent: run
it standalone (detect-only) or with --alert to also page orch-console on a
dangling reference.

Exit 0 = every referenced vault key exists. Exit 1 = at least one does not
(or the map/DB couldn't be read -- fails LOUD, never silently "looks fine").
"""
import json
import os
import sys
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
_MAP_FILE = _ORCH_DIR / "scripts" / "lib" / "deepseek_key_map.json"


def dangling_vault_keys() -> list[str]:
    """Returns the sorted list of DISTINCT vault key names the map
    references that have no row in vault_secrets. Raises on a map/DB
    problem -- callers must not treat an exception as "nothing dangling"."""
    import psycopg

    with open(_MAP_FILE) as f:
        mapping = json.load(f)
    named = sorted(set(mapping.values()))
    if not named:
        return []

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set in this process's environment.")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM public.vault_secrets WHERE name = ANY(%s)", (named,))
        present = {row[0] for row in cur.fetchall()}
    return sorted(set(named) - present)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    alert = "--alert" in argv

    try:
        missing = dangling_vault_keys()
    except Exception as e:
        sys.stderr.write(f"check_deepseek_key_map: FAILED to check ({e}); treating as a problem, not a pass.\n")
        if alert:
            _page(f"check_deepseek_key_map could not run: {e}")
        return 1

    if not missing:
        print("check_deepseek_key_map: every mapped vault key exists. OK.")
        return 0

    msg = (
        f"deepseek_key_map.json names {len(missing)} vault key(s) that do not exist in "
        f"vault_secrets: {', '.join(missing)}. Any family mapped to one of these will fail "
        f"closed at lane boot (deepseek_key_for_family.py -> provider_vault_fetch.py "
        f"SecretNotFoundError)."
    )
    sys.stderr.write("check_deepseek_key_map: " + msg + "\n")
    if alert:
        _page(msg)
    return 1


def _page(msg: str) -> None:
    try:
        from scripts import bus_send
        bus_send.send(
            from_agent="cc-fleet-health", to="orch-console", mtype="blocker",
            subject="[check_deepseek_key_map] dangling vault-key reference in deepseek_key_map.json",
            body=msg, priority="P2", req=False,
        )
    except Exception as e:
        sys.stderr.write(f"check_deepseek_key_map: --alert page itself failed: {e}\n")


if __name__ == "__main__":
    sys.exit(main())
