#!/usr/bin/env python3
"""cubeasht_monitor.py — resource + temperature monitor for cubeasht.

Why (cc-fleet-health; orch-console #58069/#58243, Musa op#27169): cubeasht is
Musa's Windows desktop (Ryzen 5 7600X, RTX 4060, SK hynix P41 NVMe). It is now
left on 24/7 and hosts our self-hosted GitHub Actions runner
`cubeasht-orchestrator` (WSL distro `ci-orchestrator`). A box that runs 24/7
under CI load needs its temperatures and resources watched, and someone told
when it overheats, fills up, or drops off.

One run (launchd, every 5 min):
  1. COLLECT one sample, read-only, over ssh:
       - one PowerShell script on stdin (scripts/cubeasht_collect.ps1):
         CPU load, RAM, disk C:, last boot, nvidia-smi (GPU), NVMe reliability
         counters, LibreHardwareMonitor CPU temp (if installed), vmmem WS
       - one WSL call (scripts/cubeasht_collect_wsl.sh): runner service state,
         `free -m` inside the distro
       - GitHub API: runner online/offline + busy (busy = a CI job is running)
  2. INSERT one host_metrics row (migration 096). An unreachable desktop is
     DATA: it is stored as ok=false so the "unreachable >30 min" window works.
  3. EVALUATE alerts over the recent window ("sustained" = every sample in the
     window breaches, and the window is actually covered by samples).
  4. PAGE orch-console (bus_send.send, P1, requires_response) — deduped via a
     JSON state file, 60-min cooldown per alert key, key cleared when resolved.
  5. PRUNE host_metrics rows for this host older than 30 days (bounded LIMIT).

Thresholds (see THRESHOLDS below):
  cpu_temp   >= 85 C sustained 10 min  (ONLY when a CPU temp source exists —
                                        today none does; skipped silently)
  gpu_temp   >= 85 C sustained 10 min  (RTX 4060 slows down ~90 C+)
  nvme_temp  >= 70 C sustained 10 min  (P41 reports TemperatureMax 86 C; 70 is
                                        early warning well below throttle)
  disk_c     <  15 % free              (latest reachable sample)
  ram        >  90 % used sustained 15 min
  runner_offline   GitHub says offline for 30 min WHILE ssh works
  unreachable      every sample in the last 30 min failed to connect

CPU temperature: no sensor is exposed on cubeasht today (MSAcpi thermal zone
"Not supported"). The collector already queries LibreHardwareMonitor's WMI
namespace root/LibreHardwareMonitor (Sensor, SensorType='Temperature', Name
'CPU Package*' / 'Core (Tctl/Tdie)*'); while LHM is absent the sample carries
cpu_temp_c=null, cpu_temp_source="unavailable" and the cpu_temp alert is a
no-op. Installing LHM (awaiting Musa's OK) lights it up with no code change.

Fail LOUD: an exception inside the monitor itself (DB insert, paging, state
file, unparseable probe output, gh failure) exits non-zero with stderr. A
desktop that does not answer ssh is NOT an error — it is the data point.
Collection errors that still let us store a row (gh failure, unparseable probe
output) are stored first, then the run exits 4 so launchd logs it loudly.

Not lease-gated: detection + operator-facing alerts stay ungated (charter §3).

Usage:
  cubeasht_monitor.py                 # one live run
  cubeasht_monitor.py --dry-run       # collect + evaluate + print; writes NOTHING
  cubeasht_monitor.py --summary --date 2026-10-07 [--tz Asia/Kuala_Lumpur]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

HOST = "cubeasht"
SSH_TARGET = "cubeasht"
WSL_DISTRO = "ci-orchestrator"
GH_REPO = "sheikh-musa/wingmen-orchestrator"
RUNNER_NAME = "cubeasht-orchestrator"
FROM_AGENT = "cc-fleet-health"
TO_AGENT = "orch-console"

PS_TIMEOUT_S = 35   # PowerShell probe measured ~5.5s; budget for a busy box
WSL_TIMEOUT_S = 15
GH_TIMEOUT_S = 10   # total worst case ~60s
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

SAMPLE_INTERVAL_MIN = 5
GRACE_MIN = SAMPLE_INTERVAL_MIN / 2  # launchd jitter tolerance on window coverage
HISTORY_MIN = 60                     # how far back alert evaluation reads
DEFAULT_COOLDOWN_MIN = 60
RETENTION_DAYS = 30
PRUNE_LIMIT = 1000                   # ~3.5 days of samples; bounded per run
DEFAULT_STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "cubeasht_monitor.json"

THRESHOLDS = {
    "cpu_temp_c": 85.0, "cpu_temp_window_min": 10,
    "gpu_temp_c": 85.0, "gpu_temp_window_min": 10,
    "nvme_temp_c": 70.0, "nvme_temp_window_min": 10,
    "disk_c_min_free_pct": 15.0,
    "ram_used_pct": 90.0, "ram_window_min": 15,
    "runner_offline_window_min": 30,
    "unreachable_window_min": 30,
}

SCRIPTS_DIR = Path(__file__).resolve().parent
PS_SCRIPT = SCRIPTS_DIR / "cubeasht_collect.ps1"
WSL_SCRIPT = SCRIPTS_DIR / "cubeasht_collect_wsl.sh"


class MonitorError(RuntimeError):
    """A failure of the monitor itself — must exit non-zero, loudly."""


class ProbeParseError(MonitorError):
    """The desktop answered, but the probe output is not what we expect."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v) -> Optional[float]:
    """Float or None. nvidia-smi prints '[N/A]' / '[Not Supported]' for absent fields."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        return None


def _as_list(v) -> list:
    """PowerShell ConvertTo-Json renders a 1-element array as a bare object."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


