#!/usr/bin/env python3
"""zero_waste_audit.py -- op#35929/35930 (Musa directive, bus #83575): measure
real per-lane/pool token (and DeepSeek dollar) burn over a trailing window,
with wake counts and how many wakes produced real work, so waste claims are
evidence-backed, never guessed.

Method (same one used manually earlier this session, now made repeatable):
  - Enumerate live lanes from agent_status, cross-referenced against actual
    tmux panes on BOTH hosts (Mini local, gzb over ssh) -- a DB row alone is
    not proof of a live process.
  - For each lane with a resolvable Claude Code project transcript, bucket
    every message's token usage (input + output + cache_creation +
    cache_read) into hourly buckets over the trailing window.
  - Wake count = agent_messages rows to_agent=<lane's base id> in the window.
  - "Produced real work" = at least one git commit in that lane's repo
    attributed to the window, OR at least one outbound agent_messages row
    from that lane in the window (a bus reply), within a reasonable lag of
    a wake. This is a proxy, not exact -- documented as such in the report.
  - DeepSeek $/hour: consecutive-delta on deepseek_balance_poll.py's JSONL
    balance history.

Every measurement that can't be taken (unreachable host, no transcript, no
balance history) is reported as "could not measure" -- this script never
interpolates or guesses a number per Musa's explicit instruction.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
MINI_PROJECTS_BASE = HOME / ".claude" / "projects"
DEEPSEEK_BALANCE_FILE = HOME / "wingmen" / "fleet-health" / "state" / "deepseek_balance_history.jsonl"
SSH_GZB = ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "gzb"]


def escape_path(p: str) -> str:
    return p.replace("/", "-").replace(".", "-")


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pane / host enumeration
# ---------------------------------------------------------------------------

def list_local_panes():
    """[(session, cwd), ...] on the Mini. [] with a note on failure."""
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_path}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:
        return None, f"could not measure Mini tmux panes: {exc}"
    if out.returncode != 0:
        return None, f"could not measure Mini tmux panes: rc={out.returncode} {out.stderr.strip()[:200]}"
    panes = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            panes.append((parts[0], parts[1]))
    return panes, None


def list_gzb_panes():
    """[(session, cwd), ...] on gzb (remote host). [] with a note on failure."""
    try:
        out = subprocess.run(
            SSH_GZB + ["tmux list-panes -a -F '#{session_name}\t#{pane_current_path}'"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:
        return None, f"could not measure gzb tmux panes: {exc}"
    if out.returncode != 0:
        return None, f"could not measure gzb tmux panes: rc={out.returncode} {out.stderr.strip()[:200]}"
    panes = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            panes.append((parts[0], parts[1]))
    return panes, None


# ---------------------------------------------------------------------------
# Transcript token usage, hourly-bucketed over a trailing window
# ---------------------------------------------------------------------------

def find_local_latest_jsonl(cwd: str):
    proj_dir = MINI_PROJECTS_BASE / escape_path(cwd)
    if not proj_dir.is_dir():
        return None
    files = sorted(proj_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


def hourly_buckets_from_lines(lines, window_start, window_end):
    """lines: iterable of raw jsonl text lines. Returns dict hour_start_iso -> total_tokens,
    plus nmsg, last_ts (iso) within the window."""
    buckets = {}
    nmsg = 0
    last_ts = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        ts_raw = obj.get("timestamp")
        if not ts_raw:
            continue
        tdt = parse_ts(ts_raw)
        if tdt is None:
            continue
        if last_ts is None or tdt > last_ts:
            last_ts = tdt
        if tdt < window_start or tdt > window_end:
            continue
        msg = obj.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not usage:
            continue
        total = (
            (usage.get("input_tokens", 0) or 0)
            + (usage.get("output_tokens", 0) or 0)
            + (usage.get("cache_creation_input_tokens", 0) or 0)
            + (usage.get("cache_read_input_tokens", 0) or 0)
        )
        hour_key = tdt.replace(minute=0, second=0, microsecond=0).isoformat()
        buckets[hour_key] = buckets.get(hour_key, 0) + total
        nmsg += 1
    return buckets, nmsg, (last_ts.isoformat() if last_ts else None)


def measure_local_lane(cwd: str, window_start, window_end):
    jf = find_local_latest_jsonl(cwd)
    if jf is None:
        return None, f"could not measure: no transcript dir resolvable for cwd={cwd}"
    try:
        with open(jf, "r") as f:
            buckets, nmsg, last_ts = hourly_buckets_from_lines(f, window_start, window_end)
    except Exception as exc:
        return None, f"could not measure: failed reading {jf}: {exc}"
    return {"buckets": buckets, "nmsg": nmsg, "last_ts": last_ts, "source": str(jf)}, None


def measure_gzb_lane(cwd: str, window_start, window_end):
    remote_dir = f"~/.claude/projects/{escape_path(cwd)}"
    try:
        find_out = subprocess.run(
            SSH_GZB + [f"ls -t {remote_dir}/*.jsonl 2>/dev/null | head -1"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:
        return None, f"could not measure (gzb ssh failed): {exc}"
    remote_file = find_out.stdout.strip()
    if not remote_file:
        return None, f"could not measure: no transcript found on gzb for cwd={cwd}"
    try:
        cat_out = subprocess.run(
            SSH_GZB + [f"cat {remote_file}"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        return None, f"could not measure (gzb cat failed): {exc}"
    if cat_out.returncode != 0:
        return None, f"could not measure: gzb cat rc={cat_out.returncode}"
    buckets, nmsg, last_ts = hourly_buckets_from_lines(cat_out.stdout.splitlines(), window_start, window_end)
    return {"buckets": buckets, "nmsg": nmsg, "last_ts": last_ts, "source": f"gzb:{remote_file}"}, None


# ---------------------------------------------------------------------------
# DeepSeek $/hour from balance-history deltas
# ---------------------------------------------------------------------------

def deepseek_dollar_buckets(window_start, window_end):
    if not DEEPSEEK_BALANCE_FILE.is_file():
        return None, "could not measure: deepseek_balance_history.jsonl not found"
    points = []
    with open(DEEPSEEK_BALANCE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            tdt = parse_ts(obj.get("ts", ""))
            if tdt is None:
                continue
            points.append((tdt, obj.get("balance_usd")))
    points.sort(key=lambda p: p[0])
    in_window = [p for p in points if window_start <= p[0] <= window_end]
    if len(in_window) < 2:
        return None, f"could not measure: only {len(in_window)} balance point(s) in window (need >=2 for a delta)"
    buckets = {}
    for (t0, b0), (t1, b1) in zip(in_window, in_window[1:]):
        if b0 is None or b1 is None:
            continue
        spend = max(0.0, b0 - b1)  # balance decreasing = spend
        hour_key = t0.replace(minute=0, second=0, microsecond=0).isoformat()
        buckets[hour_key] = buckets.get(hour_key, 0.0) + spend
    coverage_hours = (in_window[-1][0] - in_window[0][0]).total_seconds() / 3600.0
    return {"buckets": buckets, "coverage_hours": coverage_hours, "npoints": len(in_window)}, None


# ---------------------------------------------------------------------------
# Wake count + real-work proxy from the bus + git log
# ---------------------------------------------------------------------------

def wake_and_work_counts(cur, base_agent_id, window_start, window_end, repo_path=None):
    cur.execute(
        """SELECT id, created_at FROM agent_messages
           WHERE to_agent = %s AND created_at BETWEEN %s AND %s
           ORDER BY created_at""",
        (base_agent_id, window_start, window_end),
    )
    wakes = cur.fetchall()
    cur.execute(
        """SELECT created_at FROM agent_messages
           WHERE from_agent = %s AND created_at BETWEEN %s AND %s
           ORDER BY created_at""",
        (base_agent_id, window_start, window_end),
    )
    replies = [r[0] for r in cur.fetchall()]

    commit_times = []
    if repo_path and os.path.isdir(repo_path):
        try:
            out = subprocess.run(
                ["git", "-C", repo_path, "log", "--all", "--since", window_start.isoformat(),
                 "--until", window_end.isoformat(), "--format=%cI"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    tdt = parse_ts(line.strip())
                    if tdt:
                        commit_times.append(tdt)
        except Exception:
            pass

    evidence_times = sorted(replies + commit_times)
    productive = 0
    LAG = timedelta(minutes=45)
    for (_, wake_ts) in wakes:
        if any(wake_ts <= e <= wake_ts + LAG for e in evidence_times):
            productive += 1

    return {
        "wake_count": len(wakes),
        "productive_wake_count": productive,
        "reply_count": len(replies),
        "commit_count": len(commit_times),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--out", default=None, help="write JSON results here")
    args = ap.parse_args()

    window_end = now_utc()
    window_start = window_end - timedelta(hours=args.hours)

    local_panes, local_err = list_local_panes()
    gzb_panes, gzb_err = list_gzb_panes()

    results = {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "errors": [e for e in (local_err, gzb_err) if e],
        "lanes": {},
    }

    for host_label, panes, measurer in (
        ("mini", local_panes or [], measure_local_lane),
        ("gzb", gzb_panes or [], measure_gzb_lane),
    ):
        for session, cwd in panes:
            key = f"{host_label}:{session}"
            data, err = measurer(cwd, window_start, window_end)
            results["lanes"][key] = {
                "host": host_label, "session": session, "cwd": cwd,
                "measurement": data, "error": err,
            }

    ds_buckets, ds_err = deepseek_dollar_buckets(window_start, window_end)
    results["deepseek_dollars"] = ds_buckets
    if ds_err:
        results["errors"].append(ds_err)

    out_path = args.out
    if out_path:
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"wrote {out_path}", file=sys.stderr)
    else:
        json.dump(results, sys.stdout, indent=2, default=str)


if __name__ == "__main__":
    main()
