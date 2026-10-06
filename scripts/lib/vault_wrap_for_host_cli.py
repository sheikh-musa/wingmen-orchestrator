#!/usr/bin/env python3
"""vault_wrap_for_host_cli.py <secret_name> <reason> — run ON the target host
(the host that needs a NEW ability to read <secret_name>, e.g. gzb/hub-vps).
Reads a base64-encoded plaintext DEK from stdin (piped directly from
vault_export_dek_cli.py on the SOURCE host over SSH — never a shell variable,
never echoed), wraps it under THIS host's own local KEK, and upserts the
result into vault_secret_host_wraps (migration 094). Never reads another
host's KEK; never touches vault_secrets.ciphertext.

Part of the op#26219 multi-host vault portability mechanism (bus
#53277/#53351/#53588).
"""
import base64
import os
import sys

sys.path.insert(0, os.getcwd())

from nervous_system.vault import vault  # noqa: E402

secret_name, reason = sys.argv[1], sys.argv[2]
dek_b64 = sys.stdin.read().strip()
dek = base64.b64decode(dek_b64)
vault.wrap_for_host(secret_name, dek, reason=reason)
sys.stdout.write(f"wrapped {secret_name!r} for this host\n")