# ---------------------------------------------------------------- pure parsers

def parse_ps_output(text: str) -> dict:
    """Extract the JSON object between the CUBEMON_JSON markers."""
    text = text.replace("\0", "")
    m = re.search(r"CUBEMON_JSON_BEGIN\s*(.*?)\s*CUBEMON_JSON_END", text, re.S)
    if not m:
        raise ProbeParseError(f"PowerShell probe output has no CUBEMON_JSON markers: {text[:300]!r}")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ProbeParseError(f"PowerShell probe JSON unparseable: {e}: {m.group(1)[:300]!r}") from e
    if not isinstance(data, dict):
        raise ProbeParseError(f"PowerShell probe JSON is not an object: {type(data).__name__}")
    return data


def parse_nvidia_smi(text: Optional[str]) -> list[dict]:
    """`nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,
    memory.total --format=csv,noheader[,nounits]` → one dict per GPU."""
    gpus = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            raise ProbeParseError(f"nvidia-smi line has {len(parts)} fields, want 5: {line!r}")
        # tolerate the unit-bearing form too ('52', '37 %', '2909 MiB')
        vals = [re.sub(r"\s*(%|MiB)$", "", p) for p in parts[1:]]
        gpus.append({
            "name": parts[0],
            "temp_c": _num(vals[0]),
            "util_pct": _num(vals[1]),
            "mem_used_mb": _num(vals[2]),
            "mem_total_mb": _num(vals[3]),
        })
    return gpus


def extract_cpu_temp(ps: dict) -> tuple[Optional[float], str]:
    """→ (cpu_temp_c, source). LibreHardwareMonitor only; 'unavailable' otherwise.
    Prefers 'CPU Package' over 'Core (Tctl/Tdie)' (AMD exposes one or both)."""
    if ps.get("lhm_error") or "lhm_cpu_temps" not in ps:
        return None, "unavailable"
    sensors = [s for s in _as_list(ps.get("lhm_cpu_temps")) if isinstance(s, dict)]
    for prefix in ("CPU Package", "Core (Tctl/Tdie)"):
        vals = [_num(s.get("value")) for s in sensors if str(s.get("name", "")).startswith(prefix)]
        vals = [v for v in vals if v is not None and v > 0]
        if vals:
            return max(vals), "librehardwaremonitor"
    return None, "unavailable"


def parse_wsl_output(text: str) -> dict:
    """key=value lines + the raw `free -m` block → dict. Missing markers → error."""
    text = text.replace("\0", "")
    if "CUBEMON_WSL_BEGIN" not in text or "CUBEMON_WSL_END" not in text:
        raise ProbeParseError(f"WSL probe output has no CUBEMON_WSL markers: {text[:300]!r}")
    out: dict = {}
    in_free = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == "FREE_BEGIN":
            in_free = True
            continue
        if line == "FREE_END":
            in_free = False
            continue
        if in_free:
            cols = line.split()
            if cols and cols[0] == "Mem:" and len(cols) >= 7:
                total, used, avail = _num(cols[1]), _num(cols[2]), _num(cols[6])
                out["mem_total_mb"], out["mem_used_mb"], out["mem_available_mb"] = total, used, avail
                if total:
                    out["mem_used_pct"] = round((total - avail) / total * 100, 1)
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            if k == "runner_service":
                out["runner_service"] = v.strip() or "unknown"
            elif k == "uptime_s":
                out["uptime_s"] = _num(v)
            elif k == "loadavg":
                out["loadavg"] = [x for x in (_num(p) for p in v.split()) if x is not None]
    return out


