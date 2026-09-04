#!/usr/bin/env python3
"""Fleet-boot trust-gate preflight (Nazim bus 37420/37425 — SRE fleet-boot domain).

A Claude Code auto-update reset the folder-TRUST first-run prompt. Any lane that
reboots via launch_dangerous_cc.sh in a cwd NOT marked hasTrustDialogAccepted:true
in ~/.claude.json wedges at "Is this a project you trust?", cannot proceed
(--dangerously-skip-permissions does NOT auto-answer it), and is killed at ~48s ->
exit 1 -> CRASH LOOP.

This preflight pre-seeds trust for the LAUNCHING lane's OWN resolved cwd, right
before claude exec's, so the lane never wedges. Design (matches Nazim's 5 gate
conditions, 37425):

  1. CONCURRENCY: flock a dedicated lockfile across the whole read-modify-write so
     two simultaneous lane boots (lanes.sh up / fleet recovery) serialize — the
     naive whole-file temp+rename races (both read, both add, 2nd rename drops the
     1st's entry). The lock is the PRIMARY guard; idempotent re-seed only softens.
  2. Seeds only the EXACT resolved cwd's entry.
  3. Backup + JSON re-validate the rendered bytes BEFORE the atomic replace — never
     leave a malformed ~/.claude.json (that bricks EVERY cc on the box).
  4. Idempotent: already-trusted -> no write, no backup, changed=False.
  5. FAIL-LOUD: any read/parse/write/validate failure RAISES (CLI exits non-zero)
     so the launcher ABORTS rather than launching into a cwd that silently
     48s-crash-loops. A loud abort beats a silent brick.

Pure core `seed_trust()` is unit-tested without IO; `ensure_trusted()` wraps it in
the locked atomic IO. Never mutates the file when nothing changed.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time


def seed_trust(cfg: dict, cwd: str) -> "tuple[dict, bool]":
    """PURE. Return (new_cfg, changed). Ensures cfg['projects'][cwd]
    ['hasTrustDialogAccepted'] is True, preserving every other key. changed is
    False iff it was already exactly True (idempotent)."""
    projects = cfg.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    entry = projects.get(cwd)
    if not isinstance(entry, dict):
        entry = {}
    if entry.get("hasTrustDialogAccepted") is True and cwd in projects and "projects" in cfg:
        return cfg, False
    new_entry = dict(entry)
    already = new_entry.get("hasTrustDialogAccepted") is True
    new_entry["hasTrustDialogAccepted"] = True
    new_projects = dict(projects)
    new_projects[cwd] = new_entry
    new_cfg = dict(cfg)
    new_cfg["projects"] = new_projects
    # changed iff we actually flipped the flag or created the entry/projects key
    changed = not (already and cwd in projects and isinstance(cfg.get("projects"), dict))
    return new_cfg, changed


def _lockpath(config_path: str) -> str:
    return config_path + ".trustlock"


def ensure_trusted(config_path: str, cwd: str, _lock_timeout_s: float = 10.0) -> dict:
    """Locked, atomic, backed-up seed of `cwd` trust into `config_path`.

    Returns {"changed": bool, "already": bool, "backup": str|None}. RAISES on any
    failure (missing-but-unreadable, corrupt JSON, write/validate/replace error) —
    fail-loud so the caller aborts the launch (gate condition 5). A config file
    that does not exist yet is created minimally (fresh machine / first boot).
    """
    config_path = os.path.abspath(os.path.expanduser(config_path))
    lockpath = _lockpath(config_path)
    lockfd = os.open(lockpath, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        # Block for the lock (bounded), so concurrent lane boots serialize.
        deadline = None
        while True:
            try:
                fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if deadline is None:
                    deadline = _lock_timeout_s
                deadline -= 0.1
                if deadline <= 0:
                    raise TimeoutError(f"could not acquire {lockpath} within {_lock_timeout_s}s")
                time.sleep(0.1)

        # READ (inside the lock so we can't lose a concurrent writer's entry).
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                raw = f.read()
            try:
                cfg = json.loads(raw)
            except json.JSONDecodeError as e:
                # NEVER blindly clobber a config we can't parse — fail loud, leave intact.
                raise ValueError(f"~/.claude.json is not valid JSON ({e}); refusing to touch it") from e
            if not isinstance(cfg, dict):
                raise ValueError("~/.claude.json top-level is not an object; refusing to touch it")
        else:
            cfg = {}
            raw = None

        new_cfg, changed = seed_trust(cfg, cwd)
        already = not changed
        if not changed:
            return {"changed": False, "already": True, "backup": None}

        # RENDER + VALIDATE the bytes before they ever touch the real path.
        rendered = json.dumps(new_cfg, indent=2)
        json.loads(rendered)  # re-parse guard — never write bytes we can't read back

        # BACKUP the current file (only when we actually change it).
        backup = None
        if raw is not None:
            backup = f"{config_path}.trustbak.{int(time.time())}"
            with open(backup, "w") as bf:
                bf.write(raw)

        # ATOMIC replace: write temp in the same dir, fsync, os.replace.
        d = os.path.dirname(config_path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".claude.json.tmp.")
        try:
            with os.fdopen(fd, "w") as tf:
                tf.write(rendered)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp, config_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return {"changed": True, "already": already, "backup": backup}
    finally:
        try:
            fcntl.flock(lockfd, fcntl.LOCK_UN)
        finally:
            os.close(lockfd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pre-seed folder-trust for a lane's cwd before claude launches.")
    ap.add_argument("--cwd", required=True, help="the resolved CALLER_DIR claude will run in")
    ap.add_argument("--config", default=os.path.expanduser("~/.claude.json"), help="path to ~/.claude.json")
    args = ap.parse_args(argv)
    cwd = os.path.abspath(os.path.expanduser(args.cwd))
    try:
        res = ensure_trusted(args.config, cwd)
    except Exception as e:  # noqa: BLE001 — fail LOUD; caller aborts the launch
        print(f"[trust-preflight] FATAL: could not ensure trust for {cwd}: {e}", file=sys.stderr)
        return 1
    if res["changed"]:
        print(f"[trust-preflight] seeded hasTrustDialogAccepted=true for {cwd} (backup: {res['backup']})")
    else:
        print(f"[trust-preflight] already trusted: {cwd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
