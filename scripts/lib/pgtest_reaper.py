"""Reap orphaned ephemeral test Postgres clusters left behind by tests/conftest.py `pg_dsn`.

Why (orch-console #77831, 2026-10-27): `pg_dsn` stops its cluster only in the fixture's
`finally:`. A pytest that is SIGKILLed or timed out never reaches it, and `pg_ctl start`
daemonizes the postmaster, so the cluster outlives the run under launchd. Three such
orphans were found on the Mini (up 18d / 5d / 3d, 0 clients each, ~0.6GB disk apiece)
during a 98%-full disk incident. conftest now also stops on atexit/SIGTERM, but SIGKILL
can't be caught, so every new pg_dsn session first sweeps stale clusters with this module.

A cluster is reaped only when ALL hold:
  * its datadir is named `wingmen-pgtest-*` (only ever created by pg_dsn),
  * its server log has been idle for >= max_idle_s (a live session's cluster logs
    checkpoints while it's in use),
  * it has 0 client backends -- or, if it can't be reached, its postmaster is not
    running (an unreachable LIVE postmaster is never touched: fail safe).
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time

DATADIR_PREFIX = "wingmen-pgtest-"
SOCKDIR_PREFIX = "wingmen-pgsock-"
DEFAULT_MAX_IDLE_S = int(float(os.environ.get("PGTEST_REAP_MAX_IDLE_H", "6")) * 3600)


def postmaster_info(datadir):
    """(pid, port, sockdir) from postmaster.pid, or None if the cluster isn't running."""
    try:
        lines = (pathlib.Path(datadir) / "postmaster.pid").read_text().splitlines()
        return int(lines[0].strip()), lines[3].strip(), lines[4].strip()
    except (OSError, IndexError, ValueError):
        return None


def pid_alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def client_backends(sockdir, port):
    """Number of OTHER client backends, or None if the cluster can't be reached."""
    try:
        import psycopg
        with psycopg.connect(host=sockdir, port=port, user="postgres", dbname="postgres",
                             connect_timeout=3) as conn:
            return conn.execute(
                "select count(*) from pg_stat_activity "
                "where backend_type = 'client backend' and pid <> pg_backend_pid()"
            ).fetchone()[0]
    except Exception:  # noqa: BLE001 — unreachable is a distinct answer, decided by caller
        return None


def _last_activity(datadir: pathlib.Path) -> float:
    log = datadir / "log"
    try:
        return log.stat().st_mtime
    except OSError:
        return datadir.stat().st_mtime


def stale_clusters(tmp_root, *, now=None, max_idle_s=DEFAULT_MAX_IDLE_S,
                   info=postmaster_info, clients=client_backends, alive=pid_alive):
    """Datadirs under `tmp_root` that are safe to stop + remove. Read-only."""
    now = time.time() if now is None else now
    out = []
    try:
        entries = sorted(pathlib.Path(tmp_root).iterdir())
    except OSError:
        return []
    for d in entries:
        if not d.name.startswith(DATADIR_PREFIX) or d.is_symlink() or not d.is_dir():
            continue
        try:
            if now - _last_activity(d) < max_idle_s:
                continue
        except OSError:
            continue
        pm = info(d)
        if pm is None:
            out.append(d)                      # no server running: dead leftover datadir
            continue
        pid, port, sockdir = pm
        n = clients(sockdir, port)
        if n == 0 or (n is None and not alive(pid)):
            out.append(d)
    return out


def stop_and_remove(datadir, pg_ctl, *, info=postmaster_info, alive=pid_alive,
                    run=subprocess.run, rmtree=shutil.rmtree) -> bool:
    """Stop the cluster (fast) and remove its datadir + socket dir. Returns False and leaves
    everything in place if a running postmaster could not be stopped."""
    datadir = pathlib.Path(datadir)
    pm = info(datadir)
    if pm is not None and alive(pm[0]):
        r = run([pg_ctl, "-D", str(datadir), "-w", "-m", "fast", "stop"],
                capture_output=True, text=True, env={**os.environ, "LC_ALL": "C", "LANG": "C"})
        if r.returncode != 0 and alive(pm[0]):
            return False
    rmtree(datadir)
    if pm is not None and pathlib.Path(pm[2]).name.startswith(SOCKDIR_PREFIX):
        rmtree(pm[2], ignore_errors=True)
    return True


def reap(tmp_root, pg_ctl, *, now=None, max_idle_s=DEFAULT_MAX_IDLE_S, info=postmaster_info,
         clients=client_backends, alive=pid_alive, run=subprocess.run,
         rmtree=shutil.rmtree) -> list:
    """Stop + remove every stale cluster under `tmp_root`; returns the reaped dir names."""
    reaped = []
    for d in stale_clusters(tmp_root, now=now, max_idle_s=max_idle_s, info=info,
                            clients=clients, alive=alive):
        try:
            if stop_and_remove(d, pg_ctl, info=info, alive=alive, run=run, rmtree=rmtree):
                reaped.append(d.name)
        except OSError:
            continue
    return reaped
