#!/usr/bin/env python3
"""Fetch one provider coding-plan key from the fleet vault.

Isolated into its own file (rather than an inline `python -c` snippet in
provider_routing.sh) so tests can swap in a stub script via
apply_provider_routing's fetch_script argument without touching the real
vault. Prints the raw key to stdout on success; any failure (missing key,
vault error) exits non-zero with nothing on stdout — the caller treats
empty stdout as fail-closed, same as a non-zero exit.
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from nervous_system.vault import vault  # noqa: E402

key_name, label = sys.argv[1], sys.argv[2]
secret = vault.get(key_name, reason=f"launch_dangerous_cc: {label} provider for lane boot")
sys.stdout.write(secret.value)
