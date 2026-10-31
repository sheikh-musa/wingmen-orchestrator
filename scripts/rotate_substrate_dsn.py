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
  1. this tool: `stage`   (paste the new value on stdin; the vault write needs a
                          connection, so this happens while the OLD password works)
  2. this tool: `apply --execute`   (.env now holds the new value)
  3. dashboard: reset the DB password for the substrate project
  4. this tool: `restart-consumers`  (new connections must carry the new value)
  5. this tool: `verify`   (new DSN connects, old one is rejected)
  6. scrub the old value out of the transcripts (scripts/secret_hash_sweep.py)

`verify` NEVER TAKES A DSN, in any mode. It reads the new one out of `--env-file`
and reconstructs the pre-rotation one from the newest `.env.bak.*` sitting beside
it. That is not a convenience. The first draft wanted both DSNs on the command
line, which is precisely how this credential leaked on 26 Oct — a `pgrep -fl` read
a process's argv and the password went into three transcripts. A tool that exists
to stop that cannot itself offer to do it (cc-quality 87760 F1).

AND IT NEVER SKIPS THE OLD PROBE (87760 F2, console 87773). No backup, an
unparseable backup, or a backup that still holds the current password all REFUSE.
A check that only proves the new password connects is a connection test; the half
that matters after a reset is that the old password is dead.

WHY `verify` RETRIES THE NEW DSN. Supabase's own KB (28P01 after a password
rotation) says Supavisor caches database credentials briefly: with active pooled
connections it usually refreshes in about 15 seconds, but an idle pool refreshes
only when a real reconnect attempt happens. So the first attempts can fail with a
plain "password authentication failed" even though the value is correct, and a
single-shot probe would report a false FAIL at exactly the moment the operator is
watching. Failures are retried with a delay and reported with their attempt count.

The OLD probe is deliberately NOT retried, and its verdict is trusted only when
the NEW probe succeeded in the same run: during a pooler cache window everything
is rejected, and "the old password is rejected" is indistinguishable from "the
pooler is rejecting everything right now". A clean new connection is what makes
the old rejection mean something.

WHY APPLY COMES BEFORE THE RESET. The vault is reached through DATABASE_URL, the
same credential being rotated, so after the reset `apply` could no longer read the
value it is supposed to write — a chicken-and-egg that strands the fleet with a
dead password in .env and no tool that can fix it. Running apply first removes it:
stage and apply both talk to a still-working DSN, and the reset (step 3) is a
single click away from a .env that already holds the new value. Existing
connections survive a password change — Postgres authenticates at connect time —
so nothing actually breaks until new connections are attempted, which is step 4.
Do steps 2-4 in one sitting; a long gap leaves .env holding a password the
database does not accept yet.

SAFETY
  * refuses to write a value shorter than MIN_PASSWORD_LEN
  * refuses to touch a key it cannot parse; never rewrites a file it did not change
  * `--dry-run` is the default for `apply`; writing needs `--execute`
  * no subcommand accepts a DSN or a password on the command line
  * never prints, echoes or logs a password — hash prefix and length only
  * the `.env` selector is a sha256 PREFIX (8-64 hex; `keys` prints 16). Anything
    empty, short or non-hex matches nothing, so a bad selector can never rewrite a
    file wholesale. 8 hex is accepted because an operator may already hold one.
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
import time
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


def sha_prefix_matches(password: str, prefix: str) -> bool:
    """Does this password's sha256 hex start with `prefix`?

    The selector is a HASH PREFIX, so it keys the decision on the secret without any
    command line or log line ever carrying the value. 8 hex chars (the historical
    `keys` output, and what an operator may already have in hand) is accepted, but
    anything shorter, longer than the digest, or non-hex matches NOTHING — an empty
    prefix would otherwise match every password and rewrite the whole file.
    """
    needle = (prefix or "").strip().lower()
    if not (8 <= len(needle) <= 64) or any(c not in "0123456789abcdef" for c in needle):
        return False
    return hashlib.sha256(password.encode()).hexdigest().startswith(needle)


def keys_embedding_password(path: Path, password_prefix: str) -> list:
    """Keys whose URL value carries a password matching this sha256 prefix."""
    found = []
    for key, value in read_env_keys(path):
        m = URL_WITH_PASSWORD_RE.search(value)
        if m and sha_prefix_matches(m.group(2), password_prefix):
            found.append(key)
    return found


def database_url(path: Path) -> Optional[str]:
    """The DATABASE_URL value from an env file, or None when it carries no URL."""
    for key, value in read_env_keys(path):
        if key == "DATABASE_URL" and URL_WITH_PASSWORD_RE.search(value):
            return value
    return None


def url_password(value: str) -> Optional[str]:
    m = URL_WITH_PASSWORD_RE.search(value or "")
    return m.group(2) if m else None


