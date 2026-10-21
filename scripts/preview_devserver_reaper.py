"""preview_devserver_reaper — stop idle, unwatched `next dev`/`vite` preview servers and
clear their regenerable .next/dist build cache (cc-fleet-health §4.6, greenlit orch-console
#72892/#72894).

WHY (two disk CRITs same day, 2026-10-21 — #72830, #72887): both traced to the SAME root
cause in different worktrees: an ad-hoc dev-server preview (`next dev -pNNNN`) started to
show a demo/walkthrough, left running after nobody was watching it anymore. Each ballooned
its own `.next/dev` by 1-2GB/hour of hot-module-reload churn. Existing hygiene (the
node_modules reaper, the stale-worktree reaper) keys on file-mtime idleness — a server that
is actively rebuilding its own cache looks "alive" to every mtime check even when zero
humans are consuming its output. This closes that gap.

SELF-CORRECTION (#72889 -> #72897): the ORIGINAL proposed signature ("no fleet_lanes row AND
zero established connections") is WRONG — case 2 (cosem-exams-lane) HAD a registered
fleet_lanes row (desired_state=up) and would have been skipped by that gate, a false
negative on the exact CRIT it was meant to catch. The ACTUAL gate is established-connection
count alone, held idle for a grace window; fleet_lanes/tmux-pane liveness is an EXTRA safety
check (never yank a dev server out from under a lane whose own pane is mid-turn), not a
required condition for detection.

DISCIPLINE (CLAUDE.md §2):
  - Verify before act: a server is reap-eligible only after its listening port has shown
    ZERO established connections for an idle-grace window (default 20min, env-overridable) —
    never on a single instantaneous zero-connection read (avoids killing a server in the
    brief gap between page loads while someone IS reviewing).
  - Belt-and-suspenders: if the worktree matches a `fleet_lanes` row with a live tmux session
    (session name == lane name), that session's own pane is checked; a BUSY pane (mid-turn)
    spares the server even if its port looks idle.
  - Reversible: only the dev-server PROCESS (SIGTERM) and its gitignored `.next`/`dist` build
    cache are touched. Tracked files, reports, and the worktree itself are never touched.
  - Dead-man / fail-loud: a per-target failure is logged, never swallowed; reaps are
    independent (one failure never aborts the sweep); each successful reap is appended to a
    durable ledger (worktree, pid, port, bytes freed) so a lane that unexpectedly needs its
    preview back can see exactly what happened and how to restart it.
  - Staged arming: DETECT-ONLY by default. --apply acts. State (first-seen-idle timestamps)
    persists across invocations in a small JSON file so the grace window survives separate
    cron ticks.

Usage (from ~/wingmen/orchestrator):
  .venv/bin/python3 -m scripts.preview_devserver_reaper            # DETECT-ONLY
  .venv/bin/python3 -m scripts.preview_devserver_reaper --apply    # reap for real (ARMED)
  .venv/bin/python3 -m scripts.preview_devserver_reaper --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

GB = 1 << 30
MB = 1 << 20

# --- tunables (env-overridable) ---
IDLE_GRACE_MIN = float(os.environ.get("PREVIEW_REAPER_IDLE_GRACE_MIN", "20"))
IDLE_GRACE_S = IDLE_GRACE_MIN * 60
# Command-line substrings that identify a preview dev-server process worth watching.
DEV_SERVER_PATTERNS = tuple(p for p in os.environ.get(
    "PREVIEW_REAPER_PATTERNS", "next dev:next-server:vite dev:vite --port:vite --host"
).split(":") if p)
# Build-cache dir names cleared on reap (gitignored + regenerable).
RECLAIM_NAMES = tuple(n for n in os.environ.get(
    "PREVIEW_REAPER_RECLAIM_NAMES", ".next:dist"
).split(":") if n)
LSOF_TIMEOUT_S = int(os.environ.get("PREVIEW_REAPER_LSOF_TIMEOUT_S", "30"))
TMUX_TIMEOUT_S = int(os.environ.get("PREVIEW_REAPER_TMUX_TIMEOUT_S", "15"))
STATE_PATH = pathlib.Path(os.environ.get(
    "PREVIEW_REAPER_STATE_PATH",
    str(pathlib.Path(__file__).parent.parent / "state" / "preview_devserver_reaper_state.json"),
))
LEDGER_PATH = pathlib.Path(os.environ.get(
    "PREVIEW_REAPER_LEDGER_PATH",
    str(pathlib.Path(__file__).parent.parent / "logs" / "preview_devserver_reaper_ledger.jsonl"),
))
# A busy pane's footer text (fleet-wide convention — the lane is mid-turn).
BUSY_FOOTER = "esc to interrupt"


def log(msg: str) -> None:
    print(msg, flush=True)


# ---- pure decision (fully unit-testable; no I/O) -------------------------------
def classify(*, established_count: int, grace_elapsed: bool, lane_pane_busy: bool) -> str:
    """Verdict for ONE candidate dev-server process. Cheapest/safest gate first.
    - busy: someone has an established connection to the port right now — NEVER reap.
    - grace: idle, but not long enough yet — wait.
    - lane-busy: a registered lane's own tmux pane is mid-turn — spare it even if the port
      looks idle (belt-and-suspenders; the lane may be about to re-view its own preview).
    - reap: idle past the grace window, and no live lane turn in progress."""
    if established_count > 0:
        return "skip:busy"
    if not grace_elapsed:
        return "skip:grace"
    if lane_pane_busy:
        return "skip:lane-busy"
    return "reap"


def update_grace_state(state: dict, key: str, *, now: float, established_count: int,
                        idle_grace_s: float) -> "tuple[bool, dict]":
    """Pure idle-grace bookkeeping. `state` maps key -> first-seen-idle epoch.
    - busy (established_count > 0): drop the key (reset the timer) — not elapsed.
    - idle, first time seen: record `now` as first-seen, not elapsed yet.
    - idle, already tracked: elapsed iff now - first_seen >= idle_grace_s (timestamp itself
      is NEVER advanced — grace is measured from the first idle observation)."""
    new_state = dict(state)
    if established_count > 0:
        new_state.pop(key, None)
        return False, new_state
    if key not in new_state:
        new_state[key] = now
        return False, new_state
    elapsed = (now - new_state[key]) >= idle_grace_s
    return elapsed, new_state


def _matches_dev_server_pattern(command: str, patterns=DEV_SERVER_PATTERNS) -> bool:
    """True if `command` (a process command line) looks like a preview dev server."""
    return any(p in command for p in patterns)


# ---- I/O probes (fail-safe: an unverifiable read is NEVER treated as idle) ----
def _established_count(port: int) -> int:
    """Number of ESTABLISHED TCP connections on `port`. FAIL-SAFE: on any error, return 1
    (never idle) so we never reap a server we couldn't actually verify as unwatched."""
    try:
        r = subprocess.run(
            ["lsof", "-iTCP", f":{port}", "-sTCP:ESTABLISHED", "-Fp"],
            capture_output=True, text=True, timeout=LSOF_TIMEOUT_S,
        )
        if r.returncode not in (0, 1):  # 1 = lsof found nothing, not an error
            return 1
        return sum(1 for line in r.stdout.splitlines() if line.startswith("p"))
    except (subprocess.TimeoutExpired, OSError):
        return 1


