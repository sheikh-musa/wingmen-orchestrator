#!/usr/bin/env python3
"""cubeasht_job_memory_report.py — correlate cubeasht's real (WSL-internal)
memory use with GitHub Actions job activity over a window.

Why (orch-console bus #83699, op#35970): Musa is sizing a new Mac mini at
32GB to host all lanes plus CI. He wants to know cubeasht's actual CI memory
footprint, not the Windows-level figure that counts reclaimable vmmemWSL
page cache as "used" (see cc-fleet-health PR#658 — that exact confusion is
why cubeasht_monitor's RAM alert itself needed fixing). Over a window:
  - peak WSL-internal used memory (the real figure; `free -m` used, not
    buff/cache) -- overall, and separately DURING busy windows
  - the 95th percentile WSL-internal used memory during busy windows
  - the largest single job, two ways: by DURATION, and separately whichever
    job's time range contains the single sample with peak memory
  - whether two cubeasht-labeled jobs EVER overlapped in wall-clock time --
    both cubeasht-orchestrator and cubeasht-orchestrator-2 run on the SAME
    physical desktop, so an overlap is genuine concurrent memory pressure
    on one box, directly relevant to a sizing decision (not just queueing)

Data sources:
  - host_metrics (host='cubeasht'): the existing 5-min cubeasht_monitor.py
    collector already samples wsl.mem_used_pct/mem_used_mb + runner.busy;
    reuses cubeasht_monitor.connect()/fetch_samples() rather than
    re-deriving (memory ddl-detect-vendored-shared-module) -- no new
    collector, no new launchd job.
  - GitHub Actions API (gh CLI): jobs whose runner_name starts with
    "cubeasht" (today: cubeasht-orchestrator, cubeasht-orchestrator-2 --
    discovered live via the runners endpoint, never hardcoded as a closed
    set) and whose [started_at, completed_at-or-now) overlaps the window.

Fail LOUD, never guess (Musa's standing zero-waste directive): every figure
that cannot be computed from real data is the literal string
"could not measure" with the reason -- never interpolated, never a
placeholder number. A genuinely empty window (e.g. no busy samples) is
reported as "0 busy samples in window", which is DATA, not an error.

Usage:
  cubeasht_job_memory_report.py --since 2026-10-30T06:51:00Z --hours 24
  cubeasht_job_memory_report.py --since <iso> --until <iso> --out report.json
  cubeasht_job_memory_report.py --hours 24          # window ending now (smoke test)
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR.parent))
from scripts import cubeasht_monitor as mon  # noqa: E402

REPO = "sheikh-musa/wingmen-orchestrator"
LABEL = "cubeasht"
GH_TIMEOUT_S = 60
PER_PAGE = 100
MAX_PAGES = 50  # refuse rather than silently truncate past this


class ReportError(RuntimeError):
    """Any failure severe enough that the caller must not trust the output."""


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- gh access

def gh_api_paginated(path_no_page: str, list_field: str) -> list[dict]:
    """GET every page of `path_no_page` (which must NOT already have a page=
    param), concatenating `list_field`. Raises ReportError on any gh/parse
    failure or if MAX_PAGES is exhausted (fail loud, never a silent partial
    view)."""
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in path_no_page else "?"
        path = f"{path_no_page}{sep}page={page}"
        try:
            cp = subprocess.run(
                ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
                capture_output=True, text=True, timeout=GH_TIMEOUT_S,
            )
        except FileNotFoundError as e:
            raise ReportError(f"gh CLI not found: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise ReportError(f"gh api {path} timed out after {GH_TIMEOUT_S}s") from e
        if cp.returncode != 0:
            raise ReportError(f"gh api {path} failed (rc={cp.returncode}): "
                               f"{(cp.stderr or cp.stdout).strip()[:500]}")
        try:
            data = json.loads(cp.stdout)
        except json.JSONDecodeError as e:
            raise ReportError(f"gh api {path} returned unparseable output: {cp.stdout[:200]!r}") from e
        if not isinstance(data, dict) or not isinstance(data.get(list_field), list):
            raise ReportError(f"gh api {path}: missing list field {list_field!r} (keys={sorted(data) if isinstance(data, dict) else type(data)})")
        batch = data[list_field]
        out.extend(batch)
        if len(batch) < PER_PAGE:
            return out
    raise ReportError(f"gh api {path_no_page}: hit MAX_PAGES={MAX_PAGES} without exhausting "
                       "the list -- refusing a silently-truncated view")


def fetch_cubeasht_runner_names(repo: str = REPO) -> list[str]:
    """Runner names currently carrying the 'cubeasht' label -- discovered live,
    never a hardcoded closed set (today: cubeasht-orchestrator,
    cubeasht-orchestrator-2, both on the one physical desktop)."""
    runners = gh_api_paginated(f"repos/{repo}/actions/runners?per_page={PER_PAGE}", "runners")
    names = [r.get("name") for r in runners
             if LABEL in [(l.get("name") if isinstance(l, dict) else l) for l in (r.get("labels") or [])]]
    if not names:
        raise ReportError(f"no runner currently carries the '{LABEL}' label -- cannot scope jobs")
    return names


def fetch_jobs_overlapping_window(since: datetime, until: datetime,
                                   runner_names: list[str], repo: str = REPO) -> list[dict]:
    """Every job on one of `runner_names` whose [started_at, completed_at-or-now)
    overlaps [since, until). Scans completed + in_progress + queued runs
    CREATED up to 12h before `since` (a job can start well after its run is
    created if it queued) through `until`, so a run created just before the
    window but still running INTO it is not missed."""
    scan_from = since - timedelta(hours=12)
    now = datetime.now(timezone.utc)
    jobs: list[dict] = []
    seen_runs: set[int] = set()
    for status_q in ("completed", "in_progress", "queued"):
        path = (f"repos/{repo}/actions/runs?status={status_q}&per_page={PER_PAGE}"
                f"&created=%3E%3D{scan_from.strftime('%Y-%m-%dT%H:%M:%S')}")
        for run in gh_api_paginated(path, "workflow_runs"):
            rid = run.get("id")
            if not isinstance(rid, int) or rid in seen_runs:
                continue
            created_at = run.get("created_at")
            if created_at and _parse_ts(created_at) > until:
                continue
            seen_runs.add(rid)
            jpath = f"repos/{repo}/actions/runs/{rid}/jobs?per_page={PER_PAGE}"
            for j in gh_api_paginated(jpath, "jobs"):
                if j.get("runner_name") not in runner_names:
                    continue
                started = j.get("started_at")
                if not started:
                    continue  # never started (still queued) -- no memory footprint yet
                j_start = _parse_ts(started)
                completed = j.get("completed_at")
                j_end = _parse_ts(completed) if completed else now
                if j_start < until and j_end > since:
                    jobs.append(j)
    return jobs


# ---------------------------------------------------------------- pure analysis

def wsl_used_mb(sample: dict) -> Optional[float]:
    if not sample.get("ok"):
        return None
    v = (sample.get("metrics") or {}).get("wsl") or {}
    return v.get("mem_used_mb")


def wsl_used_pct(sample: dict) -> Optional[float]:
    if not sample.get("ok"):
        return None
    v = (sample.get("metrics") or {}).get("wsl") or {}
    return v.get("mem_used_pct")


def sample_is_busy(sample: dict) -> bool:
    if not sample.get("ok"):
        return False
    r = (sample.get("metrics") or {}).get("runner") or {}
    return r.get("busy") is True


def percentile(vals: list[float], pct: float) -> float:
    """Nearest-rank percentile -- no numpy dependency, exact on the sorted list."""
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return s[k]


def job_duration_min(j: dict) -> Optional[float]:
    started, completed = j.get("started_at"), j.get("completed_at")
    if not started or not completed:
        return None
    return (_parse_ts(completed) - _parse_ts(started)).total_seconds() / 60


def job_window(j: dict, now: datetime) -> tuple[datetime, datetime]:
    start = _parse_ts(j["started_at"])
    end = _parse_ts(j["completed_at"]) if j.get("completed_at") else now
    return start, end


def find_overlaps(jobs: list[dict], now: datetime) -> list[tuple[dict, dict]]:
    """Every pair of jobs whose wall-clock windows overlap, regardless of which
    runner each ran on -- both runners share ONE physical desktop, so any
    overlap is real concurrent memory pressure."""
    timed = [j for j in jobs if j.get("started_at")]
    out: list[tuple[dict, dict]] = []
    for i, a in enumerate(timed):
        a_start, a_end = job_window(a, now)
        for b in timed[i + 1:]:
            if a.get("id") == b.get("id"):
                continue
            b_start, b_end = job_window(b, now)
            if a_start < b_end and b_start < a_end:
                out.append((a, b))
    return out


def job_containing(jobs: list[dict], ts: datetime, now: datetime) -> list[dict]:
    out = []
    for j in jobs:
        if not j.get("started_at"):
            continue
        start, end = job_window(j, now)
        if start <= ts <= end:
            out.append(j)
    return out


# ---------------------------------------------------------------- report

def build_report(since: datetime, until: datetime) -> dict:
    now = datetime.now(timezone.utc)
    out: dict = {"since": _fmt_ts(since), "until": _fmt_ts(until), "generated_at": _fmt_ts(now)}

    conn_err = None
    samples: list[dict] = []
    try:
        conn = mon.connect()
        samples = mon.fetch_samples(conn, mon.HOST, since, until)
    except Exception as e:  # noqa: BLE001
        conn_err = f"{type(e).__name__}: {e}"

    if conn_err:
        out["memory"] = {"error": f"could not measure -- host_metrics query failed: {conn_err}"}
    else:
        ok_samples = [s for s in samples if s.get("ok")]
        all_mb = [v for v in (wsl_used_mb(s) for s in ok_samples) if v is not None]
        all_pct = [v for v in (wsl_used_pct(s) for s in ok_samples) if v is not None]
        busy_samples = [s for s in ok_samples if sample_is_busy(s)]
        busy_mb = [v for v in (wsl_used_mb(s) for s in busy_samples) if v is not None]
        busy_pct = [v for v in (wsl_used_pct(s) for s in busy_samples) if v is not None]

        mem: dict = {"samples_total": len(samples), "samples_ok": len(ok_samples),
                     "samples_busy": len(busy_samples)}
        mem["overall_peak_mb"] = max(all_mb) if all_mb else "could not measure -- no ok samples with wsl.mem_used_mb in window"
        mem["overall_peak_pct"] = max(all_pct) if all_pct else "could not measure -- no ok samples with wsl.mem_used_pct in window"
        if busy_mb:
            mem["busy_peak_mb"] = max(busy_mb)
            mem["busy_p95_mb"] = percentile(busy_mb, 95)
        else:
            mem["busy_peak_mb"] = mem["busy_p95_mb"] = "could not measure -- 0 busy samples in window"
        if busy_pct:
            mem["busy_peak_pct"] = max(busy_pct)
            mem["busy_p95_pct"] = percentile(busy_pct, 95)
        else:
            mem["busy_peak_pct"] = mem["busy_p95_pct"] = "could not measure -- 0 busy samples in window"

        # the sample carrying the overall peak, for job-correlation below
        peak_sample = None
        if all_mb:
            peak_val = max(all_mb)
            peak_sample = next(s for s in ok_samples if wsl_used_mb(s) == peak_val)
        mem["_peak_sample_ts"] = peak_sample["ts"].isoformat() if peak_sample else None
        out["memory"] = mem

    gh_err = None
    jobs: list[dict] = []
    runner_names: list[str] = []
    try:
        runner_names = fetch_cubeasht_runner_names()
        jobs = fetch_jobs_overlapping_window(since, until, runner_names)
    except ReportError as e:
        gh_err = str(e)

    if gh_err:
        out["jobs"] = {"error": f"could not measure -- GitHub Actions query failed: {gh_err}"}
    else:
        timed_jobs = [j for j in jobs if job_duration_min(j) is not None]
        longest = max(timed_jobs, key=job_duration_min) if timed_jobs else None
        overlaps = find_overlaps(jobs, now)

        at_peak: list[dict] = []
        peak_ts_iso = out.get("memory", {}).get("_peak_sample_ts")
        if peak_ts_iso:
            at_peak = job_containing(jobs, _parse_ts(peak_ts_iso), now)

        out["jobs"] = {
            "runner_names_scoped": runner_names,
            "jobs_overlapping_window": len(jobs),
            "longest_job_by_duration": (
                {"name": longest["name"], "run_id": longest.get("run_id"),
                 "runner_name": longest.get("runner_name"),
                 "duration_min": round(job_duration_min(longest), 1),
                 "started_at": longest.get("started_at"), "completed_at": longest.get("completed_at")}
                if longest else "could not measure -- no job in window has both started_at and completed_at"
            ),
            "jobs_at_memory_peak": (
                [{"name": j["name"], "runner_name": j.get("runner_name"),
                  "started_at": j.get("started_at"), "completed_at": j.get("completed_at")} for j in at_peak]
                if at_peak else ("could not measure -- no overall memory peak to correlate"
                                 if not peak_ts_iso else "no job covers the peak-memory timestamp")
            ),
            "jobs_ever_overlapped": bool(overlaps),
            "overlap_examples": [
                {"a": a["name"], "a_runner": a.get("runner_name"),
                 "b": b["name"], "b_runner": b.get("runner_name"),
                 "a_window": [a.get("started_at"), a.get("completed_at")],
                 "b_window": [b.get("started_at"), b.get("completed_at")]}
                for a, b in overlaps[:10]
            ],
        }

    if "_peak_sample_ts" in out.get("memory", {}):
        del out["memory"]["_peak_sample_ts"]
    out["findings"] = build_findings(out)
    return out


def build_findings(out: dict) -> list[str]:
    """Qualitative, human-readable findings derived from the measured data
    above -- never a NEW independent claim, only an interpretation of fields
    already in `out`."""
    findings: list[str] = []
    jobs = out.get("jobs", {})
    if isinstance(jobs.get("runner_names_scoped"), list) and len(jobs["runner_names_scoped"]) > 1:
        n = jobs["jobs_overlapping_window"]
        overlapped = jobs.get("jobs_ever_overlapped")
        findings.append(
            f"orch-console #83714: cubeasht's {len(jobs['runner_names_scoped'])} CI runners "
            f"({', '.join(jobs['runner_names_scoped'])}) are both self-hosted services on the "
            f"SAME physical desktop (per cubeasht_monitor.py's own scope docs, not re-measured "
            f"here) -- it is the ONLY CI path for the label 'cubeasht'. A desktop outage or "
            f"reboot stops CI entirely for every job carrying that label; a 32GB Mac mini "
            f"replacing it would inherit that single-point-of-failure shape unless a second "
            f"physical host is added. "
            + (f"In this window, jobs on the two runners overlapped in wall-clock time "
               f"(genuine CONCURRENT memory pressure on one box, not just queueing) -- see "
               f"overlap_examples." if overlapped else
               f"In this window, the {n} jobs scanned never overlapped in wall-clock time, "
               f"so measured peaks reflect at most one job running at a time; a sizing decision "
               f"based on this window alone would UNDERSTATE worst-case concurrent memory use "
               f"if the two runners ever do pick up overlapping jobs.")
        )
    return findings


def render_markdown(report: dict) -> str:
    mem, jobs = report.get("memory", {}), report.get("jobs", {})
    lines = [
        f"# cubeasht CI memory report ({report['since']} -> {report['until']})",
        f"generated {report['generated_at']}",
        "",
        "## Memory (WSL-internal, real figure -- not Windows cache-inflated)",
        f"- samples: {mem.get('samples_total')} total, {mem.get('samples_ok')} ok, {mem.get('samples_busy')} busy",
        f"- overall peak: {mem.get('overall_peak_mb')} MB ({mem.get('overall_peak_pct')}%)",
        f"- busy-window peak: {mem.get('busy_peak_mb')} MB ({mem.get('busy_peak_pct')}%)",
        f"- busy-window p95: {mem.get('busy_p95_mb')} MB ({mem.get('busy_p95_pct')}%)",
        "",
        "## Jobs (GitHub Actions, cubeasht-labeled runners)",
        f"- runners scoped: {jobs.get('runner_names_scoped')}",
        f"- jobs overlapping window: {jobs.get('jobs_overlapping_window')}",
        f"- longest job by duration: {jobs.get('longest_job_by_duration')}",
        f"- job(s) running at the memory peak: {jobs.get('jobs_at_memory_peak')}",
        f"- did two jobs ever overlap (same physical desktop)?: {jobs.get('jobs_ever_overlapped')}",
    ]
    if jobs.get("overlap_examples"):
        lines.append("- overlap examples:")
        for ex in jobs["overlap_examples"]:
            lines.append(f"  - {ex['a']} ({ex['a_runner']}) {ex['a_window']} overlaps "
                         f"{ex['b']} ({ex['b_runner']}) {ex['b_window']}")
    if report.get("findings"):
        lines.append("")
        lines.append("## Findings")
        for f in report["findings"]:
            lines.append(f"- {f}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", help="ISO8601 window start (default: --hours before --until)")
    p.add_argument("--until", help="ISO8601 window end (default: now)")
    p.add_argument("--hours", type=float, default=24.0, help="window length in hours if --since omitted")
    p.add_argument("--out", help="write JSON report to this path")
    p.add_argument("--md-out", help="write markdown report to this path")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    until = _parse_ts(args.until) if args.until else datetime.now(timezone.utc)
    since = _parse_ts(args.since) if args.since else until - timedelta(hours=args.hours)

    report = build_report(since, until)
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text)
    if args.md_out:
        Path(args.md_out).write_text(render_markdown(report))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