def dsn_with_password(dsn: str, password: str) -> str:
    """The same DSN with its password swapped — topology untouched.

    Used to rebuild the pre-rotation DSN from the backup: the backup differs from
    the live file only in the password `apply` replaced, so splicing the backup's
    password into the live URL reproduces exactly the URL that was live before.
    """
    return URL_WITH_PASSWORD_RE.sub(
        lambda m: f"://{m.group(1)}:{password}@", dsn, count=1)


def latest_backup(env_file: Path) -> Optional[Path]:
    """The newest `.env.bak.<timestamp>` sibling, or None.

    `rewrite_env` stamps backups as .bak.<UTC timestamp>, so a name sort is a
    time sort. Backups are written through the resolved path, hence the realpath.
    """
    target = Path(os.path.realpath(env_file))
    backups = sorted(target.parent.glob(target.name + ".bak.*"))
    return backups[-1] if backups else None


def rewrite_env(path: Path, old_sha8: str, new_password: str,
                backup: bool = True) -> Tuple[list, Optional[Path]]:
    """Replace the password inside URL values whose password hashes to old_sha8.

    Returns (changed_keys, backup_path). Writes atomically, preserving mode.

    SYMLINKS: several hosts keep the live `.env` as a link to a shared copy. Writing
    through `os.replace` on the LINK PATH would swap the symlink for a regular file
    and silently detach the host from its shared env — so every filesystem operation
    below happens on the resolved target while the link stays a link.
    """
    target = Path(os.path.realpath(path))
    text = target.read_text()
    changed = []

    def _sub(match: "re.Match") -> str:
        user, pw = match.group(1), match.group(2)
        if not sha_prefix_matches(pw, old_sha8):
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
        backup_path = target.with_name(target.name + f".bak.{stamp}")
        shutil.copy2(target, backup_path)
        os.chmod(backup_path, 0o600)

    mode = target.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".env.rot.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.writelines(lines)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
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

class _Secret:
    """Minimal stand-in for VaultSecret when the value did not come from the vault."""

    def __init__(self, value: str):
        self.value = value
        self.leak_flagged = False
        self.leak_reason = None


def _read_password_from_stdin() -> Optional[str]:
    if sys.stdin.isatty():
        import getpass
        value = getpass.getpass("new substrate DB password (not echoed): ")
    else:
        value = sys.stdin.readline()
    value = value.rstrip("\n")
    if len(value) < MIN_PASSWORD_LEN:
        print(f"apply: REFUSED — the value on stdin is {len(value)} chars, below the "
              f"{MIN_PASSWORD_LEN}-char floor. Nothing written.", file=sys.stderr)
        return None
    if any(c.isspace() for c in value):
        print("apply: REFUSED — the value on stdin contains whitespace. Nothing written.",
              file=sys.stderr)
        return None
    return value


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
    # Validate the selector BEFORE reading any secret: a bad prefix (empty matches
    # everything) must never get as far as a stdin read or a vault call.
    needle = (args.old_sha8 or "").strip().lower()
    if not (8 <= len(needle) <= 64) or any(c not in "0123456789abcdef" for c in needle):
        print("apply: REFUSED — --old-sha8 must be 8 to 64 hex characters of the sha256 "
              "of the CURRENT password. Nothing written.", file=sys.stderr)
        return 2
    if args.from_stdin:
        # Escape hatch for the case the runbook exists to avoid: the dashboard reset
        # already landed before `apply` ran, so DATABASE_URL is dead and the vault —
        # which is reached THROUGH it — cannot be read. Same no-print guarantee: the
        # value arrives on stdin and only its sha8 leaves.
        value = _read_password_from_stdin()
        if value is None:
            return 2
        secret = _Secret(value)
    else:
        _ensure_database_url_in_process(env_file)
        vault = _vault()
        try:
            secret = vault.get(args.name,
                               reason="rotation: read staged substrate password to rewrite .env")
        except Exception as exc:
            print(f"apply: REFUSED — cannot read {args.name} from the vault "
                  f"({type(exc).__name__}). Nothing written. If the dashboard reset "
                  "already happened, DATABASE_URL is dead and the vault cannot be "
                  "reached through it — re-run with --from-stdin and paste the new "
                  "value.", file=sys.stderr)
            return 2

    olds = keys_embedding_password(env_file, needle)
    if not olds:
        # Conservative: require an explicit --old-sha8 rather than guessing which
        # credential to replace.
        print("apply: REFUSED — no key's password matches --old-sha8. Pass the hash "
              "prefix of the password currently in .env (from `keys`, which prints 16 "
              "hex chars; a shorter prefix is accepted). Nothing written.",
              file=sys.stderr)
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
    """Which keys carry a URL password, by hash prefix — the input `apply` needs.

    Prints 16 hex chars, not 8: the selector decides which credential gets
    rewritten, and 8 hex is 32 bits — a second key whose password collided there
    would be silently rewritten too (cc-quality 87760 N3). `apply` still accepts a
    shorter prefix for a value the operator already has printed.
    """
    for key, value in read_env_keys(args.env_file):
        m = URL_WITH_PASSWORD_RE.search(value)
        if not m:
            continue
        digest = hashlib.sha256(m.group(2).encode()).hexdigest()[:16]
        print(f"{key}\tuser={m.group(1)}\tpw=sha256:{digest}")
    return 0