def runner_from_api(data: dict, name: str = RUNNER_NAME) -> dict:
    """GitHub /actions/runners response → {name, status, busy}. A runner missing
    from the list is status 'not_registered' (it cannot run CI either)."""
    runners = data.get("runners")
    if not isinstance(runners, list):
        raise MonitorError(f"GitHub runners response missing 'runners' list (keys={sorted(data)})")
    for r in runners:
        if r.get("name") == name:
            return {"name": name, "status": r.get("status"), "busy": bool(r.get("busy"))}
    return {"name": name, "status": "not_registered", "busy": False}


def build_metrics(ps: dict, wsl: Optional[dict], runner: Optional[dict],
                  probe_errors: Optional[dict] = None) -> dict:
    """Normalise one reachable sample into the stored metrics shape."""
    errs = dict(probe_errors or {})
    for k in ("os_error", "cpu_error", "disk_error", "gpu_error", "disks_error", "lhm_error", "vmmem_error"):
        if ps.get(k):
            errs[k.replace("_error", "")] = str(ps[k])[:300]

    m: dict = {"reachable": True}
    m["cpu_name"] = (ps.get("cpu_name") or "").strip() or None
    m["cpu_load_pct"] = _num(ps.get("cpu_load_pct"))
    m["cpu_threads"] = ps.get("cpu_threads")
    m["cpu_temp_c"], m["cpu_temp_source"] = extract_cpu_temp(ps)

    total_kb, free_kb = _num(ps.get("ram_total_kb")), _num(ps.get("ram_free_kb"))
    if total_kb and free_kb is not None:
        m["ram_total_mb"] = round(total_kb / 1024)
        m["ram_used_pct"] = round((total_kb - free_kb) / total_kb * 100, 1)
    else:
        m["ram_total_mb"] = m["ram_used_pct"] = None

    used_b, free_b = _num(ps.get("disk_c_used_bytes")), _num(ps.get("disk_c_free_bytes"))
    if used_b is not None and free_b is not None and (used_b + free_b) > 0:
        tot = used_b + free_b
        m["disk_c_total_gb"] = round(tot / 1e9, 1)
        m["disk_c_free_gb"] = round(free_b / 1e9, 1)
        m["disk_c_free_pct"] = round(free_b / tot * 100, 1)
    else:
        m["disk_c_total_gb"] = m["disk_c_free_gb"] = m["disk_c_free_pct"] = None

    try:
        gpus = parse_nvidia_smi(ps.get("nvidia_smi"))
    except ProbeParseError as e:
        gpus = []
        errs["gpu"] = str(e)[:300]
    m["gpus"] = gpus
    temps = [g["temp_c"] for g in gpus if g["temp_c"] is not None]
    m["gpu_temp_c"] = max(temps) if temps else None
    m["gpu_util_pct"] = max((g["util_pct"] for g in gpus if g["util_pct"] is not None), default=None)

    nvme = []
    for d in _as_list(ps.get("disks")):
        if not isinstance(d, dict):
            continue
        nvme.append({
            "name": d.get("name"), "bus": d.get("bus"),
            "temp_c": _num(d.get("temp_c")), "temp_max_c": _num(d.get("temp_max_c")),
            "wear_pct": _num(d.get("wear_pct")),
        })
    m["disks"] = nvme
    # 0 C is what Windows reports when a disk has no temperature sensor
    nt = [d["temp_c"] for d in nvme if d.get("bus") == "NVMe" and d["temp_c"]]
    m["nvme_temp_c"] = max(nt) if nt else None

    m["last_boot"] = ps.get("last_boot")
    vm = _num(ps.get("vmmem_ws_bytes"))
    m["vmmem_ws_mb"] = round(vm / 1048576) if vm is not None else None
    m["wsl"] = wsl
    m["runner"] = runner
    m["probe_errors"] = errs
    return m


# ---------------------------------------------------------------- alert evaluation

def _m(s: dict) -> dict:
    return s.get("metrics") or {}


