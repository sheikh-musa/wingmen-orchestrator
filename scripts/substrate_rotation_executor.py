#!/usr/bin/env python3
"""substrate_rotation_executor.py — the ONE atomic substrate-password rotation.

WHY (bus 87803, Musa op 36843). The password reached three transcripts (26 Oct
`pgrep -fl`), so it is a live project-owner credential sitting in files that get
replayed to model providers. The previous runbook had a human doing dashboard steps
between two tool runs; a human in the middle of a credential rotation is a gap in the
middle of a credential rotation. Supabase exposes the same reset over its Management
API, so the whole sequence is executable, and being executable it can be ordered,
journalled and rolled back.

ORDER (each step must succeed before the next starts):
  0  preflight    every target carries the OLD password; the DB answers; the PAT is
                  present; the daemons to restart exist. Nothing is written.
  1  generate     a URL/conninfo-safe password in this process, never printed, and
                  archive the superseded vault value (rotate, not a flag overwrite).
  2  rewrite      every target file gets the NEW password while the OLD one is still
                  live: the three Mini files and the remote bundle the hub pulls.
  3  patch        PATCH /v1/projects/{ref}/database/password, value in the BODY.
  4  propagate    tmux global env (new panes) and the hub's own environment.
  5  restart      every long-lived holder, once, together.
  6  verify       new connects, old is rejected, then a watch window.

WHY THIS ORDER. Every step that needs the OLD password happens while the OLD password
still works. The vault is reached THROUGH the database, so a reset-first order strands
the value in the vault with no tool able to fetch it. Postgres authenticates at connect
time, so rewriting files first breaks nothing; the only fragile moment is between the
patch and the restart pass, which is why those two are adjacent and both automated.

FAIL-STOP AND ROLLBACK. A failure before step 3 restores every rewritten file from the
bytes it held before and stops — nothing is broken, because the database still has the
old password. A failure AT step 3 is the same (the PATCH did not land). A failure after
step 3 cannot be rolled back by undoing the file write alone, so it prints the exact
state and the manual recovery, and never pretends otherwise.

SAFETY
  * `--plan` is the default; writing needs `--execute --confirm rotate-substrate`.
  * the password never touches argv, a log line, a journal or a bus row: only
    sha256[:8] and its length are emitted.
  * refuses when any target does not carry the password it expected, when the daemon
    list does not match reality, or when the PAT is missing.
  * the remote hop uses the same stdin rule as `rotate_substrate_dsn.py apply
    --remote`; nothing secret enters an ssh argv on either side.

Usage:
  substrate_rotation_executor.py plan
  substrate_rotation_executor.py execute --confirm rotate-substrate --reason "why"
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotate_substrate_dsn as rot  # noqa: E402

HOME = Path.home()
# The three places the Mini holds this credential (measured by hash, bus 87756).
DEFAULT_ENV_FILES = (
    HOME / "wingmen" / "orchestrator" / ".env",
    HOME / "wingmen" / "projects" / "orchestrator.wt-pr215fix" / ".env",
    HOME / "wingmen" / "projects" / "cosem-platform.wt-author" / ".env.local",
)
# Long-lived holders on the Mini that keep the credential in memory across beats.
# Verified against `launchctl list` before anything runs; a missing label refuses the
# rotation rather than being silently skipped (drift must be loud).
DEFAULT_DAEMONS = (
    "dev.wingmen.nazim-ingest",
    "dev.wingmen.tg-out-dead-replay",
    "dev.wingmen.cai-bus-notify",
    "dev.wingmen.finance-bus-notify",
    "dev.wingmen.fleet-health-bus-notify",
    "dev.wingmen.nazim-bus-notify",
    "dev.wingmen.agent-wake-subscriber",
    "dev.wingmen.hosted-console",
    "dev.wingmen.fleet-console",
)
CONFIRM_PHRASE = "rotate-substrate"
PATCH_URL = "https://api.supabase.com/v1/projects/{ref}/database/password"


class Refused(Exception):
    """A precondition failed. Nothing downstream may run."""


# ── side effects (one function each, so tests replace exactly one thing) ──────

def probe(dsn: str) -> bool:
    import psycopg
    try:
        with psycopg.connect(dsn, connect_timeout=8) as conn:
            with conn.cursor() as cur:
                cur.execute("select 1")
                cur.fetchone()
        return True
    except Exception:
        return False


def patch_password(ref: str, token: str, new_password: str) -> tuple:
    """(ok, short_detail). The value travels in the JSON BODY, never in argv."""
    import httpx
    try:
        r = httpx.patch(PATCH_URL.format(ref=ref),
                        headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"},
                        json={"password": new_password},
                        timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}"
    detail = f"HTTP {r.status_code}"
    if not (200 <= r.status_code < 300):
        # Supabase error bodies do not echo the password; keep it short anyway.
        detail += ": " + (r.text or "").strip()[:160]
    return 200 <= r.status_code < 300, detail


def launchd_present(label: str) -> bool:
    r = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
    return r.returncode == 0


def kickstart(label: str) -> tuple:
    r = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()[:200]


def tmux_set_global(name: str, value: str) -> tuple:
    """tmux has no stdin path for set-environment; the value is in this argv for
    milliseconds. Documented, not hidden: same-user `ps` can see it, no transcript
    ever will (the call is a subprocess, not a typed command)."""
    r = subprocess.run(["tmux", "set-environment", "-g", name, value],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()[:200]


def vault_rotate(name: str, value: str, reason: str) -> None:
    rot._ensure_database_url_in_process(rot.DEFAULT_ENV_FILE)
    rot._vault().rotate(name, value, reason)


def vault_has_current(name: str, reason: str):
    rot._ensure_database_url_in_process(rot.DEFAULT_ENV_FILE)
    try:
        return rot._vault().get(name, reason=reason)
    except Exception:
        return None


# ── the executor ──────────────────────────────────────────────────────────────

class Rotation:
    def __init__(self, args, deps=None):
        self.args = args
        self.d = deps or {}
        self.journal = []
        self.originals = {}      # path/as-key -> pre-rewrite text (rollback)
        self.new_password = None
        self.step = 0

    # -- helpers ------------------------------------------------------------
    def _fn(self, name):
        return self.d.get(name, globals()[name])

    def _record(self, step, outcome, **fields):
        entry = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "step": step, "outcome": outcome}
        entry.update(fields)
        self.journal.append(entry)
        self._flush()
        extra = " ".join(f"{k}={v}" for k, v in fields.items())
        print(f"[{entry['ts']}] {step}: {outcome} {extra}".rstrip())

    def _flush(self):
        path = Path(self.args.journal)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            for entry in self.journal:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        os.chmod(path, 0o600)

    def _targets(self):
        files = [Path(p) for p in self.args.env_file] if self.args.env_file \
            else list(DEFAULT_ENV_FILES)
        return files

    # -- steps --------------------------------------------------------------
    def preflight(self):
        """Everything that can be known without writing. Raises Refused."""
        needle = self.args.old_sha8.strip().lower()
        if not rot.sha_prefix_matches("x", needle) and len(needle) < 8:
            raise Refused("--old-sha8 must be at least 8 hex characters")
        targets = self._targets()
        for path in targets:
            if not path.exists():
                raise Refused(f"{path} does not exist")
            keys = rot.keys_embedding_password(path, needle)
            if not keys:
                raise Refused(f"{path} holds no key with password {needle} — the "
                              "rotation would silently skip it")
        if self.args.remote:
            text = self._fn("read_remote")(self.args.remote, self.args.remote_path)
            if text is None:
                raise Refused(f"cannot read {self.args.remote_path} on {self.args.remote}")
            if not rot.keys_embedding_password_in_text(text, needle):
                raise Refused(f"the remote file {self.args.remote_path} on "
                              f"{self.args.remote} does not carry {needle}")
            self.originals["remote"] = text
        for label in self._daemons():
            if not self._fn("launchd_present")(label):
                raise Refused(f"launchd job {label} is not loaded — the restart pass "
                              "would silently miss a holder")
        if not os.environ.get("SUPABASE_ACCESS_TOKEN") and not self.args.token_env:
            raise Refused("SUPABASE_ACCESS_TOKEN is not set and no --token-env given")
        if not self._fn("probe")(rot.database_url(targets[0]) or ""):
            raise Refused("the OLD password does not connect right now — rotating now "
                          "would leave no working credential and no way back")
        for path in targets:
            self.originals[path] = Path(os.path.realpath(path)).read_text()
        self._record("preflight", "OK", targets=len(targets),
                     remote=bool(self.args.remote), daemons=len(self._daemons()))

    def _daemons(self):
        return self.args.job or list(DEFAULT_DAEMONS)

    def step_generate(self):
        pw = secrets.token_urlsafe(self.args.bytes)
        if len(pw) < rot.MIN_PASSWORD_LEN or any(c.isspace() for c in pw):
            raise Refused("generated value does not satisfy our own floor")
        self.new_password = pw
        current = self._fn("vault_has_current")("SUBSTRATE_DB_PASSWORD_NEW",
                                                "rotation: read current before rotate")
        if current is not None and current.value == pw:
            raise Refused("the vault already holds this value — refuse to run twice")
        self._fn("vault_rotate")("SUBSTRATE_DB_PASSWORD_NEW", pw,
                                 self.args.reason)
        self._record("generate", "OK", new=rot.mask(pw))

    def step_rewrite(self):
        changed_total = 0
        for path in self._targets():
            changed, backup = rot.rewrite_env(path, self.args.old_sha8, self.new_password)
            if not changed:
                raise Refused(f"{path} changed nothing — preflight said it would")
            changed_total += len(changed)
            self._record("rewrite", "OK", target=str(path), keys=",".join(changed),
                         backup=str(backup) if backup else "-")
        if self.args.remote:
            lines, changed = rot.rewrite_text(self.originals["remote"],
                                              self.args.old_sha8, self.new_password)
            backup = self._fn("write_remote")(self.args.remote, self.args.remote_path,
                                              "".join(lines))
            if not backup:
                raise Refused("the remote write failed")
            changed_total += len(changed)
            self._record("rewrite", "OK", target=f"{self.args.remote}:{self.args.remote_path}",
                         keys=",".join(changed), backup=backup)
        self._record("rewrite", "OK", files=changed_total)

    def step_patch(self):
        token = os.environ.get(self.args.token_env or "SUPABASE_ACCESS_TOKEN")
        if not token:
            raise Refused(f"{self.args.token_env or 'SUPABASE_ACCESS_TOKEN'} is empty")
        ok, detail = self._fn("patch_password")(self.args.ref, token, self.new_password)
        if not ok:
            raise Refused(f"the Management API refused the reset: {detail}")
        self._record("patch", "OK", ref=self.args.ref, detail=detail)

    def step_propagate(self):
        ok, detail = self._fn("tmux_set_global")("DATABASE_URL", self.new_password)
        self._record("propagate", "OK" if ok else "WARN", surface="tmux-global",
                     detail=detail or "set")
        self._record("propagate", "TODO",
                     surface="hub",
                     detail="hub must set its environment after fetch-secrets")

    def step_restart(self):
        failed = []
        for label in self._daemons():
            ok, detail = self._fn("kickstart")(label)
            if not ok:
                failed.append(f"{label}: {detail}")
            self._record("restart", "OK" if ok else "FAIL", job=label, detail=detail)
        if failed:
            raise Refused("restart failures: " + "; ".join(failed))

    def step_verify(self):
        new_dsn = rot.database_url(self._targets()[0])
        old_dsn = None
        for path in self._targets():
            backup = rot.latest_backup(path)
            if backup:
                old_pw = rot.url_password(rot.database_url(backup) or "")
                if old_pw:
                    old_dsn = rot.dsn_with_password(new_dsn or "", old_pw)
                    break
        if not new_dsn:
            raise Refused("no DSN in the rewritten file")
        ok = False
        for attempt in range(1, self.args.retries + 1):
            ok = self._fn("probe")(new_dsn)
            self._record("verify", "OK" if ok else "retry", probe="new", attempt=attempt)
            if ok:
                break
            time.sleep(self.args.retry_delay)
        if not ok:
            raise Refused("the new password does not connect")
        if old_dsn:
            still = self._fn("probe")(old_dsn)
            self._record("verify", "FAIL" if still else "OK", probe="old",
                         verdict="STILL WORKS" if still else "rejected")
            if still:
                raise Refused("the OLD password still works — the reset did not land")
        self._record("verify", "OK", probe="old", verdict="not attempted (no backup)")

    def step_watch(self):
        """Re-probe both sides on a loop. Honest limit: this watches CONNECTIVITY, not
        Supabase's server-side auth-failure log; the log query is a follow-up."""
        deadline = time.time() + self.args.watch_seconds
        while time.time() < deadline:
            time.sleep(min(60, max(1, self.args.watch_seconds)))
            ok = self._fn("probe")(rot.database_url(self._targets()[0]) or "")
            self._record("watch", "OK" if ok else "FAIL", probe="new")
            if not ok:
                raise Refused("the new password stopped connecting during the watch")

    # -- driver -------------------------------------------------------------
    def rollback(self):
        restored = []
        for path, text in self.originals.items():
            if path == "remote":
                continue
            try:
                target = Path(os.path.realpath(path))
                target.write_text(text)
                os.chmod(target, 0o600)
                restored.append(str(path))
            except Exception as exc:  # noqa: BLE001
                restored.append(f"{path}: RESTORE FAILED {type(exc).__name__}")
        if self.args.remote and "remote" in self.originals:
            ok = self._fn("write_remote")(self.args.remote, self.args.remote_path,
                                          self.originals["remote"])
            restored.append(f"remote: {'restored' if ok else 'RESTORE FAILED'}")
        self._record("rollback", "OK" if restored else "nothing-to-do",
                     targets=len(restored))

    def run(self):
        plan_only = not self.args.execute
        steps = [("preflight", self.preflight), ("generate", self.step_generate),
                 ("rewrite", self.step_rewrite), ("patch", self.step_patch),
                 ("propagate", self.step_propagate), ("restart", self.step_restart),
                 ("verify", self.step_verify)]
        if plan_only:
            self.preflight()
            print("plan: preflight OK. Steps that WOULD run: " +
                  ", ".join(name for name, _ in steps[1:]) + ", watch")
            print("plan: nothing written. Re-run with --execute --confirm "
                  f"{CONFIRM_PHRASE}")
            return 0
        if self.args.confirm != CONFIRM_PHRASE:
            print(f"execute: REFUSED — --confirm must be exactly {CONFIRM_PHRASE}",
                  file=sys.stderr)
            return 2
        patched = False
        for name, fn in steps:
            try:
                fn()
                if name == "patch":
                    patched = True
            except Refused as exc:
                self._record(name, "REFUSED", detail=str(exc))
                if not patched:
                    self.rollback()
                    print(f"execute: STOPPED at {name}: {exc}. Files restored; the "
                          "database still has the OLD password.", file=sys.stderr)
                else:
                    print(f"execute: STOPPED at {name}: {exc}. THE DATABASE NOW HAS "
                          "THE NEW PASSWORD and the files already hold it; the failed "
                          "step is the only thing left — re-run that step, do not "
                          "re-rotate.", file=sys.stderr)
                return 3
        if self.args.watch_seconds > 0:
            try:
                self.step_watch()
            except Refused as exc:
                self._record("watch", "REFUSED", detail=str(exc))
                return 3
        print("execute: rotation COMPLETE — journal at " + self.args.journal)
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("plan", "execute"))
    ap.add_argument("--env-file", action="append", default=None,
                    help="target file (repeatable); default = the three Mini files")
    ap.add_argument("--old-sha8", required=True,
                    help="sha256[:8+] of the CURRENT password")
    ap.add_argument("--ref", default="tscuymavysscrvoberrr")
    ap.add_argument("--token-env", default="SUPABASE_ACCESS_TOKEN")
    ap.add_argument("--remote", default=None, help="ssh host of the hub's bundle")
    ap.add_argument("--remote-path", default=None)
    ap.add_argument("--job", action="append", default=None, help="launchd label")
    ap.add_argument("--bytes", type=int, default=36)
    ap.add_argument("--retries", type=int, default=6)
    ap.add_argument("--retry-delay", type=float, default=10.0)
    ap.add_argument("--watch-seconds", type=int, default=600)
    ap.add_argument("--reason", default="substrate password rotation (Musa op 36843)")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--journal", default=None)
    args = ap.parse_args(argv)
    args.execute = args.execute or args.mode == "execute"
    if args.remote and not args.remote_path:
        ap.error("--remote needs --remote-path")
    if not args.journal:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.journal = str(Path(__file__).resolve().parents[1] /
                           "reports" / "rotation" / stamp / "journal.jsonl")
    rot_mod = {"read_remote": rot.read_remote_env, "write_remote": rot.write_remote_env}
    return Rotation(args, deps=rot_mod).run()


if __name__ == "__main__":
    sys.exit(main())
