#!/usr/bin/env python3
"""Resolve the per-client DeepSeek vault-key NAME for a lane's family (Musa
op#35814, console bus #82358: per-client DeepSeek keys, replacing the one
shared DEEPSEEK_CODING_KEY that got leak-flagged after a Telegram paste).

deepseek_key_for_family.py <agent_id>

Prints the vault key name (e.g. "DEEPSEEK_KEY_BARIQ") on stdout and exits 0
on a known family. On ANY failure -- unknown family, unreadable/invalid map
file -- prints nothing to stdout, a clear reason to stderr, and exits 1.
FAIL-CLOSED is the point: console's explicit requirement is that a lane with
no mapped family must never silently fall back to the shared
DEEPSEEK_CODING_KEY once this map is live.

FAMILY DERIVATION IS SHARED, NOT DUPLICATED: family_of() is imported from
lane_token_resolver (same source scripts/lib/lane_model_resolver.py reuses)
so this map keys off the IDENTICAL family as every other per-family tier in
the fleet -- a twin family-map would drift (already-documented lesson).

Whether a family's named key actually EXISTS in the vault is NOT checked
here -- that's provider_vault_fetch.py's job (it already fails closed on a
missing/empty vault secret). This script only answers "which name".
"""
import json
import os
import sys

# Self-locating (NOT cwd-based): this script is invoked with an explicit
# absolute path from provider_routing.sh, which may be running against a
# fake orch_dir in tests -- the real repo root (for the `scripts.lib...`
# import below AND for the map file beside this one) must come from
# __file__, same pattern as lane_model_resolver.py's _default_orch_dir().
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_ORCH_ROOT = os.path.dirname(os.path.dirname(_LIB_DIR))
sys.path.insert(0, _ORCH_ROOT)

from scripts.lib.lane_token_resolver import family_of  # noqa: E402

_MAP_FILE = os.path.join(_LIB_DIR, "deepseek_key_map.json")


def main(argv):
    if len(argv) != 2:
        print("usage: deepseek_key_for_family.py <agent_id>", file=sys.stderr)
        return 1
    agent_id = argv[1]

    family = family_of(agent_id)
    if not family:
        print(f"no lane family could be derived from agent_id={agent_id!r}", file=sys.stderr)
        return 1

    try:
        with open(_MAP_FILE) as f:
            mapping = json.load(f)
    except (OSError, ValueError) as e:
        print(f"deepseek_key_map.json unreadable/invalid: {e}", file=sys.stderr)
        return 1

    key_name = mapping.get(family)
    if not key_name:
        print(
            f"no DeepSeek vault-key mapped for family={family!r} (agent_id={agent_id!r}); "
            "refusing to fall back to the shared DEEPSEEK_CODING_KEY",
            file=sys.stderr,
        )
        return 1

    sys.stdout.write(key_name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
