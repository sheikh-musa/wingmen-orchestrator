#!/usr/bin/env python3
"""vault_placeholder_send.py — resolves a {{SECRET}} placeholder for a send
script's --secret-vault-key option (bus #44378).

Reads the placeholder-templated text and the vault key name from ENV (never
argv — same convention tg_send.sh already uses for the bot token/chat/text,
so the resolved value stays out of `ps`), fetches the value in-process via
nervous_system.vault, and prints ONLY the substituted text to stdout. The
caller (the shell send script) must keep using the ORIGINAL template — with
the placeholder still in it — for the durable operator_log call: this script
never sees or returns anything that call should record.

Env in:
  VPH_TEMPLATE   the outgoing message text, containing exactly one {{SECRET}}
  VPH_VAULT_KEY  the vault secret name to fetch

Stdout out: the substituted text, nothing else. Diagnostics go to stderr.

Exit codes:
  0  ok, substituted text printed to stdout
  1  no {{SECRET}} placeholder in VPH_TEMPLATE — refuse. A caller passing
     --secret-vault-key with no placeholder almost certainly meant to paste
     the raw value directly into the message instead, which is exactly the
     footgun this option exists to remove.
  2  VPH_VAULT_KEY missing, or the vault lookup failed (no KEK, unknown
     secret, wrong host, ...) — see nervous_system.vault's VaultError message.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from nervous_system.vault import VaultError, vault  # noqa: E402

PLACEHOLDER = "{{SECRET}}"


def substitute(template: str, key: str, *, get=None) -> str:
    """Pure-ish core: replaces PLACEHOLDER in `template` with vault key
    `key`'s value. `get` is injectable for tests (defaults to vault.get).
    Raises ValueError if the placeholder is absent, VaultError on lookup
    failure — main() turns both into the documented exit codes."""
    if PLACEHOLDER not in template:
        raise ValueError(
            f"--secret-vault-key {key!r} given but the message has no {PLACEHOLDER} "
            "placeholder — refusing (bus #44378: this option exists so the resolved "
            "value never has to be pasted into the message text by hand)."
        )
    fetch = get or vault.get
    secret = fetch(key, reason="send-script {{SECRET}} placeholder substitution (bus #44378)")
    return template.replace(PLACEHOLDER, secret.value)


def main(argv=None) -> int:
    template = os.environ.get("VPH_TEMPLATE", "")
    key = os.environ.get("VPH_VAULT_KEY", "")
    if not key:
        print("vault_placeholder_send: VPH_VAULT_KEY not set", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(substitute(template, key))
    except ValueError as exc:
        print(f"vault_placeholder_send: {exc}", file=sys.stderr)
        return 1
    except VaultError as exc:
        print(f"vault_placeholder_send: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
