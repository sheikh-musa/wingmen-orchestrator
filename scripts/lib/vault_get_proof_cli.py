#!/usr/bin/env python3
"""vault_get_proof_cli.py <secret_name> <reason> — proves vault.get(secret_name)
succeeds on THIS host without ever printing the secret value. Prints only
redacted metadata: length and a short sha256 prefix of the value (enough to
cross-check the SAME value was read on two different hosts without either
host's output revealing it).
"""
import hashlib
import os
import sys

sys.path.insert(0, os.getcwd())

from nervous_system.vault import vault  # noqa: E402

secret_name, reason = sys.argv[1], sys.argv[2]
secret = vault.get(secret_name, reason=reason)
digest = hashlib.sha256(secret.value.encode("utf-8")).hexdigest()[:12]
print(f"OK name={secret_name} len={len(secret.value)} sha256_12={digest} leak_flagged={secret.leak_flagged}")