def sustained(samples: list[dict], now: datetime, window_min: float,
              pred: Callable[[dict], bool], grace_min: float = GRACE_MIN) -> bool:
    """True iff the last `window_min` minutes are COVERED by samples and EVERY
    in-window sample satisfies pred. A single non-breaching sample (a blip back
    to normal, or a gap in the data like an unreachable sample for a temp alert)
    → False.

    Window = [now - window - grace, now]; covered = the oldest in-window sample
    is no later than now - window + grace. The grace (half a sample interval)
    on BOTH sides absorbs launchd/ssh jitter: with 5-min samples, the sample
    "10 min ago" may really be 10:00.4 ago and must still count."""
    win = _window(samples, now, window_min, grace_min)
    if not win:
        return False
    if min(s["ts"] for s in win) > now - timedelta(minutes=window_min - grace_min):
        return False  # not enough history yet to call it sustained
    return all(pred(s) for s in win)


def _window(samples: list[dict], now: datetime, window_min: float,
            grace_min: float = GRACE_MIN) -> list[dict]:
    start = now - timedelta(minutes=window_min + grace_min)
    return [s for s in samples if start <= s["ts"] <= now]


def _ge(field: str, thr: float) -> Callable[[dict], bool]:
    def p(s: dict) -> bool:
        v = _m(s).get(field)
        return bool(s.get("ok")) and v is not None and v >= thr
    return p


def _window_vals(samples: list[dict], now: datetime, window_min: float, field: str) -> list[float]:
    return [_m(s)[field] for s in _window(samples, now, window_min)
            if _m(s).get(field) is not None]


def evaluate_alerts(samples: list[dict], now: datetime, th: dict = THRESHOLDS) -> list[dict]:
    """samples: [{ts: datetime, ok: bool, metrics: dict, error: str|None}] (any order).
    → findings [{kind, key, ...detail}]."""
    samples = sorted(samples, key=lambda s: s["ts"])
    out: list[dict] = []

    for kind, field, unit in (("cpu_temp", "cpu_temp_c", "C"), ("gpu_temp", "gpu_temp_c", "C"),
                              ("nvme_temp", "nvme_temp_c", "C")):
        thr, win = th[f"{kind}_c"], th[f"{kind}_window_min"]
        if sustained(samples, now, win, _ge(field, thr)):
            vals = _window_vals(samples, now, win, field)
            out.append({"kind": kind, "key": f"{HOST}:{kind}", "threshold": thr, "window_min": win,
                        "latest": vals[-1], "peak": max(vals), "unit": unit})

    ok_samples = [s for s in samples if s.get("ok") and s["ts"] <= now]
    if ok_samples:
        last = ok_samples[-1]
        free = _m(last).get("disk_c_free_pct")
        if free is not None and free < th["disk_c_min_free_pct"]:
            out.append({"kind": "disk_c", "key": f"{HOST}:disk_c", "threshold": th["disk_c_min_free_pct"],
                        "free_pct": free, "free_gb": _m(last).get("disk_c_free_gb"),
                        "total_gb": _m(last).get("disk_c_total_gb")})

    def ram_hi(s: dict) -> bool:
        v = _m(s).get("ram_used_pct")
        return bool(s.get("ok")) and v is not None and v > th["ram_used_pct"]
    if sustained(samples, now, th["ram_window_min"], ram_hi):
        vals = _window_vals(samples, now, th["ram_window_min"], "ram_used_pct")
        out.append({"kind": "ram", "key": f"{HOST}:ram", "threshold": th["ram_used_pct"],
                    "window_min": th["ram_window_min"], "latest": vals[-1], "peak": max(vals)})

    def runner_down(s: dict) -> bool:
        r = _m(s).get("runner") or {}
        return bool(s.get("ok")) and r.get("status") is not None and r.get("status") != "online"
    if sustained(samples, now, th["runner_offline_window_min"], runner_down):
        r = _m(ok_samples[-1]).get("runner") or {}
        wsl = _m(ok_samples[-1]).get("wsl") or {}
        out.append({"kind": "runner_offline", "key": f"{HOST}:runner_offline",
                    "window_min": th["runner_offline_window_min"], "runner": r.get("name"),
                    "status": r.get("status"), "runner_service": wsl.get("runner_service")})

    def unreachable(s: dict) -> bool:
        return (not s.get("ok")) and _m(s).get("reachable") is False
    if sustained(samples, now, th["unreachable_window_min"], unreachable):
        win = _window(samples, now, th["unreachable_window_min"])
        last_ok = ok_samples[-1]["ts"] if ok_samples else None
        out.append({"kind": "unreachable", "key": f"{HOST}:unreachable",
                    "window_min": th["unreachable_window_min"], "failed_samples": len(win),
                    "last_ok": _fmt_ts(last_ok) if last_ok else None,
                    "last_error": (win[-1].get("error") or "")[:200]})
    return out