def _pane_busy(session: str) -> bool:
    """True if tmux session `session`'s pane footer shows the fleet-wide BUSY marker.
    A session that does not exist is NOT a live pane to worry about -> False (lets an
    orphan/not-currently-attached server be reaped). FAIL-SAFE on any OTHER error: True
    (never treat an unverifiable pane as idle)."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True, text=True, timeout=TMUX_TIMEOUT_S,
        )
        if r.returncode != 0:
            return False  # no such session (or no such pane) -> nothing live to spare
        return BUSY_FOOTER in r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return True


def _parse_ps_output(text: str) -> "list[tuple[int, str]]":
    """Parse `ps -eo pid,command` output into (pid, command) pairs, skipping the header
    and any unparseable line (fail-safe: skip, never raise)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, command = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        out.append((pid, command))
    return out


def _candidate_processes() -> "list[tuple[int, str]]":
    """All currently-running processes whose command line matches a dev-server pattern."""
    try:
        r = subprocess.run(["ps", "-eo", "pid,command"],
                           capture_output=True, text=True, timeout=LSOF_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return []
    rows = _parse_ps_output(r.stdout)
    return [(pid, cmd) for pid, cmd in rows if _matches_dev_server_pattern(cmd)]


def _cwd_for_pid(pid: int) -> "pathlib.Path | None":
    """The process's current working directory, or None if unknown/unverifiable."""
    try:
        r = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                           capture_output=True, text=True, timeout=LSOF_TIMEOUT_S)
        for line in r.stdout.splitlines():
            if line.startswith("n"):
                return pathlib.Path(line[1:])
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _listening_port_for_pid(pid: int) -> "int | None":
    """The TCP port the process is LISTENing on, or None if none/unverifiable."""
    try:
        r = subprocess.run(["lsof", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
                           capture_output=True, text=True, timeout=LSOF_TIMEOUT_S)
        for line in r.stdout.splitlines():
            if line.startswith("n") and ":" in line:
                port_s = line.rsplit(":", 1)[-1]
                try:
                    return int(port_s)
                except ValueError:
                    continue
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _dir_size(path: pathlib.Path) -> int:
    total = 0
    try:
        for dp, _, files in os.walk(path):
            for f in files:
                try:
                    total += (pathlib.Path(dp) / f).lstat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def find_reclaimable_dirs(wt: pathlib.Path, names=RECLAIM_NAMES):
    """All build-cache dirs (`.next`/`dist` by default) within `wt` (pruned — does not
    descend into a match)."""
    out = []
    try:
        for dirpath, dirnames, _ in os.walk(wt):
            keep = []
            for dn in dirnames:
                if dn in names:
                    out.append(pathlib.Path(dirpath) / dn)
                else:
                    keep.append(dn)
            dirnames[:] = keep
    except OSError:
        pass
    return out


def _rmtree(path: pathlib.Path) -> None:
    import shutil
    shutil.rmtree(path)


def _load_state(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(path: pathlib.Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def _append_ledger(path: pathlib.Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---- DB I/O ---------------------------------------------------------------
def _dsn() -> str:
    from dotenv import load_dotenv
    load_dotenv(str(pathlib.Path(__file__).parent.parent / ".env"))
    return os.environ.get("SUPABASE_DB_URL") or os.environ["DATABASE_URL"]


def _connect():
    import psycopg
    return psycopg.connect(_dsn(), connect_timeout=15)


def fetch_fleet_lane_names(conn) -> dict:
    """worktree_path -> lane (tmux session name), for the pane-idle safety check."""
    cur = conn.cursor()
    cur.execute("SELECT worktree_path, lane FROM fleet_lanes WHERE worktree_path IS NOT NULL")
    return {row[0]: row[1] for row in cur.fetchall()}


# ---- orchestration ----------------------------------------------------------
def _kill_process(pid: int) -> bool:
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError as e:
        log(f"  ! kill failed pid={pid}: {e}")
        return False


def scan(*, state: dict, lane_names: dict, now: float) -> "tuple[list[dict], dict]":
    """Detect-only pass: returns (reports, new_state). Each report has worktree/pid/port/
    verdict/freed(would-be). Never touches the process or filesystem."""
    reports = []
    new_state = dict(state)
    for pid, cmd in _candidate_processes():
        port = _listening_port_for_pid(pid)
        if port is None:
            continue
        cwd = _cwd_for_pid(pid)
        if cwd is None:
            continue
        worktree = str(cwd)
        established = _established_count(port)
        key = f"{pid}:{port}"
        grace_elapsed, new_state = update_grace_state(
            new_state, key, now=now, established_count=established, idle_grace_s=IDLE_GRACE_S)
        lane_name = lane_names.get(worktree)
        pane_busy = _pane_busy(lane_name) if lane_name else False
        verdict = classify(established_count=established, grace_elapsed=grace_elapsed,
                           lane_pane_busy=pane_busy)
        would_free = sum(_dir_size(d) for d in find_reclaimable_dirs(cwd)) if verdict == "reap" else 0
        reports.append({
            "pid": pid, "port": port, "worktree": worktree, "command": cmd,
            "lane": lane_name, "established": established, "verdict": verdict,
            "would_free": would_free,
        })
    return reports, new_state


def reap(reports: "list[dict]") -> "list[dict]":
    """Apply pass: for each report with verdict=='reap', kill the process and clear its
    build-cache dirs. Returns reap-ledger entries (one per attempted reap, success or not)."""
    out = []
    for r in reports:
        if r["verdict"] != "reap":
            continue
        entry = {"ts": time.time(), "worktree": r["worktree"], "pid": r["pid"],
                  "port": r["port"], "freed_bytes": 0, "errors": []}
        if not _kill_process(r["pid"]):
            entry["errors"].append("kill-failed")
            out.append(entry)
            continue
        time.sleep(1)
        for d in find_reclaimable_dirs(pathlib.Path(r["worktree"])):
            sz = _dir_size(d)
            try:
                _rmtree(d)
                entry["freed_bytes"] += sz
            except OSError as e:
                entry["errors"].append(f"rm failed {d}: {e}")
        out.append(entry)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="reap for real (default: detect-only)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)
    mode = "APPLY" if args.apply else "DETECT-ONLY"

    try:
        conn = _connect()
        lane_names = fetch_fleet_lane_names(conn)
    except Exception as e:
        log(f"db-connect-failed (treating as no registered lanes): {e}")
        lane_names = {}

    state = _load_state(STATE_PATH)
    now = time.time()
    reports, new_state = scan(state=state, lane_names=lane_names, now=now)
    _save_state(STATE_PATH, new_state)

    summary = {"mode": mode, "scanned": len(reports),
               "reap_candidates": sum(1 for r in reports if r["verdict"] == "reap"),
               "would_free_bytes": sum(r["would_free"] for r in reports), "reports": reports}

    ledger_entries = []
    if args.apply:
        ledger_entries = reap([r for r in reports if r["verdict"] == "reap"])
        for e in ledger_entries:
            _append_ledger(LEDGER_PATH, e)
        summary["reaped"] = len(ledger_entries)
        summary["freed_bytes"] = sum(e["freed_bytes"] for e in ledger_entries)
        summary["errors"] = [err for e in ledger_entries for err in e["errors"]]

    if args.json:
        log(json.dumps(summary, indent=2))
    else:
        log(f"[preview-reaper {mode}] scanned={len(reports)} "
            f"reap_candidates={summary['reap_candidates']} "
            f"would_free~{summary['would_free_bytes'] // MB}MB")
        for r in reports:
            if r["verdict"] != "skip:busy" or r["established"]:
                log(f"  {r['verdict']} pid={r['pid']} port={r['port']} wt={r['worktree']}")

    return 1 if summary.get("errors") else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
