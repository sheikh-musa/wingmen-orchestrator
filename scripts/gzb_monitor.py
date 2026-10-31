#!/usr/bin/env python3
"""gzb_monitor.py — resource monitor for gzb, the VPS hub + irsyad host (host
'gzb' in host_metrics).

Why (orch-console bus #83733, Musa op#35975): host_metrics today only tracks
cubeasht; Musa wants all 3 fleet machines tracked with real numbers so the
new-Mac-mini sizing decision rests on data, not guesses. This is gzb's half
(mini_monitor.py is the Mini's half, collected locally; cubeasht_monitor.py
is unchanged). gzb runs the hub + irsyad lanes -- nothing runs ON gzb for
this; it is polled read-only over ssh from the Mini, same shape as
cubeasht_monitor.py's ssh collection.

Collection, every 5 min, one ssh call (read-only, no sudo):
  `cat /proc/loadavg; echo SEP; free -m; echo SEP; df -k /; echo SEP; nproc;
   echo SEP; cat /sys/class/thermal/thermal_zone0/temp; echo SEP;
   pgrep -x claude | wc -l; echo SEP; head -1 /proc/stat`
  - CPU load: /proc/loadavg's 1/5/15-min averages (+ nproc for a load_pct).
  - memory: `free -m`'s own columns. The REAL used figure is total minus
    `available` (the kernel's own estimate of memory a new process could get
    without swapping, already discounting reclaimable buff/cache) -- NOT
    naive total-minus-free, which would count buff/cache as used (the same
    trap class PR#658 fixed for cubeasht's Windows-level reading; native
    Linux's own `free` already separates the pools, so this is just picking
    the right column, same principle as mini_monitor.py).
  - swap: `free -m`'s Swap row.
  - disk free: `df -k /`.
  - thermal: /sys/class/thermal/thermal_zone0/temp (x86_pkg_temp, confirmed
    live on gzb, millidegrees C) -- read-only, no sudo. If the zone file is
    absent (a different VPS/kernel), reported as "could not measure", never
    guessed.
  - live claude sessions: `pgrep -x claude | wc -l` over ssh -- exact
    process-name match, same secrets-safety reasoning as mini_monitor.py
    (never pgrep -f with a listing flag).
  - CPU steal time (op#36664, cc-console-revamp's machines-resources panel,
    Musa: "gzbai: 4 vCPU, show load and steal if available"): one read of
    /proc/stat's aggregate "cpu " line, same single-read-per-probe shape as
    everything else here (no added sleep/latency). steal is the 8th
    space-separated field; cpu_steal_pct is steal as a % of the 8
    non-guest jiffie buckets (user+nice+system+idle+iowait+irq+softirq+
    steal -- guest/guest_nice are already folded into user/nice, so they're
    excluded from the denominator, same convention as top/mpstat). An
    instantaneous reading, not a delta -- a rough indicator, same caveat as
    cpu_load_pct's 1-min average. Soft-fails to None (same pattern as
    cpu_temp_c's absent-thermal-zone case) if /proc/stat is missing or an
    unexpected shape -- never raises, since it's advisory-only.

Fail LOUD, unreachable-is-a-row (same rule as cubeasht_monitor.py): an ssh
failure (timeout, auth, network) inserts ok=false with the error -- never a
silently-skipped row, never a gap. A sub-probe parse failure inside an
otherwise-reachable sample is recorded in probe_errors; the rest of the
sample is still stored.

Usage:
  gzb_monitor.py                 # one live run
  gzb_monitor.py --dry-run       # collect + evaluate + print; writes NOTHING
  gzb_monitor.py --summary --date 2026-10-30
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import cubeasht_monitor as cm  # noqa: E402 -- reuse connect/insert_sample/fetch_samples/
                                # sustained/_window_vals/_parse_ts/_fmt_ts/_stats/day_bounds/
                                # load_state/save_state/plan_pages/mark_paged/prune

HOST = "gzb"
SSH_TARGET = "gzb"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
SSH_TIMEOUT_S = 20
FROM_AGENT = "cc-fleet-health"
TO_AGENT = "orch-console"

SAMPLE_INTERVAL_MIN = 5
GRACE_MIN = SAMPLE_INTERVAL_MIN / 2
HISTORY_MIN = 60
DEFAULT_COOLDOWN_MIN = 60
RETENTION_DAYS = 30
PRUNE_LIMIT = 1000
DEFAULT_STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "gzb_monitor.json"

THRESHOLDS = {
    "mem_real_used_pct": 90.0, "mem_window_min": 15,
    "disk_free_pct_min": 15.0,
    "cpu_temp_c": 85.0, "cpu_temp_window_min": 10,
}

_PROBE_CMD = (
    "cat /proc/loadavg; echo GZBMON_SEP; free -m; echo GZBMON_SEP; df -k /; "
    "echo GZBMON_SEP; nproc; echo GZBMON_SEP; "
    "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo NA; "
    "echo GZBMON_SEP; pgrep -x claude | wc -l; "
    "echo GZBMON_SEP; head -1 /proc/stat"
)


class MonitorError(RuntimeError):
    pass


class ProbeParseError(MonitorError):
    pass


# ---------------------------------------------------------------- parsing (pure)

def parse_probe_output(text: str) -> dict:
    parts = text.split("GZBMON_SEP")
    if len(parts) != 7:
        raise ProbeParseError(f"gzb probe output has {len(parts)} sections, expected 7: {text[:300]!r}")
    loadavg_raw, free_raw, df_raw, nproc_raw, therm_raw, claude_raw, stat_raw = (p.strip() for p in parts)

    out: dict = {}

    m = re.match(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)", loadavg_raw)
    if not m:
        raise ProbeParseError(f"/proc/loadavg unparseable: {loadavg_raw!r}")
    out["loadavg_1m"], out["loadavg_5m"], out["loadavg_15m"] = (float(x) for x in m.groups())

    try:
        out["cpu_count"] = int(nproc_raw.strip())
    except ValueError as e:
        raise ProbeParseError(f"nproc unparseable: {nproc_raw!r}") from e
    out["cpu_load_pct"] = round(out["loadavg_1m"] / out["cpu_count"] * 100, 1) if out["cpu_count"] else None

    mem_line = next((l for l in free_raw.splitlines() if l.startswith("Mem:")), None)
    if not mem_line:
        raise ProbeParseError(f"free -m output missing Mem: line: {free_raw[:200]!r}")
    cols = mem_line.split()
    if len(cols) < 7:
        raise ProbeParseError(f"free -m Mem: line has {len(cols)} columns, expected >=7: {mem_line!r}")
    total, used, _free, _shared, buff_cache, available = (int(c) for c in cols[1:7])
    out["mem_total_mb"] = total
    out["mem_naive_used_mb"] = used  # free's own "used" -- kept for comparison, NOT alerted on
    out["mem_buff_cache_mb"] = buff_cache
    out["mem_real_used_mb"] = total - available
    out["mem_real_used_pct"] = round((total - available) / total * 100, 1) if total else None

    swap_line = next((l for l in free_raw.splitlines() if l.startswith("Swap:")), None)
    if swap_line:
        scols = swap_line.split()
        if len(scols) >= 4:
            s_total, s_used = int(scols[1]), int(scols[2])
            out["swap_total_mb"], out["swap_used_mb"] = s_total, s_used
            out["swap_used_pct"] = round(s_used / s_total * 100, 1) if s_total else 0.0

    df_lines = [l for l in df_raw.splitlines() if l.strip() and not l.startswith("Filesystem")]
    if not df_lines:
        raise ProbeParseError(f"df -k / output has no data line: {df_raw[:200]!r}")
    dcols = df_lines[0].split()
    if len(dcols) < 4:
        raise ProbeParseError(f"df -k / data line unparseable: {df_lines[0]!r}")
    total_k, used_k, avail_k = int(dcols[1]), int(dcols[2]), int(dcols[3])
    out["disk_total_gb"] = round(total_k / 1_048_576, 1)
    out["disk_free_gb"] = round(avail_k / 1_048_576, 1)
    out["disk_free_pct"] = round(avail_k / (used_k + avail_k) * 100, 1) if (used_k + avail_k) else None

    therm_raw = therm_raw.strip()
    if therm_raw and therm_raw != "NA" and therm_raw.lstrip("-").isdigit():
        out["cpu_temp_c"] = round(int(therm_raw) / 1000, 1)
        out["cpu_temp_source"] = "thermal_zone0"
    else:
        out["cpu_temp_c"] = None
        out["cpu_temp_source"] = "unavailable"

    claude_raw = claude_raw.strip()
    out["claude_sessions"] = int(claude_raw) if claude_raw.isdigit() else None

    # cpu_steal_pct: advisory-only, soft-fails to None on any unexpected shape
    # (same pattern as cpu_temp_c above) -- never breaks the rest of the probe.
    out["cpu_steal_pct"] = None
    stat_fields = stat_raw.split()
    if len(stat_fields) >= 9 and stat_fields[0] == "cpu":
        try:
            jiffies = [int(x) for x in stat_fields[1:9]]  # user nice system idle iowait irq softirq steal
        except ValueError:
            jiffies = None
        if jiffies:
            total = sum(jiffies)
            steal = jiffies[7]
            if total > 0:
                out["cpu_steal_pct"] = round(steal / total * 100, 1)

    return out


# ---------------------------------------------------------------- collection (I/O)

def _ssh_run(cmd: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", *SSH_OPTS, SSH_TARGET, cmd],
                          capture_output=True, text=True, timeout=timeout)


def collect() -> tuple[bool, dict, Optional[str]]:
    """→ (ok, metrics, error). Never raises for an unreachable host."""
    try:
        cp = _ssh_run(_PROBE_CMD, SSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, {"reachable": False}, f"ssh timed out after {SSH_TIMEOUT_S}s"
    if cp.returncode == 255:
        return False, {"reachable": False}, f"ssh unreachable (rc=255): {cp.stderr.strip()[-200:]}"
    try:
        metrics = parse_probe_output(cp.stdout)
    except ProbeParseError as e:
        return False, {"reachable": True}, f"probe output unparseable (rc={cp.returncode}): {e}"[:500]
    metrics["reachable"] = True
    return True, metrics, None


# ---------------------------------------------------------------- alerts (pure)

def evaluate_alerts(samples: list[dict], now: datetime, th: dict = THRESHOLDS) -> list[dict]:
    samples = sorted(samples, key=lambda s: s["ts"])
    out: list[dict] = []

    def temp_hi(s: dict) -> bool:
        v = cm._m(s).get("cpu_temp_c")
        return bool(s.get("ok")) and v is not None and v >= th["cpu_temp_c"]
    if cm.sustained(samples, now, th["cpu_temp_window_min"], temp_hi):
        vals = cm._window_vals(samples, now, th["cpu_temp_window_min"], "cpu_temp_c")
        out.append({"kind": "gzb_temp", "key": f"{HOST}:temp", "threshold": th["cpu_temp_c"],
                    "window_min": th["cpu_temp_window_min"], "latest": vals[-1], "peak": max(vals)})

    def mem_hi(s: dict) -> bool:
        v = cm._m(s).get("mem_real_used_pct")
        return bool(s.get("ok")) and v is not None and v > th["mem_real_used_pct"]
    if cm.sustained(samples, now, th["mem_window_min"], mem_hi):
        vals = cm._window_vals(samples, now, th["mem_window_min"], "mem_real_used_pct")
        out.append({"kind": "gzb_mem", "key": f"{HOST}:mem", "threshold": th["mem_real_used_pct"],
                    "window_min": th["mem_window_min"], "latest": vals[-1], "peak": max(vals)})

    ok_samples = [s for s in samples if s.get("ok") and s["ts"] <= now]
    if ok_samples:
        last = ok_samples[-1]
        free = cm._m(last).get("disk_free_pct")
        if free is not None and free < th["disk_free_pct_min"]:
            out.append({"kind": "gzb_disk", "key": f"{HOST}:disk", "threshold": th["disk_free_pct_min"],
                        "free_pct": free, "free_gb": cm._m(last).get("disk_free_gb")})
    return out


def render_page(f: dict) -> tuple[str, str]:
    k = f["kind"]
    if k == "gzb_mem":
        subj = f"gzb memory nearly full: {f['latest']:.0f}% real-used for {f['window_min']}+ min"
        body = (
            f"TL;DR: gzb (the VPS hub + irsyad host) has used more than {f['threshold']:.0f}% of its "
            f"REAL memory (total minus /proc's own 'available' estimate, not naive total-used) for "
            f"{f['window_min']} minutes (now {f['latest']:.0f}%, peak {f['peak']:.0f}%).\n\n"
            "WHY IT MATTERS: gzb runs the orchestrator hub + irsyad lanes; sustained memory "
            "pressure there risks OOM-killing a live lane.\n\n"
            "WHAT TO DO: check what's running (claude session count is in the same sample)."
        )
    elif k == "gzb_disk":
        subj = f"gzb disk nearly full: {f['free_pct']:.1f}% free"
        body = (
            f"TL;DR: gzb's root volume has only {f['free_pct']:.1f}% free ({f['free_gb']} GB); "
            f"alert below {f['threshold']:.0f}%.\n\nWHAT TO DO: free space on the VPS."
        )
    elif k == "gzb_temp":
        subj = f"gzb CPU hot: {f['latest']:.0f}C for {f['window_min']}+ min"
        body = (
            f"TL;DR: gzb's CPU package temp has been at or above {f['threshold']:.0f}C for "
            f"{f['window_min']} minutes (now {f['latest']:.0f}C, peak {f['peak']:.0f}C)."
        )
    else:
        subj, body = f"gzb_monitor finding: {k}", json.dumps(f)
    return subj, body + "\n\n(cc-fleet-health gzb_monitor; repeats at most hourly while it lasts.)"


def _send_page(f: dict) -> int:
    sys.path.insert(0, str(SCRIPTS_DIR.parent))
    from scripts.bus_send import send

    subj, body = render_page(f)
    row_id, _thread = send(from_agent=FROM_AGENT, to=TO_AGENT, mtype="update",
                           subject=subj, body=body, priority="P1", req=True)
    return row_id


# ---------------------------------------------------------------- daily summary

def summarize(samples: list[dict], day: date, tz: str = "UTC") -> dict:
    lo, hi = cm.day_bounds(day, tz)
    inday = [s for s in samples if lo <= s["ts"] < hi]
    ok = [s for s in inday if s.get("ok")]

    def vals(rows, field):
        return [float(cm._m(s)[field]) for s in rows if cm._m(s).get(field) is not None]

    return {
        "host": HOST, "date": day.isoformat(), "tz": tz,
        "samples": len(inday), "reachable_samples": len(ok),
        "unreachable_samples": sum(1 for s in inday if cm._m(s).get("reachable") is False),
        "mem_real_used_pct": cm._stats(vals(ok, "mem_real_used_pct")),
        "disk_free_pct": cm._stats(vals(ok, "disk_free_pct")),
        "cpu_load_pct": cm._stats(vals(ok, "cpu_load_pct")),
        "cpu_steal_pct": cm._stats(vals(ok, "cpu_steal_pct")),
        "cpu_temp_c": cm._stats(vals(ok, "cpu_temp_c")),
        "claude_sessions": cm._stats(vals(ok, "claude_sessions")),
    }


# ---------------------------------------------------------------- main

def _brief(m: dict) -> str:
    if not m.get("reachable"):
        return "UNREACHABLE"
    return (f"cpu={m.get('cpu_load_pct')}% steal={m.get('cpu_steal_pct')}% temp={m.get('cpu_temp_c')}C "
            f"mem_real={m.get('mem_real_used_pct')}% swap={m.get('swap_used_pct')}% "
            f"disk_free={m.get('disk_free_pct')}% claude_sessions={m.get('claude_sessions')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--date")
    p.add_argument("--tz", default="UTC")
    p.add_argument("--cooldown-min", type=int, default=DEFAULT_COOLDOWN_MIN)
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    return p


def run_summary(args) -> int:
    if not args.date:
        raise MonitorError("--summary needs --date YYYY-MM-DD")
    day = date.fromisoformat(args.date)
    lo, hi = cm.day_bounds(day, args.tz)
    conn = cm.connect()
    try:
        samples = cm.fetch_samples(conn, HOST, lo, hi)
    finally:
        conn.close()
    print(json.dumps(summarize(samples, day, args.tz), indent=2))
    return 0


def run_once(args) -> int:
    stamp = cm._fmt_ts(cm._now())
    ok, metrics, error = collect()
    print(f"[{stamp}] {HOST} ok={ok} {_brief(metrics)}" + (f" error={error}" if error else ""))

    if args.dry_run:
        print("  sample: " + json.dumps(metrics, sort_keys=True))
        now = cm._now()
        history: list[dict] = []
        try:
            conn = cm.connect()
            try:
                history = cm.fetch_samples(conn, HOST, now - timedelta(minutes=HISTORY_MIN))
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            print(f"  (dry-run: history unavailable — {type(e).__name__}: {str(e).splitlines()[0][:160]})")
        samples = history + [{"ts": now, "ok": ok, "metrics": metrics, "error": error}]
        findings = evaluate_alerts(samples, now)
        to_page, _ = cm.plan_pages(findings, cm.load_state(args.state_file), now, args.cooldown_min)
        print(f"  findings={len(findings)} to_page={len(to_page)} (history samples={len(history)})")
        print("  (dry-run: nothing inserted, paged, pruned or written)")
        return 0

    conn = cm.connect()
    try:
        row_id, ts = cm.insert_sample(conn, HOST, ok, metrics, error)
        print(f"  inserted host_metrics #{row_id}")
        samples = cm.fetch_samples(conn, HOST, ts - timedelta(minutes=HISTORY_MIN), ts + timedelta(seconds=1))
        findings = evaluate_alerts(samples, ts)
        state = cm.load_state(args.state_file)
        to_page, new_state = cm.plan_pages(findings, state, ts, args.cooldown_min)
        print(f"  findings={len(findings)} to_page={len(to_page)} "
              f"suppressed(cooldown)={len(findings) - len(to_page)}")
        if new_state != state:
            cm.save_state(args.state_file, new_state)
        for f in to_page:
            bus_id = _send_page(f)
            new_state = cm.mark_paged(new_state, f["key"], ts)
            cm.save_state(args.state_file, new_state)
            print(f"  PAGED [{f['kind']}] -> {TO_AGENT} bus #{bus_id}")
        n = cm.prune(conn, host=HOST, days=RETENTION_DAYS, limit=PRUNE_LIMIT)
        if n:
            print(f"  pruned {n} host_metrics row(s) older than {RETENTION_DAYS}d")
    finally:
        conn.close()

    if not ok:
        print(f"gzb_monitor: FAIL LOUD (sample stored) — {error}", file=sys.stderr)
        return 4
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_summary(args) if args.summary else run_once(args)
    except MonitorError as e:
        print(f"gzb_monitor: FAIL LOUD — {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"gzb_monitor: FAIL LOUD — {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
