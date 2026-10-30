#!/usr/bin/env python3
"""mini_monitor.py — resource monitor for the Mac Mini (host 'mini' in
host_metrics), the box cc-fleet-health itself runs on.

Why (orch-console bus #83733, Musa op#35975): host_metrics today only tracks
cubeasht; Musa wants all 3 fleet machines tracked with real numbers so the
new-Mac-mini sizing decision rests on data, not guesses. This is the Mini's
half (gzb_monitor.py is the VPS half); cubeasht_monitor.py is unchanged.

Collection is LOCAL (no ssh -- this runs ON the Mini), every 5 min:
  - CPU load: os.getloadavg() (1/5/15-min averages) + hw.ncpu for a load_pct.
  - memory: parses `vm_stat` page counts + `sysctl -n hw.memsize/hw.pagesize`.
    Reports the REAL used figure as (active + wired + compressed) / total --
    NOT the naive "total - free" a misleading tool would show, which counts
    inactive (reclaimable, cache-like) pages as used. Same principle PR#658
    had to apply to cubeasht's WSL/vmmem reading -- here macOS's own vm_stat
    already separates the pools cleanly, so no vmmem-style subtraction is
    needed, just picking the right pools to sum.
  - swap: `sysctl vm.swapusage`.
  - disk free: shutil.disk_usage("/") (stdlib, no subprocess needed).
  - thermal throttling: `pmset -g therm` -- readable WITHOUT sudo (verified
    live). No sensor/throttle line recorded -> not throttled (data, not an
    error). If pmset itself is ever unavailable, reported as
    "could not measure" -- cc-fleet-health has NO sudo/root on the Mini and
    never attempts to get it (memory no-sudo-root-access-on-mini).
  - live claude sessions: `pgrep -x claude | wc -l` -- exact process-name
    match only (never -f, which can leak argv/secrets into output per the
    secrets_transcript_guard hook: pgrep -f combined with a listing flag
    prints full command lines).

Fail LOUD: an exception inside the monitor itself (DB insert, paging, state
file) exits non-zero with stderr. A sub-probe that fails (an unexpected
vm_stat/pmset format, a missing binary) does NOT kill the whole sample --
it is recorded in probe_errors and the rest of the sample is still stored,
same resilience shape as cubeasht_monitor.py. Local collection is never
"unreachable" the way an ssh probe can be; ok=False is reserved for a
monitor-level failure that left no usable sample at all.

Usage:
  mini_monitor.py                 # one live run
  mini_monitor.py --dry-run       # collect + evaluate + print; writes NOTHING
  mini_monitor.py --summary --date 2026-10-30
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
                                # load_state/save_state/plan_pages/mark_paged (all host-agnostic
                                # or host-parameterized already; no re-derivation)

HOST = "mini"
FROM_AGENT = "cc-fleet-health"
TO_AGENT = "orch-console"
SYSCTL_TIMEOUT_S = 5
VMSTAT_TIMEOUT_S = 5
PMSET_TIMEOUT_S = 5
PGREP_TIMEOUT_S = 5

SAMPLE_INTERVAL_MIN = 5
GRACE_MIN = SAMPLE_INTERVAL_MIN / 2
HISTORY_MIN = 60
DEFAULT_COOLDOWN_MIN = 60
RETENTION_DAYS = 30
PRUNE_LIMIT = 1000
DEFAULT_STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "mini_monitor.json"

THRESHOLDS = {
    "mem_real_used_pct": 90.0, "mem_window_min": 15,
    "disk_free_pct_min": 15.0,
}


class MonitorError(RuntimeError):
    pass


class ProbeParseError(MonitorError):
    pass


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------- parsing (pure)

_VMSTAT_FIELDS = {
    "Pages free": "free", "Pages active": "active", "Pages inactive": "inactive",
    "Pages speculative": "speculative", "Pages wired down": "wired",
    "Pages occupied by compressor": "compressed", "Anonymous pages": "anonymous",
}


def parse_vm_stat(text: str) -> dict:
    """`vm_stat` output -> {field: page_count}. Only the fields we use; an
    unrecognized/empty line is skipped, not an error (vm_stat's header line
    and less-used counters are not needed here)."""
    out: dict = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, _, rest = line.partition(":")
        label = label.strip()
        if label not in _VMSTAT_FIELDS:
            continue
        digits = rest.strip().rstrip(".")
        if not digits.lstrip("-").isdigit():
            continue
        out[_VMSTAT_FIELDS[label]] = int(digits)
    if "free" not in out or "anonymous" not in out:
        raise ProbeParseError(f"vm_stat output missing expected fields: {text[:300]!r}")
    return out


def compute_memory(pages: dict, page_size: int, mem_total_bytes: int) -> dict:
    """Real-used = (anonymous + wired + compressed) / total. "active" also
    includes active FILE-BACKED pages (reclaimable disk cache), which would
    over-count; "anonymous" is app/heap memory that isn't disk-backed and
    can't be reclaimed the same way. inactive/free/active are reported but
    NOT counted as used (same class of mistake PR#658 fixed for cubeasht's
    Windows-level reading; this one caught by cc-quality on PR#660 review)."""
    to_mb = lambda n: round(n * page_size / 1_048_576, 1)
    anonymous, wired = pages.get("anonymous", 0), pages.get("wired", 0)
    compressed = pages.get("compressed", 0)
    active, inactive, free = pages.get("active", 0), pages.get("inactive", 0), pages.get("free", 0)
    real_used_pages = anonymous + wired + compressed
    total_mb = round(mem_total_bytes / 1_048_576, 1)
    real_used_mb = to_mb(real_used_pages)
    return {
        "mem_total_mb": total_mb,
        "mem_real_used_mb": real_used_mb,
        "mem_real_used_pct": round(real_used_mb / total_mb * 100, 1) if total_mb else None,
        "mem_free_mb": to_mb(free),
        "mem_active_mb": to_mb(active),  # includes reclaimable file-backed pages; info only
        "mem_inactive_mb": to_mb(inactive),  # reclaimable/cache-like, NOT counted as used
        "mem_anonymous_mb": to_mb(anonymous),
        "mem_compressed_mb": to_mb(compressed),
    }


_SWAP_RE = re.compile(
    r"total\s*=\s*([\d.]+)M\s+used\s*=\s*([\d.]+)M\s+free\s*=\s*([\d.]+)M")


def parse_swapusage(text: str) -> dict:
    m = _SWAP_RE.search(text)
    if not m:
        raise ProbeParseError(f"sysctl vm.swapusage output unparseable: {text[:200]!r}")
    total, used, free = (float(x) for x in m.groups())
    return {"swap_total_mb": total, "swap_used_mb": used, "swap_free_mb": free,
            "swap_used_pct": round(used / total * 100, 1) if total else 0.0}


def parse_therm(text: str) -> dict:
    """`pmset -g therm` -- no sudo needed (verified live). A bare "no X
    recorded" response means no throttling signal has fired (data, not an
    error); any `key = value` line found is captured as-is for visibility."""
    kv: dict = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k:
                kv[k] = v
    return {"recorded_fields": kv, "throttled": any(
        "limit" in k.lower() and v not in ("100", "100.0", "0") for k, v in kv.items()
    )} if kv else {"recorded_fields": {}, "throttled": False, "note": "no thermal warning recorded"}


# ---------------------------------------------------------------- collection (I/O)

def collect() -> tuple[bool, dict, Optional[str]]:
    """Local collection -- never raises for a normal sub-probe failure (those
    land in probe_errors); only an unanticipated monitor bug propagates."""
    import os

    probe_errors: dict = {}
    metrics: dict = {"reachable": True}

    try:
        load1, load5, load15 = os.getloadavg()
        ncpu_cp = _run(["sysctl", "-n", "hw.ncpu"], SYSCTL_TIMEOUT_S)
        ncpu = int(ncpu_cp.stdout.strip()) if ncpu_cp.returncode == 0 else None
        metrics["loadavg_1m"], metrics["loadavg_5m"], metrics["loadavg_15m"] = load1, load5, load15
        metrics["cpu_load_pct"] = round(load1 / ncpu * 100, 1) if ncpu else None
        metrics["cpu_count"] = ncpu
    except Exception as e:  # noqa: BLE001
        probe_errors["cpu"] = f"{type(e).__name__}: {e}"

    try:
        vcp = _run(["vm_stat"], VMSTAT_TIMEOUT_S)
        if vcp.returncode != 0:
            raise ProbeParseError(f"vm_stat exited {vcp.returncode}: {vcp.stderr.strip()[:200]}")
        pages = parse_vm_stat(vcp.stdout)
        pcp = _run(["sysctl", "-n", "hw.pagesize"], SYSCTL_TIMEOUT_S)
        mcp = _run(["sysctl", "-n", "hw.memsize"], SYSCTL_TIMEOUT_S)
        if pcp.returncode != 0 or mcp.returncode != 0:
            raise ProbeParseError("sysctl hw.pagesize/hw.memsize failed")
        metrics.update(compute_memory(pages, int(pcp.stdout.strip()), int(mcp.stdout.strip())))
    except Exception as e:  # noqa: BLE001
        probe_errors["memory"] = f"{type(e).__name__}: {e}"

    try:
        scp = _run(["sysctl", "vm.swapusage"], SYSCTL_TIMEOUT_S)
        if scp.returncode != 0:
            raise ProbeParseError(f"sysctl vm.swapusage exited {scp.returncode}")
        metrics.update(parse_swapusage(scp.stdout))
    except Exception as e:  # noqa: BLE001
        probe_errors["swap"] = f"{type(e).__name__}: {e}"

    try:
        import shutil
        du = shutil.disk_usage("/")
        metrics["disk_free_gb"] = round(du.free / 1e9, 1)
        metrics["disk_total_gb"] = round(du.total / 1e9, 1)
        metrics["disk_free_pct"] = round(du.free / du.total * 100, 1) if du.total else None
    except Exception as e:  # noqa: BLE001
        probe_errors["disk"] = f"{type(e).__name__}: {e}"

    try:
        tcp = _run(["pmset", "-g", "therm"], PMSET_TIMEOUT_S)
        if tcp.returncode != 0:
            raise ProbeParseError(f"pmset -g therm exited {tcp.returncode}")
        metrics["thermal"] = parse_therm(tcp.stdout)
    except Exception as e:  # noqa: BLE001
        probe_errors["thermal"] = f"could not measure: {type(e).__name__}: {e}"

    try:
        pcp2 = _run(["pgrep", "-x", "claude"], PGREP_TIMEOUT_S)
        if pcp2.returncode not in (0, 1):  # 1 = no match, not an error
            raise ProbeParseError(f"pgrep -x claude exited {pcp2.returncode}: {pcp2.stderr.strip()[:200]}")
        metrics["claude_sessions"] = len([l for l in pcp2.stdout.splitlines() if l.strip()])
    except Exception as e:  # noqa: BLE001
        probe_errors["claude_sessions"] = f"{type(e).__name__}: {e}"

    if probe_errors:
        metrics["probe_errors"] = probe_errors
    # ok=False only if we got NOTHING usable at all (every probe failed).
    ok = len(probe_errors) < 6
    return ok, metrics, (None if ok else "every local probe failed")


# ---------------------------------------------------------------- alerts (pure)

def evaluate_alerts(samples: list[dict], now: datetime, th: dict = THRESHOLDS) -> list[dict]:
    samples = sorted(samples, key=lambda s: s["ts"])
    out: list[dict] = []

    def mem_hi(s: dict) -> bool:
        v = cm._m(s).get("mem_real_used_pct")
        return bool(s.get("ok")) and v is not None and v > th["mem_real_used_pct"]
    if cm.sustained(samples, now, th["mem_window_min"], mem_hi):
        vals = cm._window_vals(samples, now, th["mem_window_min"], "mem_real_used_pct")
        out.append({"kind": "mini_mem", "key": f"{HOST}:mem", "threshold": th["mem_real_used_pct"],
                    "window_min": th["mem_window_min"], "latest": vals[-1], "peak": max(vals)})

    ok_samples = [s for s in samples if s.get("ok") and s["ts"] <= now]
    if ok_samples:
        last = ok_samples[-1]
        free = cm._m(last).get("disk_free_pct")
        if free is not None and free < th["disk_free_pct_min"]:
            out.append({"kind": "mini_disk", "key": f"{HOST}:disk", "threshold": th["disk_free_pct_min"],
                        "free_pct": free, "free_gb": cm._m(last).get("disk_free_gb")})
    return out


def render_page(f: dict) -> tuple[str, str]:
    k = f["kind"]
    if k == "mini_mem":
        subj = f"Mini memory nearly full: {f['latest']:.0f}% real-used for {f['window_min']}+ min"
        body = (
            f"TL;DR: the Mac Mini (cc-fleet-health's own box) has used more than "
            f"{f['threshold']:.0f}% of its REAL memory (active+wired+compressed, not cache) for "
            f"{f['window_min']} minutes (now {f['latest']:.0f}%, peak {f['peak']:.0f}%).\n\n"
            "WHY IT MATTERS: this box runs the fleet's agents; sustained real memory pressure "
            "means swapping and everything (including this monitor) slows down.\n\n"
            "WHAT TO DO: check what's running (claude session count is in the same sample); a "
            "runaway or leaked process is the usual cause."
        )
    elif k == "mini_disk":
        subj = f"Mini disk nearly full: {f['free_pct']:.1f}% free"
        body = (
            f"TL;DR: the Mac Mini's root volume has only {f['free_pct']:.1f}% free "
            f"({f['free_gb']} GB); alert below {f['threshold']:.0f}%.\n\n"
            "WHY IT MATTERS: when the disk fills, the fleet's own agents (including this one) "
            "can fail to write state/logs.\n\n"
            "WHAT TO DO: free space (old worktrees, logs, caches)."
        )
    else:
        subj, body = f"mini_monitor finding: {k}", json.dumps(f)
    return subj, body + "\n\n(cc-fleet-health mini_monitor; repeats at most hourly while it lasts.)"


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
        "mem_real_used_pct": cm._stats(vals(ok, "mem_real_used_pct")),
        "disk_free_pct": cm._stats(vals(ok, "disk_free_pct")),
        "cpu_load_pct": cm._stats(vals(ok, "cpu_load_pct")),
        "claude_sessions": cm._stats(vals(ok, "claude_sessions")),
        "swap_used_pct": cm._stats(vals(ok, "swap_used_pct")),
    }


# ---------------------------------------------------------------- main

def _brief(m: dict) -> str:
    return (f"cpu={m.get('cpu_load_pct')}% mem_real={m.get('mem_real_used_pct')}% "
            f"swap={m.get('swap_used_pct')}% disk_free={m.get('disk_free_pct')}% "
            f"claude_sessions={m.get('claude_sessions')} "
            f"therm_throttled={(m.get('thermal') or {}).get('throttled')}")


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
    if metrics.get("probe_errors"):
        print(f"  probe notes: {json.dumps(metrics['probe_errors'])}")

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
        print(f"mini_monitor: FAIL LOUD (sample stored) — {error}", file=sys.stderr)
        return 4
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_summary(args) if args.summary else run_once(args)
    except MonitorError as e:
        print(f"mini_monitor: FAIL LOUD — {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"mini_monitor: FAIL LOUD — {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
