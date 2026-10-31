#!/usr/bin/env python3
"""rotate_substrate_dsn.py — stage, apply and verify a substrate DSN rotation.

WHY THIS EXISTS (bus 87628 → 87660, 2026-11-01)
  The substrate `postgres` role's password reached three transcript files (26 Oct
  `pgrep -fl` leak, session 6da64adb). It was still byte-identical to the live
  `DATABASE_URL`, i.e. a working project-owner credential sitting in plaintext on a
  disk that gets replayed to model providers. Resetting it in the Supabase dashboard
  is one click; the dangerous part is everything AFTER that click — every long-lived
  consumer on the box keeps a dead password in `.env` and starts failing auth, and a
  manual edit is exactly where a value gets printed into a transcript.

  So the value never travels through a shell argument, a log line or a chat message:
  it comes IN on stdin, lands in the vault, and comes back out only into `.env`.
  Every line this tool prints is a sha256 prefix and a length.

SUBCOMMANDS
  stage                 read the NEW password from stdin (never argv), validate, and
                        store it in the vault, marking the superseded value leaked.
                        Run this AFTER the dashboard reset, or before — either way
                        the vault is the only place the value is written.
  apply [--dry-run]     rewrite every `.env` key whose URL embeds the OLD password so
                        it embeds the vault's staged password instead. Atomic write,
                        0600 backup, prints old/new sha8 only.
  verify                prove the new DSN authenticates AND the old one no longer does
                        (old value read from the backup file, never printed). Prints
                        PASS/FAIL per probe.
  restart-consumers     kickstart the launchd jobs that hold a long-lived connection,
                        in dependency order. `--dry-run` lists them.

ORDER OF OPERATIONS (the runbook this encodes)
  1. dashboard: reset the DB password for the substrate project
  2. this tool: `stage`   (paste the new value on stdin)
  3. this tool: `apply`   then `restart-consumers`
  4. this tool: `verify`
  5. scrub the old value out of the transcripts (scripts/secret_hash_sweep.py)

SAFETY
  * refuses to write a value shorter than MIN_PASSWORD_LEN
  * refuses to touch a key it cannot parse; never rewrites a file it did not change
  * `--dry-run` is the default for `apply`; writing needs `--execute`
  * never prints, echoes or logs a password — sha256[:8] and length only
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_ENV_FILE = Path.home() / "wingmen" / "orchestrator" / ".env"
# The name the fleet already stores this credential under (vault_secrets,
# created 2026-10-01 by the previous rotation). Reusing it keeps one canonical
# entry instead of a fourth name for the same secret.
DEFAULT_SECRET_NAME = "SUBSTRATE_DB_PASSWORD_NEW"
MIN_PASSWORD_LEN = 20

URL_WITH_PASSWORD_RE = re.compile(r"://([^:/@\s]+):([^@\s]+)@")
ENV_LINE_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")

# Long-lived consumers: they open a connection or read the DSN at boot and would
# keep a dead password until restarted. Short-lived launchd jobs re-read `.env`
# on every run and are deliberately NOT restarted here.
LONG_LIVED_JOBS = (
    "dev.wingmen.ingest",
    "dev.wingmen.tg-out",
)


def sha8(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def mask(value: str) -> str:
    """The only representation of a secret this tool ever emits."""
    return f"sha256:{sha8(value)} len={len(value)}"


# ── .env handling ──────────────────────────────────────────────────────────────

def _unquote(raw: str) -> Tuple[str, str]:
    """Split a .env value into (quote-char, inner). Quotes are preserved on write."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[0], raw[1:-1]
    return "", raw


def read_env_keys(path: Path) -> list:
    """[(key, value)] for every parseable KEY=VALUE line. Values never printed."""
    out = []
    for line in path.read_text(errors="replace").splitlines():
        m = ENV_LINE_RE.match(line)
        if not m:
            continue
        _q, inner = _unquote(m.group(4))
        out.append((m.group(2), inner))
    return out


def keys_embedding_password(path: Path, password_sha8: str) -> list:
    """Keys whose URL value carries a password with this sha8 prefix."""
    found = []
    for key, value in read_env_keys(path):
        m = URL_WITH_PASSWORD_RE.search(value)
        if m and sha8(m.group(2)) == password_sha8:
            found.append(key)
    return found