# ---------------------------------------------------------------- dedup state

def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise MonitorError(f"dedup state {path} unreadable/corrupt: {e} — fix or move it aside") from e
    if not isinstance(data, dict):
        raise MonitorError(f"dedup state {path} is not a JSON object")
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def plan_pages(findings: list[dict], state: dict, now: datetime,
               cooldown_min: int = DEFAULT_COOLDOWN_MIN) -> tuple[list[dict], dict]:
    """→ (findings to page now, new state with resolved keys cleared)."""
    live = {f["key"] for f in findings}
    new_state = {k: v for k, v in state.items() if k in live}
    to_page = [f for f in findings
               if not (new_state.get(f["key"])
                       and now - _parse_ts(new_state[f["key"]]) < timedelta(minutes=cooldown_min))]
    return to_page, new_state


def mark_paged(state: dict, key: str, now: datetime) -> dict:
    s = dict(state)
    s[key] = _fmt_ts(now)
    return s


# ---------------------------------------------------------------- page text

_FOOT = "\n\n(cc-fleet-health cubeasht_monitor; repeats at most hourly while it lasts.)"


def render_page(f: dict) -> tuple[str, str]:
    k = f["kind"]
    if k in ("cpu_temp", "gpu_temp", "nvme_temp"):
        part = {"cpu_temp": "CPU", "gpu_temp": "graphics card (GPU)", "nvme_temp": "SSD (NVMe drive)"}[k]
        short = {"cpu_temp": "CPU", "gpu_temp": "GPU", "nvme_temp": "NVMe SSD"}[k]
        subj = (f"cubeasht {short} running hot: {f['latest']:.0f}C for {f['window_min']}+ min "
                f"(alert at {f['threshold']:.0f}C)")
        body = (
            f"TL;DR: Musa's desktop (cubeasht) {part} has been at or above {f['threshold']:.0f}C "
            f"for the last {f['window_min']} minutes (now {f['latest']:.0f}C, peak {f['peak']:.0f}C).\n\n"
            f"WHAT: every 5-minute sample in the last {f['window_min']} min read >= {f['threshold']:.0f}C.\n\n"
            "WHY IT MATTERS: the desktop runs 24/7 and hosts our CI runner. Sustained heat shortens "
            "hardware life and makes the part slow itself down (throttle), so CI gets slower or flaky.\n\n"
            "WHAT TO DO: check the desktop's airflow/fans (dust, blocked vents, hot room). If a CI job "
            "is hammering it, that may be expected for a while; if it is idle and still hot, look at it."
        )
    elif k == "disk_c":
        subj = f"cubeasht disk C: nearly full: {f['free_pct']:.1f}% free ({f['free_gb']} GB)"
        body = (
            f"TL;DR: Musa's desktop (cubeasht) C: drive has only {f['free_pct']:.1f}% free "
            f"({f['free_gb']} GB of {f['total_gb']} GB); alert below {f['threshold']:.0f}%.\n\n"
            "WHY IT MATTERS: the WSL CI runner lives on C:. When the disk fills, CI builds fail and "
            "Windows itself gets unstable.\n\n"
            "WHAT TO DO: free space (WSL disk image, Docker images, Downloads, old build caches), "
            "or ask cc-fleet-health to look at what grew."
        )
    elif k == "ram":
        subj = f"cubeasht memory nearly full: {f['latest']:.0f}% used for {f['window_min']}+ min"
        body = (
            f"TL;DR: Musa's desktop (cubeasht) has used more than {f['threshold']:.0f}% of its RAM for "
            f"{f['window_min']} minutes (now {f['latest']:.0f}%, peak {f['peak']:.0f}%).\n\n"
            "WHY IT MATTERS: when memory runs out, Windows swaps to disk and everything (including "
            "CI in WSL) crawls or crashes.\n\n"
            "WHAT TO DO: see what is using memory (Task Manager; WSL shows up as 'vmmem'). A stuck CI "
            "job or a forgotten app is the usual cause."
        )
    elif k == "runner_offline":
        subj = f"CI runner {f['runner']} offline for {f['window_min']}+ min while cubeasht is ON"
        body = (
            f"TL;DR: the desktop is on and answering, but GitHub says our CI runner {f['runner']} "
            f"has been '{f['status']}' for {f['window_min']}+ minutes.\n\n"
            f"WHAT: ssh to cubeasht works on every sample, GitHub runner status = {f['status']}; "
            f"WSL runner service state = {f.get('runner_service') or 'unknown'}.\n\n"
            "WHY IT MATTERS: CI jobs for wingmen-orchestrator will sit queued; PRs do not get tested.\n\n"
            "WHAT TO DO: restart the runner service inside WSL (distro ci-orchestrator, "
            "`systemctl restart actions.runner.*`), or check the runner's registration on GitHub."
        )
    elif k == "unreachable":
        subj = f"cubeasht unreachable for {f['window_min']}+ min (likely asleep or off)"
        body = (
            f"TL;DR: Musa's desktop (cubeasht) has not answered for {f['window_min']}+ minutes. "
            "It is most likely asleep, switched off, or off the network.\n\n"
            f"WHAT: all {f['failed_samples']} connection attempts in the last {f['window_min']} min "
            f"failed; last good sample {f['last_ok'] or 'not in the last hour'}. "
            f"Last error: {f['last_error'] or 'n/a'}\n\n"
            "WHY IT MATTERS: it is meant to be on 24/7 — it hosts our CI runner, so CI is stalled "
            "and we cannot see its temperatures.\n\n"
            "WHAT TO DO: wake it / switch it on, and check Windows power settings (sleep should be "
            "'Never'). If it was turned off on purpose, ignore this."
        )
    else:  # pragma: no cover - defensive
        raise MonitorError(f"unknown finding kind {k!r}")
    return subj, body + _FOOT


