#!/usr/bin/env python3
"""No-echo, no-argv secret intake into the fleet vault (Musa op#35814, bus
#82358). Built so Musa (or anyone with a live terminal on the Mini) can hand
a brand-new secret to the vault WITHOUT ever typing it into Telegram, a bus
message, a CLI argument, or anything that lands in shell history or an
agent transcript -- the exact gap that leak-flagged DEEPSEEK_CODING_KEY
(pasted on Telegram instead of going through an intake like this one).

Usage (via the scripts/vault_put_prompt.sh wrapper, run in a human's OWN
terminal -- never inside an agent's tmux session, since that enters a
transcript):

    scripts/vault_put_prompt.sh NAME "reason text"

The secret VALUE is never a command-line argument (never in argv, never in
shell history) -- it is read via getpass.getpass(), which disables terminal
echo and is not logged anywhere. On success, prints ONLY the secret's name
and created_at timestamp (metadata -- never the value, never the ciphertext).
"""
import getpass
import os
import sys

sys.path.insert(0, os.getcwd())

from nervous_system.vault import vault, VaultError, _connect  # noqa: E402


def main(argv):
    if len(argv) != 3:
        print('usage: vault_put_prompt.py NAME "reason"', file=sys.stderr)
        return 1
    name, reason = argv[1], argv[2]

    if not sys.stdin.isatty():
        print(
            "refusing to run non-interactively -- this helper exists so the secret "
            "value is only ever typed by a human at a real terminal, never piped/scripted.",
            file=sys.stderr,
        )
        return 1

    value = getpass.getpass(f"Paste the value for '{name}' (input hidden, never echoed): ")
    if not value:
        print("empty value, aborting -- nothing written.", file=sys.stderr)
        return 1
    confirm = getpass.getpass("Paste it again to confirm: ")
    if confirm != value:
        print("the two entries did not match, aborting -- nothing written.", file=sys.stderr)
        return 1
    del confirm

    try:
        vault.put(name, value, reason=reason)
    except VaultError as e:
        print(f"vault.put failed: {e}", file=sys.stderr)
        return 1
    finally:
        del value

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, created_at FROM public.vault_secrets WHERE name = %s",
                (name,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        print("vault.put returned but the row could not be re-read -- investigate.", file=sys.stderr)
        return 1

    print(f"OK: {row[0]} created_at={row[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