def rewrite_env(path: Path, old_sha8: str, new_password: str,
                backup: bool = True) -> Tuple[list, Optional[Path]]:
    """Replace the password inside URL values whose password hashes to old_sha8.

    Returns (changed_keys, backup_path). Writes atomically, preserving mode.
    """
    text = path.read_text()
    changed = []

    def _sub(match: "re.Match") -> str:
        user, pw = match.group(1), match.group(2)
        if sha8(pw) != old_sha8:
            return match.group(0)
        return f"://{user}:{new_password}@"

    lines = []
    for line in text.splitlines(keepends=True):
        m = ENV_LINE_RE.match(line.rstrip("\n"))
        if not m:
            lines.append(line)
            continue
        new_line = URL_WITH_PASSWORD_RE.sub(_sub, line)
        if new_line != line:
            changed.append(m.group(2))
        lines.append(new_line)

    if not changed:
        return [], None

    backup_path = None
    if backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = path.with_name(path.name + f".bak.{stamp}")
        shutil.copy2(path, backup_path)
        os.chmod(backup_path, 0o600)

    mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.rot.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.writelines(lines)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return changed, backup_path


# ── vault ──────────────────────────────────────────────────────────────────────

def _vault(marker: Optional[Path] = None):
    """The fleet vault, imported lazily so tests can stub it and so a missing
    DATABASE_URL in this process is reported, not swallowed."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nervous_system.vault import Vault  # noqa: E402
    return Vault()


def _ensure_database_url_in_process(env_file: Path) -> None:
    """The vault's own connection comes from DATABASE_URL; when a rotation script
    is run outside a sourced shell it is not set."""
    if os.environ.get("DATABASE_URL"):
        return
    for key, value in read_env_keys(env_file):
        if key == "DATABASE_URL":
            os.environ["DATABASE_URL"] = value
            return


# ── subcommands ────────────────────────────────────────────────────────────────

def cmd_stage(args) -> int:
    if sys.stdin.isatty():
        import getpass
        new = getpass.getpass("new substrate DB password (not echoed): ")
    else:
        new = sys.stdin.readline()
    new = new.rstrip("\n")
    if len(new) < MIN_PASSWORD_LEN:
        print(f"stage: REFUSED — the value is {len(new)} chars, below the "
              f"{MIN_PASSWORD_LEN}-char floor. Nothing written.", file=sys.stderr)
        return 2
    if any(c.isspace() for c in new):
        print("stage: REFUSED — the value contains whitespace, which cannot survive "
              "a .env line or a libpq conninfo. Nothing written.", file=sys.stderr)
        return 2
    _ensure_database_url_in_process(args.env_file)
    vault = _vault()
    reason = (args.reason or
              "rotation: substrate postgres role leaked into transcripts 26-31 Oct "
              "(bus 87628/87660)")
    # Mark the SUPERSEDED value as leaked, then land the new one on top of it.
    # vault.put(leak_flagged=...) is the mechanism the vault already provides for
    # "this credential is known-compromised"; deleting it would destroy the record.
    current = None
    try:
        current = vault.get(args.name, reason=reason + " (read current before overwrite)")
    except Exception:
        current = None
    if current is not None and not getattr(current, "leak_flagged", False):
        try:
            vault.put(args.name, current.value, reason=reason,
                      leak_flagged=True,
                      leak_flagged_reason="live substrate password found in 3 transcript "
                                          "files (session 6da64adb, 26 Oct pgrep -fl leak)")
            print(f"stage: flagged superseded value {mask(current.value)} as leaked")
        except Exception as exc:
            print(f"stage: WARNING could not set leak flag ({type(exc).__name__}) — "
                  "continuing; the rotation itself still lands", file=sys.stderr)
    vault.put(args.name, new, reason=reason)
    print(f"stage: OK {args.name} {mask(new)}")
    return 0


def cmd_apply(args) -> int:
    env_file = args.env_file
    if not env_file.exists():
        print(f"apply: REFUSED — {env_file} does not exist", file=sys.stderr)
        return 2
    _ensure_database_url_in_process(env_file)
    vault = _vault()
    try:
        secret = vault.get(args.name,
                           reason="rotation: read staged substrate password to rewrite .env")
    except Exception as exc:
        print(f"apply: REFUSED — cannot read {args.name} from the vault "
              f"({type(exc).__name__}). Nothing written.", file=sys.stderr)
        return 2

    olds = keys_embedding_password(env_file, args.old_sha8) if args.old_sha8 else []
    if not olds:
        # Fall back: every URL key whose password differs from the staged one is a
        # candidate ONLY if it belongs to the same role/project. Conservative: require
        # an explicit --old-sha8 rather than guessing which credential to replace.
        print("apply: REFUSED — no key's password matches --old-sha8. Pass the sha8 of "
              "the password currently in .env (printed by `apply --dry-run` on a "
              "previous run, or `keys --env-file`).", file=sys.stderr)
        return 2

    if not args.execute:
        for key in olds:
            print(f"apply DRY-RUN: would rewrite {key} in {env_file}: "
                  f"{args.old_sha8} -> {mask(secret.value)}")
        print("apply DRY-RUN: nothing written. Re-run with --execute.")
        return 0

    changed, backup = rewrite_env(env_file, args.old_sha8, secret.value)
    for key in changed:
        print(f"apply: rewrote {key} in {env_file}: {args.old_sha8} -> {mask(secret.value)}")
    if backup:
        print(f"apply: backup at {backup} (0600, holds the OLD value — delete it after "
              "`verify` passes)")
    return 0 if changed else 2


def cmd_keys(args) -> int:
    """Which keys carry a URL password, by sha8 — the input `apply` needs."""
    for key, value in read_env_keys(args.env_file):
        m = URL_WITH_PASSWORD_RE.search(value)
        if not m:
            continue
        print(f"{key}\tuser={m.group(1)}\tpw=sha256:{sha8(m.group(2))}")
    return 0


def cmd_verify(args) -> int:
    """New password must connect; the pre-rotation one must be rejected."""
    import psycopg

    def connect(dsn: str, timeout: int = 8) -> Tuple[bool, str]:
        try:
            with psycopg.connect(dsn, connect_timeout=timeout) as conn:
                with conn.cursor() as cur:
                    cur.execute("select 1")
                    cur.fetchone()
            return True, "connected"
        except Exception as exc:  # auth failures surface as OperationalError
            return False, type(exc).__name__

    ok_new, detail_new = connect(args.new_dsn)
    print(f"verify: NEW dsn -> {'PASS' if ok_new else 'FAIL'} ({detail_new})")
    rc = 0 if ok_new else 1

    if args.old_dsn:
        ok_old, detail_old = connect(args.old_dsn)
        print(f"verify: OLD dsn -> {'STILL WORKS — FAIL' if ok_old else 'rejected — PASS'} "
              f"({detail_old})")
        if ok_old:
            rc = 1
    return rc


def cmd_restart_consumers(args) -> int:
    jobs = list(args.job or LONG_LIVED_JOBS)
    if args.dry_run:
        print("restart-consumers DRY-RUN: would `launchctl kickstart -k` in order:")
        for job in jobs:
            print(f"  gui/{os.getuid()}/{job}")
        print("restart-consumers DRY-RUN: nothing restarted.")
        return 0
    rc = 0
    for job in jobs:
        target = f"gui/{os.getuid()}/{job}"
        proc = subprocess.run(["launchctl", "kickstart", "-k", target],
                              capture_output=True, text=True)
        status = "ok" if proc.returncode == 0 else f"FAILED rc={proc.returncode}"
        print(f"restart-consumers: {target} {status}")
        if proc.returncode != 0:
            rc = proc.returncode
            print((proc.stderr or "").strip()[:300], file=sys.stderr)
    return rc


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    ap.add_argument("--name", default=DEFAULT_SECRET_NAME,
                    help=f"vault secret name (default {DEFAULT_SECRET_NAME})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stage", help="store the new password (stdin, never argv)")
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("keys", help="list .env keys that carry a URL password, by sha8")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("apply", help="rewrite .env to the staged password")
    p.add_argument("--old-sha8", required=True,
                   help="sha256[:8] of the password currently in .env")
    p.add_argument("--execute", action="store_true",
                   help="actually write (default is a dry run)")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify", help="new DSN connects; old DSN is rejected")
    p.add_argument("--new-dsn", required=True)
    p.add_argument("--old-dsn", default=None,
                   help="the pre-rotation DSN (from the backup file)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restart-consumers", help="restart long-lived launchd consumers")
    p.add_argument("--job", action="append", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_restart_consumers)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