def _send_page(f: dict) -> int:
    sys.path.insert(0, str(SCRIPTS_DIR.parent))
    from scripts.bus_send import send  # lazy: psycopg2/DB only on the live paging path

    subj, body = render_page(f)
    row_id, _thread = send(from_agent=FROM_AGENT, to=TO_AGENT, mtype="update",
                           subject=subj, body=body, priority="P1", req=True)
    return row_id


# ---------------------------------------------------------------- daily summary

def _stats(vals: list[float]) -> Optional[dict]:
    if not vals:
        return None
    return {"min": round(min(vals), 1), "avg": round(sum(vals) / len(vals), 1),
            "max": round(max(vals), 1), "n": len(vals)}


def day_bounds(day: date, tz: str = "UTC") -> tuple[datetime, datetime]:
    from zoneinfo import ZoneInfo
    z = ZoneInfo(tz)
    start = datetime.combine(day, time.min, tzinfo=z)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def summarize(samples: list[dict], day: date, tz: str = "UTC") -> dict:
    """min/avg/max for GPU/NVMe(/CPU when present) temp and CPU load — overall,
    during CI (runner busy) and idle — for samples within `day` in `tz`."""
    lo, hi = day_bounds(day, tz)
    inday = [s for s in samples if lo <= s["ts"] < hi]
    ok = [s for s in inday if s.get("ok")]

    def vals(rows, field):
        return [float(_m(s)[field]) for s in rows if _m(s).get(field) is not None]

    busy = [s for s in ok if (_m(s).get("runner") or {}).get("busy") is True]
    idle = [s for s in ok if (_m(s).get("runner") or {}).get("busy") is False]
    out = {
        "host": HOST, "date": day.isoformat(), "tz": tz,
        "samples": len(inday), "reachable_samples": len(ok),
        "unreachable_samples": sum(1 for s in inday if _m(s).get("reachable") is False),
        "gpu_temp_c": _stats(vals(ok, "gpu_temp_c")),
        "nvme_temp_c": _stats(vals(ok, "nvme_temp_c")),
        "cpu_load_pct": {
            "overall": _stats(vals(ok, "cpu_load_pct")),
            "ci_busy": _stats(vals(busy, "cpu_load_pct")),
            "ci_idle": _stats(vals(idle, "cpu_load_pct")),
        },
        "ram_used_pct": _stats(vals(ok, "ram_used_pct")),
        "ci_busy_samples": len(busy),
    }
    cpu_t = vals(ok, "cpu_temp_c")
    if cpu_t:
        out["cpu_temp_c"] = _stats(cpu_t)
    return out


# ---------------------------------------------------------------- DB

def connect():
    import psycopg2
    sys.path.insert(0, str(SCRIPTS_DIR.parent))
    from scripts.bus_send import dburl
    return psycopg2.connect(dburl(os.environ))


def insert_sample(conn, host: str, ok: bool, metrics: dict, error: Optional[str]) -> tuple[int, datetime]:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO host_metrics (host, metrics, ok, error) VALUES (%s, %s::jsonb, %s, %s) "
            "RETURNING id, ts",
            (host, json.dumps(metrics), ok, error),
        )
        row_id, ts = cur.fetchone()
    conn.commit()
    return row_id, ts


