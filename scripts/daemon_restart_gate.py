#!/usr/bin/env python3
"""daemon_restart_gate -- "catch A" for backlog#124's DAEMONS bucket (orch-console
ruling, bus #74954/#75001). Migration 119's release is gated on confirming every
LIVE agent_messages-writing daemon is actually RUNNING its migrated code, not just
that the source is clean -- a long-running process keeps its old in-memory code
until restarted, so "source migrated" != "safe" (console's own framing).

Compares a launchd-managed daemon's process START TIME against the on-disk mtime
of its watched source files:
  - process started AFTER the newest watched mtime -> running current code (PASS).
  - process started BEFORE the newest watched mtime -> it predates the latest
    on-disk change and is still running stale code in memory (FAIL -- needs a
    bounce, e.g. `launchctl kickstart -k gui/<uid>/<label>`).
  - no running PID at all -> a hard FAIL for an always-on daemon that's
    unexpectedly down, UNLESS --pre-deployment is given: a daemon not yet
    deployed has nothing to bounce -- its first launch inherently runs current
    code (lane_wedge_watchdog's status per orch-console, bus #75001).

Read-only: never bounces, kills, or restarts anything itself -- it only reports,
so the operator decides when to actually bounce a live daemon.

Usage:
  scripts/daemon_restart_gate.py <launchd-label> --source <file> [--source <file> ...] \\
      [--pre-deployment] [--json]

Exit codes:
  0 -- confirmed running current code (or pre-deployment-satisfied)
  1 -- stale (needs a bounce) or unexpectedly not running
  2 -- usage error
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

_PID_RE = re.compile(r'"PID"\s*=\s*(\d+)')
_LSTART_FMT = "%a %b %d %H:%M:%S %Y"
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class RestartGateResult:
    ok: bool
    status: str  # RUNNING_FRESH | STALE_NEEDS_RESTART | NOT_RUNNING | PRE_DEPLOYMENT_SATISFIED
    message: str


def launchd_pid(label: str, *, run=subprocess.run) -> int | None:
    """The PID launchd reports for `label`, or None if not loaded, loaded-but-not-
    running (no "PID" key in `launchctl list <label>`'s dict dump), or launchctl
    doesn't recognize the label at all (nonzero exit)."""
    proc = run(["launchctl", "list", label], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    m = _PID_RE.search(proc.stdout)
    return int(m.group(1)) if m else None


def process_start_epoch(pid: int, *, run=subprocess.run) -> float | None:
    """macOS `ps -o lstart= -p <pid>` -> epoch seconds, or None if the pid doesn't
    exist (a race between launchctl reporting it and this lookup)."""
    proc = run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        return None
    # macOS space-pads a single-digit day-of-month -- collapse repeated spaces
    # before parsing with a single-space format string.
    normalized = _WS_RE.sub(" ", out)
    dt = datetime.datetime.strptime(normalized, _LSTART_FMT)
    return dt.timestamp()


def source_mtime(paths: list[str]) -> float:
    return max(os.path.getmtime(p) for p in paths)


def _fmt(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def check(label: str, source_paths: list[str], *, pre_deployment: bool = False,
          run=subprocess.run) -> RestartGateResult:
    pid = launchd_pid(label, run=run)
    if pid is None:
        if pre_deployment:
            return RestartGateResult(
                ok=True, status="PRE_DEPLOYMENT_SATISFIED",
                message=f"{label}: not currently running (pre-deployment) -- its first "
                        f"launch inherently runs current code, nothing to bounce.")
        return RestartGateResult(
            ok=False, status="NOT_RUNNING",
            message=f"{label}: expected a live daemon but launchctl reports no running PID.")

    started = process_start_epoch(pid, run=run)
    if started is None:
        return RestartGateResult(
            ok=False, status="NOT_RUNNING",
            message=f"{label}: launchctl reported pid {pid} but it no longer exists "
                    f"(race) -- re-run this check.")

    mtime = source_mtime(source_paths)
    if started > mtime:
        return RestartGateResult(
            ok=True, status="RUNNING_FRESH",
            message=f"{label}: pid {pid} started {_fmt(started)}, after the newest "
                    f"watched source changed {_fmt(mtime)} -- running current code.")
    return RestartGateResult(
        ok=False, status="STALE_NEEDS_RESTART",
        message=f"{label}: pid {pid} started {_fmt(started)}, BEFORE the newest watched "
                f"source changed {_fmt(mtime)} -- still running pre-change code in "
                f"memory. Bounce it (`launchctl kickstart -k gui/$(id -u)/{label}`) and "
                f"re-run this check.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("label", help="launchd Label (e.g. dev.wingmen.wake-backstop-sweep)")
    parser.add_argument("--source", action="append", required=True, dest="sources",
                         help="A source file this daemon's writer code lives in or imports -- "
                              "repeatable. The gate compares the daemon's process start time "
                              "against the NEWEST mtime across all given sources.")
    parser.add_argument("--pre-deployment", action="store_true",
                         help="This daemon is not deployed yet -- treat 'no running PID' as "
                              "satisfied (first launch will run current code) rather than a "
                              "failure.")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    result = check(args.label, args.sources, pre_deployment=args.pre_deployment)
    if args.json:
        print(json.dumps({"ok": result.ok, "status": result.status, "message": result.message}))
    else:
        prefix = "OK" if result.ok else "FAIL"
        print(f"daemon_restart_gate: {prefix} [{result.status}] {result.message}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