def _probe(psycopg, dsn: str, timeout: int = 8) -> Tuple[bool, str]:
    """One connection attempt. Returns (ok, short reason). Never echoes the DSN."""
    try:
        with psycopg.connect(dsn, connect_timeout=timeout) as conn:
            with conn.cursor() as cur:
                cur.execute("select 1")
                cur.fetchone()
        return True, "connected"
    except Exception as exc:  # auth failures surface as OperationalError
        return False, type(exc).__name__


def _verify_refuse(reason: str) -> None:
    print(f"verify: REFUSED — {reason}", file=sys.stderr)


def _verify_dsns(args) -> Tuple[Optional[str], Optional[str]]:
    """(new_dsn, old_dsn), or (None, None) with a printed refusal.

    NEITHER DSN COMES FROM ARGV (cc-quality 87760 F1). Both already sit on disk: the
    new one in the env file `apply` rewrote, the old one in the 0600 backup it left
    beside it. Accepting them as arguments would put the just-rotated password back
    into ps, shell history and any transcript — which is the leak this whole rotation
    exists to close.

    THE OLD-DSN PROBE IS MANDATORY (87760 F2, console 87773). A verify that cannot
    prove the pre-rotation password is dead only proves the new one connects, and the
    whole point of a rotation is the other half. Missing backup, unparseable backup,
    or a backup that still holds the current password all REFUSE rather than degrade.
    """
    if not args.env_file.exists():
        _verify_refuse(f"{args.env_file} does not exist")
        return None, None
    new_dsn = database_url(args.env_file)
    if new_dsn is None:
        _verify_refuse(f"{args.env_file} has no DATABASE_URL with an embedded password")
        return None, None
    print(f"verify: new DSN from {args.env_file} pw={mask(url_password(new_dsn))}")

    backup = latest_backup(args.env_file)
    if backup is None:
        _verify_refuse(
            f"no {args.env_file.name}.bak.* beside {args.env_file}. apply writes one; "
            "without it there is no pre-rotation password to prove dead, so a PASS here "
            "would only say the new password connects")
        return None, None
    old_pw = url_password(database_url(backup) or "")
    if old_pw is None:
        _verify_refuse(f"{backup.name} carries no DATABASE_URL password to test")
        return None, None
    if sha8(old_pw) == sha8(url_password(new_dsn) or ""):
        _verify_refuse(
            f"{backup.name} holds the SAME password as {args.env_file.name} — apply did "
            "not run (or ran twice), so there is no pre-rotation value to test")
        return None, None
    print(f"verify: old DSN rebuilt from {backup.name} pw={mask(old_pw)}")
    return new_dsn, dsn_with_password(new_dsn, old_pw)


def cmd_verify(args) -> int:
    """New password must connect; the pre-rotation one must be rejected."""
    import psycopg

    new_dsn, old_dsn = _verify_dsns(args)
    if new_dsn is None or old_dsn is None:
        return 2

    attempts = max(1, int(args.retries))
    ok_new, detail_new = False, "not attempted"
    for attempt in range(1, attempts + 1):
        ok_new, detail_new = _probe(psycopg, new_dsn)
        if ok_new:
            break
        if attempt < attempts:
            print(f"verify: NEW attempt {attempt}/{attempts} failed ({detail_new}) — the "
                  "pooler caches credentials for a few seconds after a reset; retrying in "
                  f"{args.retry_delay:g}s")
            time.sleep(max(0.0, float(args.retry_delay)))

    print(f"verify: NEW dsn -> {'PASS' if ok_new else 'FAIL'} ({detail_new})"
          + (f" after {attempts} attempt(s)" if attempts > 1 else ""))
    rc = 0 if ok_new else 1

    if not ok_new:
        print("verify: OLD probe not attempted — while the pooler is still rejecting "
              "everything, a rejection would read as a PASS. Fix the new connection first.")
        return rc

    ok_old, detail_old = _probe(psycopg, old_dsn)
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
    p.add_argument("--from-stdin", action="store_true",
                   help="read the new password from stdin instead of the vault — only "
                        "for the case where the dashboard reset already landed and the "
                        "vault is unreachable through the dead DSN")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser(
        "verify",
        help="new DSN connects AND old DSN is rejected (both read from disk, never argv)")
    p.add_argument("--retries", type=int, default=3,
                   help="attempts for the NEW probe, spaced by --retry-delay "
                        "(the pooler caches credentials ~15s after a reset)")
    p.add_argument("--retry-delay", type=float, default=5.0)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restart-consumers", help="restart long-lived launchd consumers")
    p.add_argument("--job", action="append", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_restart_consumers)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