def fetch_samples(conn, host: str, since: datetime, until: Optional[datetime] = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts, ok, metrics, error FROM host_metrics WHERE host=%s AND ts >= %s "
            "AND (%s::timestamptz IS NULL OR ts < %s::timestamptz) ORDER BY ts",
            (host, since, until, until),
        )
        rows = cur.fetchall()
    conn.rollback()  # read-only; end the transaction cleanly
    return [{"ts": r[0], "ok": r[1],
             "metrics": r[2] if isinstance(r[2], dict) else json.loads(r[2]), "error": r[3]}
            for r in rows]


def prune(conn, host: str = HOST, days: int = RETENTION_DAYS, limit: int = PRUNE_LIMIT) -> int:
    """Delete at most `limit` of this host's rows older than `days` (oldest first)."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM host_metrics WHERE id IN ("
            " SELECT id FROM host_metrics WHERE host=%s AND ts < now() - make_interval(days => %s)"
            " ORDER BY ts LIMIT %s)",
            (host, days, limit),
        )
        n = cur.rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------- collection (I/O)

def _ssh(remote_cmd: str, stdin_path: Path, timeout: int) -> subprocess.CompletedProcess:
    with open(stdin_path, "rb") as fh:
        cp = subprocess.run(["ssh", *SSH_OPTS, SSH_TARGET, remote_cmd],
                            stdin=fh, capture_output=True, timeout=timeout)
    return subprocess.CompletedProcess(
        cp.args, cp.returncode,
        cp.stdout.replace(b"\0", b"").decode("utf-8", "replace"),
        cp.stderr.replace(b"\0", b"").decode("utf-8", "replace"))


def fetch_runner() -> dict:
    path = f"repos/{GH_REPO}/actions/runners?per_page=100"
    try:
        cp = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", path],
                            capture_output=True, text=True, timeout=GH_TIMEOUT_S)
    except FileNotFoundError as e:
        raise MonitorError(f"gh CLI not found on PATH: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise MonitorError(f"gh api {path} timed out after {GH_TIMEOUT_S}s") from e
    if cp.returncode != 0:
        raise MonitorError(f"gh api {path} failed rc={cp.returncode}: {(cp.stderr or cp.stdout).strip()[:300]}")
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError as e:
        raise MonitorError(f"gh api {path} unparseable: {cp.stdout[:200]!r}") from e
    return runner_from_api(data)


def collect() -> tuple[bool, dict, Optional[str], list[str]]:
    """→ (ok, metrics, error, loud_errors). Never raises for an unreachable host."""
    loud: list[str] = []
    try:
        runner = fetch_runner()
    except MonitorError as e:
        runner = None
        loud.append(f"runner status: {e}")

    try:
        cp = _ssh("powershell -NoProfile -ExecutionPolicy Bypass -Command -", PS_SCRIPT, PS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, {"reachable": False, "runner": runner}, \
            f"ssh/powershell timed out after {PS_TIMEOUT_S}s", loud
    if cp.returncode == 255:  # ssh's own failure code: connect/auth/network
        return False, {"reachable": False, "runner": runner}, \
            f"ssh unreachable (rc=255): {cp.stderr.strip()[-200:]}", loud
    try:
        ps = parse_ps_output(cp.stdout)
    except ProbeParseError as e:
        loud.append(str(e))
        return False, {"reachable": True, "runner": runner}, \
            f"probe output unparseable (rc={cp.returncode}): {e}"[:500], loud

    probe_errors: dict = {}
    wsl = None
    try:
        wcp = _ssh(f"wsl -d {WSL_DISTRO} -u root -- bash -s", WSL_SCRIPT, WSL_TIMEOUT_S)
        wsl = parse_wsl_output(wcp.stdout)
    except subprocess.TimeoutExpired:
        probe_errors["wsl"] = f"wsl probe timed out after {WSL_TIMEOUT_S}s"
    except ProbeParseError as e:  # distro stopped/missing: data, not a monitor bug
        probe_errors["wsl"] = str(e)[:300]
    return True, build_metrics(ps, wsl, runner, probe_errors), None, loud


# ---------------------------------------------------------------- main

def _brief(m: dict) -> str:
    if not m.get("reachable"):
        return "UNREACHABLE"
    r = m.get("runner") or {}
    w = m.get("wsl") or {}
    return (f"cpu={m.get('cpu_load_pct')}% cpu_temp={m.get('cpu_temp_c')}({m.get('cpu_temp_source')}) "
            f"gpu={m.get('gpu_temp_c')}C/{m.get('gpu_util_pct')}% nvme={m.get('nvme_temp_c')}C "
            f"ram={m.get('ram_used_pct')}% diskC_free={m.get('disk_c_free_pct')}% "
            f"wsl_mem={w.get('mem_used_pct')}% runner={r.get('status')}/busy={r.get('busy')} "
            f"svc={w.get('runner_service')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="collect+evaluate+print; insert/page/prune NOTHING")
    p.add_argument("--summary", action="store_true", help="print the daily summary JSON for --date")
    p.add_argument("--date", help="YYYY-MM-DD for --summary")
    p.add_argument("--tz", default="UTC", help="timezone the --date day is cut in (default UTC)")
    p.add_argument("--cooldown-min", type=int, default=DEFAULT_COOLDOWN_MIN)
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    return p


def run_summary(args) -> int:
    if not args.date:
        raise MonitorError("--summary needs --date YYYY-MM-DD")
    day = date.fromisoformat(args.date)
    lo, hi = day_bounds(day, args.tz)
    conn = connect()
    try:
        samples = fetch_samples(conn, HOST, lo, hi)
    finally:
        conn.close()
    print(json.dumps(summarize(samples, day, args.tz), indent=2))
    return 0


def run_once(args) -> int:
    stamp = _fmt_ts(_now())
    ok, metrics, error, loud = collect()
    print(f"[{stamp}] {HOST} ok={ok} {_brief(metrics)}" + (f" error={error}" if error else ""))
    if metrics.get("probe_errors"):
        print(f"  probe notes: {json.dumps(metrics['probe_errors'])}")

    if args.dry_run:
        print("  sample: " + json.dumps(metrics, sort_keys=True))
        now = _now()
        history: list[dict] = []
        try:
            conn = connect()
            try:
                history = fetch_samples(conn, HOST, now - timedelta(minutes=HISTORY_MIN))
            finally:
                conn.close()
        except Exception as e:  # dry-run only: table may not be applied yet
            print(f"  (dry-run: history unavailable — {type(e).__name__}: {str(e).splitlines()[0][:160]}; "
                  "evaluating the current sample alone)")
        samples = history + [{"ts": now, "ok": ok, "metrics": metrics, "error": error}]
        findings = evaluate_alerts(samples, now)
        to_page, _ = plan_pages(findings, load_state(args.state_file), now, args.cooldown_min)
        print(f"  thresholds: {json.dumps(THRESHOLDS)}")
        print(f"  findings={len(findings)} to_page={len(to_page)} (history samples={len(history)})")
        for f in findings:
            subj, _ = render_page(f)
            tag = "WOULD PAGE" if f in to_page else "suppressed(cooldown)"
            print(f"  {tag} [{f['kind']}] -> {TO_AGENT} P1 rr: {subj}")
        for msg in loud:
            print(f"  LOUD (would exit 4): {msg}")
        print("  (dry-run: nothing inserted, paged, pruned or written)")
        return 0

    conn = connect()
    try:
        row_id, ts = insert_sample(conn, HOST, ok, metrics, error)
        print(f"  inserted host_metrics #{row_id}")
        samples = fetch_samples(conn, HOST, ts - timedelta(minutes=HISTORY_MIN), ts + timedelta(seconds=1))
        findings = evaluate_alerts(samples, ts)
        state = load_state(args.state_file)
        to_page, new_state = plan_pages(findings, state, ts, args.cooldown_min)
        print(f"  findings={len(findings)} to_page={len(to_page)} "
              f"suppressed(cooldown)={len(findings) - len(to_page)}")
        if new_state != state:
            save_state(args.state_file, new_state)  # resolved keys cleared
        for f in to_page:
            bus_id = _send_page(f)
            new_state = mark_paged(new_state, f["key"], ts)
            save_state(args.state_file, new_state)
            print(f"  PAGED [{f['kind']}] -> {TO_AGENT} bus #{bus_id}")
        n = prune(conn)
        if n:
            print(f"  pruned {n} host_metrics row(s) older than {RETENTION_DAYS}d")
    finally:
        conn.close()

    if loud:
        for msg in loud:
            print(f"cubeasht_monitor: FAIL LOUD (sample stored) — {msg}", file=sys.stderr)
        return 4
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_summary(args) if args.summary else run_once(args)
    except MonitorError as e:
        print(f"cubeasht_monitor: FAIL LOUD — {e}", file=sys.stderr)
        return 2
    except Exception as e:  # DB/paging/state failure: still loud, still non-zero
        print(f"cubeasht_monitor: FAIL LOUD — {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
