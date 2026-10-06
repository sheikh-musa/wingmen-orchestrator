#!/usr/bin/env python3
"""vault_export_dek_cli.py <secret_name> <reason> — run ON the host that
already has primary read access to <secret_name> (e.g. the Mini). Prints the
secret's plaintext DEK, base64-encoded, to stdout — nothing else. The caller
pipes this directly into vault_wrap_for_host_cli.py on the TARGET host over
SSH; it must never be captured into a shell variable that gets echoed/logged,
same contract as provider_vault_fetch.py's secret.value.

Part of the op#26219 multi-host vault portability mechanism (bus
#53277/#53351/#53588, migration 094) — a plaintext DEK is the only material
that ever crosses hosts, over the same channel a vault secret's value
already crosses on a hand-delivered put(); a KEK never does.
"""
import base64
import os
import sys

sys.path.insert(0, os.getcwd())

from nervous_system.vault import vault  # noqa: E402

secret_name, reason = sys.argv[1], sys.argv[2]
dek = vault.export_dek(secret_name, reason=reason)
sys.stdout.write(base64.b64encode(dek).decode("ascii"))
