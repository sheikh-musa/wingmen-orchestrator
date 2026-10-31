#!/usr/bin/env python3
"""put_client_secret.py — the ONE sanctioned way for an agent to move a client
credential from where it lives (a store's options table, an env var, a provider
CLI) into the fleet vault WITHOUT ever printing it (orch-console bus #87265, P0,
Musa op 36795).

Why this exists: the 30 Oct leak was not a missing vault — it was a shortcut
around it. A body needed a WooCommerce secret, dumped it out of `wp_options` with
a SELECT, and the value became part of its transcript (and therefore of every
later request to the model provider). `scripts/hooks/credential_store_guard.py`
now refuses that dump; this helper is the road that stays open:

    psql "$WOO_DSN" -tAc \\
      "SELECT option_value FROM wp_options WHERE option_name = 'wc_facebook_feed_url_secret'" \\
      | scripts/put_client_secret.py MODESTY_HUT_WC_FB_FEED_SECRET \\
          --reason "vaulting the woo feed secret off the store (bus 87265)"

The value travels inside the pipe. Nothing here echoes it: the only output is the
vault name, a sha256 PREFIX (8 hex chars, for later hash-matched leak sweeps) and
the value's LENGTH. The same 8-char prefix is what `secret_hash_sweep.py` searches
transcripts for, so a later scan can prove a value never appeared anywhere.

Input, exactly one of:
  * stdin (piped)                        — the usual case
  * --from-env VAR                       — the value is already in the environment
                                          (e.g. a store's own client library put it there)
  * --from-file PATH                     — a credential file, read without printing
Never from argv: a value on the command line is in shell history and in the
transcript of the very command that was supposed to protect it.

Safety:
  * refuses an empty value, a tty-stdin with no --from-env/--from-file, and a
    name containing characters that do not belong in a vault key;
  * writes through nervous_system.vault.Vault.put, so the reason is audited and
    the value is encrypted under the local KEK like every other vault secret;
  * prints nothing on failure except the error class (never the value, never the
    ciphertext, never a DSN).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


def _die(msg: str, code: int = 1) -> int:
    sys.stderr.write(f"put_client_secret: {msg}\n")
    return code


def _read_value(args) -> str | None:
    if args.from_env:
        value = os.environ.get(args.from_env)
        if value is None:
            sys.stderr.write(
                f"put_client_secret: --from-env {args.from_env} is not set.\n")
            return None
        return value
    if args.from_file:
        try:
            with open(os.path.expanduser(args.from_file), "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            sys.stderr.write(f"put_client_secret: cannot read --from-file ({exc.__class__.__name__}).\n")
            return None
    if sys.stdin.isatty():
        sys.stderr.write(
            "put_client_secret: no value supplied. Pipe it in, or pass --from-env VAR / "
            "--from-file PATH. The value is never accepted as an argument.\n")
        return None
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Move a client credential into the fleet vault without printing it.")
    ap.add_argument("name", help="vault key, e.g. MODESTY_HUT_WC_FB_FEED_SECRET")
    ap.add_argument("--reason", required=True,
                    help="why this secret is being stored (goes to the vault audit log)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from-env", metavar="VAR", help="read the value from this env var")
    src.add_argument("--from-file", metavar="PATH", help="read the value from this file")
    ap.add_argument("--leak-flagged", action="store_true",
                    help="mark the value as already exposed (requires --leak-reason)")
    ap.add_argument("--leak-reason", default="", help="how it was exposed")
    ap.add_argument("--dry-run", action="store_true",
                    help="do everything except write to the vault; still prints only "
                         "name + hash prefix + length")
    args = ap.parse_args(argv)

    if not _NAME_RE.match(args.name):
        return _die(f"invalid vault name {args.name!r} "
                    "(letters, digits, _ . : - only; must not start with punctuation).", 2)
    if not args.reason.strip():
        return _die("--reason must not be empty (it is the audit trail).", 2)
    if args.leak_flagged and not args.leak_reason.strip():
        return _die("--leak-flagged requires --leak-reason.", 2)

    value = _read_value(args)
    if value is None:
        return 1
    # Surrounding whitespace is stripped: the newline psql/cat adds would otherwise
    # become part of the stored secret. A value with meaningful leading/trailing
    # whitespace is out of scope for this helper (use the vault directly).
    value = value.strip()
    if not value:
        return _die("the supplied value is empty — nothing written.")

    fp = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    length = len(value)
    # Hash and length are computed before the value is handed off, and the local
    # reference is dropped as soon as the vault call returns.
    try:
        if args.dry_run:
            sys.stdout.write(f"DRY-RUN {args.name} sha256:{fp} len={length}\n")
            return 0

        sys.path.insert(0, _REPO_ROOT)
        from nervous_system.vault import vault, VaultError  # noqa: E402

        try:
            vault.put(
                args.name,
                value,
                reason=args.reason.strip(),
                leak_flagged=args.leak_flagged,
                leak_flagged_reason=(args.leak_reason.strip() or None),
            )
        except VaultError as exc:
            return _die(f"vault.put refused this write ({exc})")
        except Exception as exc:  # noqa: BLE001 — never echo the value
            return _die(f"vault.put failed ({exc.__class__.__name__})")
    finally:
        del value

    # The ONLY success output: name + hash prefix + length. No value, no ciphertext.
    sys.stdout.write(f"OK {args.name} sha256:{fp} len={length}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
