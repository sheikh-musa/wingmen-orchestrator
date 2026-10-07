#!/usr/bin/env python3
"""ci_queue_watchdog.py — page when a self-hosted CI runner label is STUCK.

Why (cc-fleet-health, orch-console bus #57952): wingmen-orchestrator's
"Lint & Test" job runs on the self-hosted runner `cubeasht-orchestrator`
(labels self-hosted, Linux, X64, cubeasht, win-wsl) on an operator's Windows
desktop (WSL). When that desktop is off or asleep, jobs do not FAIL — they
QUEUE silently, forever. Nobody watches GitHub Actions queues, so a PR can sit
un-tested for hours. This watchdog closes that gap.

v2 — busy-aware (orch-console #58373, after FALSE pages #58367/#58368): v1
paged per job on "queued > 30 min". But cubeasht is ONE serial runner (~8 min
per job); with 1 running + 3 queued, the 3rd job legitimately waits ~24 min.
That is throughput, not an outage. v2 reads runner state first and pages only
on a genuine stall, ONE collapsed page per (repo, label):

  no_runner      no registered runner carries --label, jobs queued.
  runner_offline every --label runner is offline, jobs queued
                 ("runner offline (desktop may be off/asleep)").
  stuck_pickup   a --label runner is online + IDLE, jobs queued, and no --label
                 job started/finished within --grace-min
                 ("runner online but not picking up jobs"). The activity check
                 stops a runner caught idle for a moment between two jobs from
                 being reported as stuck.
  not_draining   all online --label runners are BUSY and the oldest queued job
                 is older than ETA + --threshold-min, where
                 ETA = ceil(N / online runners) x avg job minutes, N = queue depth,
                 avg = mean(completed_at - started_at) of the last 10 completed
                 (success/failure) --label jobs, fallback 8 min
                 ("runner busy, queue depth N, ETA ~X min — not draining").

  Runner counting is per LABEL across ALL runners carrying it (as of 2026-10-07
  cubeasht has two, cubeasht-orchestrator and cubeasht-orchestrator-2):
  capacity = online runners; runner_offline only when EVERY one is offline; one
  offline + one online-busy is degraded capacity, which is mentioned in the
  not_draining page and is NOT paged on its own while the queue drains.

  The offline / stuck / no-runner cases only fire once the oldest queued job is
  older than --grace-min (default 5), so a job queued seconds ago while the
  runner reconnects or picks it up doesn't page. Offline with nothing queued is
  only LOGGED, because the desktop being off overnight is expected.

Age field: job `created_at` (when GitHub enqueued it). On a still-queued job
`started_at` is null or a placeholder, so it can't measure queue wait.

Paging: one bus page per (repo, label) per --cooldown-min (default 60), P1 +
requires_response, cc-fleet-health → orch-console, via scripts/bus_send.send(),
listing every queued job. Dedup state is a JSON file
{key: {"ts": last_paged_utc, "kind": ...}}. A key is CLEARED when the finding
resolves. v1 string values are still read.

Fail LOUD: any gh error, non-JSON output, or unexpected shape raises
WatchdogError → exit 2 with a clear stderr message. A monitor that silently
reports "nothing queued" because its query failed is worse than no monitor.
A paging failure exits 3. State for pages already sent is kept, so the next run
retries only what failed.

--dry-run: scan + print what WOULD be paged; never sends, never writes state.

Not lease-gated: this is detection + an operator-facing alert (charter §3:
detection and alerts stay ungated; only self-healing actions are gated).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_REPO = "sheikh-musa/wingmen-orchestrator"
DEFAULT_LABEL = "cubeasht"
DEFAULT_THRESHOLD_MIN = 30
DEFAULT_GRACE_MIN = 5
DEFAULT_COOLDOWN_MIN = 60
FALLBACK_AVG_MIN = 8.0
AVG_SAMPLE = 10
COMPLETED_RUNS_SCAN = 20
DEFAULT_STATE_FILE = Path.home() / "wingmen" / "fleet-health" / "state" / "ci_queue_watchdog.json"
FROM_AGENT = "cc-fleet-health"
TO_AGENT = "orch-console"
RUN_STATUSES = ("queued", "in_progress")
GH_TIMEOUT_S = 60
PER_PAGE = 100


class WatchdogError(RuntimeError):
    """Any failure that must make the watchdog exit non-zero, loudly."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- gh access

def gh_api(path: str) -> dict:
    """`gh api <path>` → parsed JSON object. Raises WatchdogError on ANY failure."""
    try:
        cp = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
            capture_output=True, text=True, timeout=GH_TIMEOUT_S,
        )
    except FileNotFoundError as e:
        raise WatchdogError(f"gh CLI not found on PATH ({os.environ.get('PATH')!r}): {e}") from e
    except subprocess.TimeoutExpired as e:
        raise WatchdogError(f"gh api {path} timed out after {GH_TIMEOUT_S}s") from e
    if cp.returncode != 0:
        raise WatchdogError(
            f"gh api {path} failed (rc={cp.returncode}): {(cp.stderr or cp.stdout).strip()[:500]}"
        )
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError as e:
        raise WatchdogError(f"gh api {path} returned unparseable output: {cp.stdout[:200]!r}") from e
    if not isinstance(data, dict):
        raise WatchdogError(f"gh api {path} returned unparseable output (not an object): {type(data)}")
    return data


def _list_field(data: dict, field: str, path: str, complete: bool = True) -> list:
    v = data.get(field)
    if not isinstance(v, list):
        raise WatchdogError(f"gh api {path}: response missing list field {field!r} (keys={sorted(data)})")
    total = data.get("total_count")
    # Truncation only if the page came back FULL. GitHub's status-filtered lists can
    # report total_count above the items actually returned for a moment (count lags the
    # list; seen live 2026-10-07: "total_count=3 > page size 2"); a short page means
    # nothing was cut off, so that must not fail the run.
    if complete and isinstance(total, int) and total > len(v) and len(v) >= PER_PAGE:
        # Not silent truncation: say so loudly. >100 active runs/jobs on one repo
        # would itself be an anomaly worth a human look.
        raise WatchdogError(
            f"gh api {path}: total_count={total} > page size {len(v)}; pagination not "
            "implemented — refusing to report a partial view"
        )
    return v


def fetch_label_jobs(repo: str) -> list[dict]:
    """All jobs of every queued/in_progress run in `repo` (unfiltered)."""
    jobs: list[dict] = []
    seen_runs: set[int] = set()
    for status in RUN_STATUSES:
        path = f"repos/{repo}/actions/runs?status={status}&per_page={PER_PAGE}"
        for run in _list_field(gh_api(path), "workflow_runs", path):
            rid = run.get("id")
            if not isinstance(rid, int):
                raise WatchdogError(f"gh api {path}: run without integer id: {run!r:.200}")
            if rid in seen_runs:
                continue
            seen_runs.add(rid)
            jpath = f"repos/{repo}/actions/runs/{rid}/jobs?per_page={PER_PAGE}"
            jobs.extend(_list_field(gh_api(jpath), "jobs", jpath))
    return jobs


def fetch_recent_completed_jobs(repo: str, label: str) -> list[dict]:
    """Up to AVG_SAMPLE recent completed --label jobs (for avg duration + last activity).
    Only a SAMPLE of history is wanted, so total_count > page size is expected here."""
    path = f"repos/{repo}/actions/runs?status=completed&per_page={COMPLETED_RUNS_SCAN}"
    out: list[dict] = []
    for run in _list_field(gh_api(path), "workflow_runs", path, complete=False):
        rid = run.get("id")
        if not isinstance(rid, int):
            raise WatchdogError(f"gh api {path}: run without integer id: {run!r:.200}")
        jpath = f"repos/{repo}/actions/runs/{rid}/jobs?per_page={PER_PAGE}"
        out.extend(j for j in _list_field(gh_api(jpath), "jobs", jpath, complete=False)
                   if label in (j.get("labels") or []))
        if len(_timed_jobs(out, label)) >= AVG_SAMPLE:
            break
    return out


def fetch_runners(repo: str) -> list[dict]:
    path = f"repos/{repo}/actions/runners?per_page={PER_PAGE}"
    return _list_field(gh_api(path), "runners", path)


# ---------------------------------------------------------------- pure classification

def _job_age_min(job: dict, now: datetime) -> int:
    ts = job.get("created_at") or job.get("started_at")
    if not ts:
        raise WatchdogError(f"job {job.get('id')} has neither created_at nor started_at")
    return int((now - _parse_ts(ts)).total_seconds() // 60)


def queued_label_jobs(jobs: list[dict], label: str) -> list[dict]:
    return [j for j in jobs if j.get("status") == "queued" and label in (j.get("labels") or [])]


def _runner_labels(r: dict) -> list[str]:
    return [(l.get("name") if isinstance(l, dict) else l) for l in (r.get("labels") or [])]


def _timed_jobs(completed: list[dict], label: str) -> list[dict]:
    """Completed success/failure --label jobs with usable timings, newest first."""
    ok = [j for j in completed
          if label in (j.get("labels") or []) and j.get("conclusion") in ("success", "failure")
          and j.get("started_at") and j.get("completed_at")
          and _parse_ts(j["completed_at"]) > _parse_ts(j["started_at"])]
    return sorted(ok, key=lambda j: j["completed_at"], reverse=True)


def avg_job_minutes(completed: list[dict], label: str) -> tuple[float, str]:
    sample = _timed_jobs(completed, label)[:AVG_SAMPLE]
    if not sample:
        return FALLBACK_AVG_MIN, "fallback"
    mins = [(_parse_ts(j["completed_at"]) - _parse_ts(j["started_at"])).total_seconds() / 60
            for j in sample]
    return sum(mins) / len(mins), f"last {len(mins)} jobs"


def _minutes_since_last_activity(active: list[dict], completed: list[dict],
                                 label: str, now: datetime) -> float:
    stamps = [_parse_ts(j["started_at"]) for j in active
              if j.get("status") == "in_progress" and label in (j.get("labels") or [])
              and j.get("started_at")]
    stamps += [_parse_ts(j["completed_at"]) for j in completed
               if label in (j.get("labels") or []) and j.get("completed_at")]
    if not stamps:
        return math.inf
    return (now - max(stamps)).total_seconds() / 60


def evaluate(repo: str, label: str, active: list[dict], runners: list[dict],
             completed: list[dict], threshold_min: int, grace_min: int,
             now: datetime) -> list[dict]:
    """→ [] or ONE collapsed finding for (repo, label)."""
    queued = queued_label_jobs(active, label)
    if not queued:
        return []  # offline with nothing waiting = expected (desktop off overnight); log only
    jobs = sorted(
        ({"id": j.get("id"), "name": j.get("name") or "?", "age_min": _job_age_min(j, now),
          "url": j.get("html_url") or ""} for j in queued),
        key=lambda x: x["age_min"], reverse=True,
    )
    oldest = jobs[0]["age_min"]
    mine = [r for r in runners if label in _runner_labels(r)]
    online = [r for r in mine if r.get("status") == "online"]
    base = {
        "key": f"ci_queue:{repo}:{label}", "repo": repo, "label": label,
        "depth": len(jobs), "oldest_age_min": oldest, "jobs": jobs,
        "runners": [f"{r.get('name')}:{r.get('status')}{'/busy' if r.get('busy') else ''}"
                    for r in mine],
        "threshold_min": threshold_min, "grace_min": grace_min,
        "total_runners": len(mine), "online_runners": len(online),
        "offline_runner_names": [r.get("name") for r in mine if r.get("status") != "online"],
    }

    if not mine:
        return [dict(base, kind="no_runner")] if oldest > grace_min else []
    if not online:
        return [dict(base, kind="runner_offline")] if oldest > grace_min else []
    if any(not r.get("busy") for r in online):
        quiet = _minutes_since_last_activity(active, completed, label, now)
        if oldest > grace_min and quiet > grace_min:
            return [dict(base, kind="stuck_pickup",
                         quiet_min=None if math.isinf(quiet) else int(quiet))]
        return []
    avg, src = avg_job_minutes(completed, label)
    eta = math.ceil(len(jobs) / len(online)) * avg
    if oldest > eta + threshold_min:
        return [dict(base, kind="not_draining", eta_min=round(eta), avg_min=round(avg, 1),
                     avg_source=src)]
    return []


# ---------------------------------------------------------------- dedup state

def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise WatchdogError(f"dedup state {path} unreadable/corrupt: {e} — fix or move it aside") from e
    if not isinstance(data, dict):
        raise WatchdogError(f"dedup state {path} is not a JSON object")
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _state_ts(v) -> datetime:
    ts = v.get("ts") if isinstance(v, dict) else v  # v1 stored a bare timestamp string
    if not isinstance(ts, str):
        raise WatchdogError(f"dedup state entry has no timestamp: {v!r}")
    return _parse_ts(ts)


def plan_pages(findings: list[dict], state: dict, now: datetime, cooldown_min: int) -> tuple[list[dict], dict]:
    """→ (findings to page now, new state with resolved keys cleared)."""
    live = {f["key"] for f in findings}
    new_state = {k: v for k, v in state.items() if k in live}
    to_page = []
    for f in findings:
        last = new_state.get(f["key"])
        if last and now - _state_ts(last) < timedelta(minutes=cooldown_min):
            continue
        to_page.append(f)
    return to_page, new_state


def mark_paged(state: dict, finding: dict, now: datetime) -> dict:
    s = dict(state)
    s[finding["key"]] = {"ts": _fmt_ts(now), "kind": finding["kind"]}
    return s


# ---------------------------------------------------------------- page text

def _job_lines(f: dict) -> str:
    return "\n".join(f"  - job {j['id']} '{j['name']}' queued {j['age_min']} min  {j['url']}"
                     for j in f["jobs"])


def render_page(f: dict) -> tuple[str, str]:
    repo_short = f["repo"].split("/")[-1]
    lbl, n, oldest = f["label"], f["depth"], f["oldest_age_min"]
    runners = ", ".join(f["runners"]) or "none"
    tail = (f"\n\nQueued '{lbl}' jobs (oldest first):\n{_job_lines(f)}\n\n"
            f"Runners[{lbl}]: {runners}. This page repeats at most once per cooldown "
            "window per label while the problem lasts.")
    if f["kind"] == "not_draining":
        offline = f.get("offline_runner_names") or []
        degraded = (f"; degraded capacity, {f['online_runners']}/{f['total_runners']} runners online"
                    if offline else "")
        subj = (f"CI slow: {repo_short} '{lbl}' runner busy, queue depth {n}, "
                f"ETA ~{f['eta_min']} min — not draining (oldest queued {oldest} min{degraded})")
        degraded_line = (
            f"DEGRADED CAPACITY: {', '.join(offline)} offline (desktop may be off/asleep); only "
            f"{f['online_runners']} of {f['total_runners']} '{lbl}' runner(s) are taking jobs. "
            "Bringing it back online is the fastest fix.\n\n" if offline else "")
        body = (
            f"TL;DR: the '{lbl}' runner(s) are up and working, but the queue is not draining "
            "as fast as expected.\n\n"
            f"WHAT: {n} job(s) queued; the oldest has waited {oldest} min. At ~{f['avg_min']} "
            f"min per job ({f['avg_source']}) across {f['online_runners']} online runner(s) "
            f"the queue should clear in ~{f['eta_min']} min; we only page once the wait "
            f"exceeds that ETA + {f['threshold_min']} min.\n\n"
            + degraded_line +
            "WHY: jobs may be running much slower than usual, a job may be hung, or more "
            "work is arriving than one serial runner can handle.\n\n"
            "WHAT TO DO: look at the running job for a hang (cancel it if stuck); if it's "
            "just volume, consider cancelling superseded runs or adding runner capacity."
        )
    elif f["kind"] == "runner_offline":
        subj = (f"CI stuck: {repo_short} '{lbl}' runner offline (desktop may be off/asleep) "
                f"— {n} job(s) queued, oldest {oldest} min")
        body = (
            f"TL;DR: the self-hosted '{lbl}' CI runner is offline while {n} job(s) wait "
            "for it.\n\n"
            "WHY: Musa's Windows desktop may be off or asleep, or the WSL runner service "
            "stopped. GitHub does not fail these jobs; it waits. CI is waiting, so PRs are "
            "not being tested.\n\n"
            "WHAT TO DO: wake/turn on the desktop and confirm the runner shows Idle/Active in "
            f"https://github.com/{f['repo']}/settings/actions/runners . Offline with nothing "
            "queued is expected overnight and is NOT paged."
        )
    elif f["kind"] == "stuck_pickup":
        quiet = f.get("quiet_min")
        quiet_s = f"for {quiet} min" if quiet is not None else "recently"
        subj = (f"CI stuck: {repo_short} '{lbl}' runner online but not picking up jobs "
                f"— {n} queued, oldest {oldest} min")
        body = (
            f"TL;DR: the '{lbl}' runner reports online and idle, yet {n} job(s) sit queued.\n\n"
            f"WHAT: no '{lbl}' job has started or finished {quiet_s}; the oldest queued job "
            f"has waited {oldest} min.\n\n"
            "WHY: the runner process may be wedged (connected but not polling for jobs), or "
            "the queued jobs need labels this runner doesn't have.\n\n"
            "WHAT TO DO: restart the runner service in WSL on the desktop; check the job's "
            "runs-on labels match the runner's."
        )
    elif f["kind"] == "no_runner":
        subj = (f"CI: no runner registered for label '{lbl}' — {n} {repo_short} job(s) "
                f"queued, oldest {oldest} min")
        body = (
            f"TL;DR: {n} CI job(s) in {f['repo']} need a '{lbl}' runner, but no runner "
            "with that label is registered at all.\n\n"
            "WHY: the runner may have been removed/re-registered under different labels. "
            "These jobs will wait forever.\n\n"
            "WHAT TO DO: re-register the self-hosted runner with the label, or change the "
            "workflow's runs-on."
        )
    else:  # pragma: no cover - defensive
        raise WatchdogError(f"unknown finding kind {f['kind']!r}")
    return subj, body + tail


def _send_page(f: dict) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.bus_send import send  # lazy: psycopg2/DB only on the live paging path

    subj, body = render_page(f)
    row_id, _thread = send(
        from_agent=FROM_AGENT, to=TO_AGENT, mtype="update", subject=subj, body=body,
        priority="P1", req=True,
    )
    return row_id


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", action="append", default=None,
                   help=f"owner/name; repeatable (default {DEFAULT_REPO})")
    p.add_argument("--label", default=DEFAULT_LABEL, help=f"runner label to watch (default {DEFAULT_LABEL})")
    p.add_argument("--threshold-min", type=int, default=DEFAULT_THRESHOLD_MIN,
                   help="busy runner: page when oldest queued age > ETA + this (default 30)")
    p.add_argument("--grace-min", type=int, default=DEFAULT_GRACE_MIN,
                   help="offline/idle/no-runner: minimum oldest-queued age before paging (default 5)")
    p.add_argument("--cooldown-min", type=int, default=DEFAULT_COOLDOWN_MIN)
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    p.add_argument("--dry-run", action="store_true", help="print what would page; send nothing, write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repos = args.repo or [DEFAULT_REPO]
    now = _now()
    stamp = _fmt_ts(now)
    try:
        findings: list[dict] = []
        for repo in repos:
            active = fetch_label_jobs(repo)
            runners = fetch_runners(repo)
            queued = queued_label_jobs(active, args.label)
            completed = fetch_recent_completed_jobs(repo, args.label) if queued else []
            mine = [r for r in runners if args.label in _runner_labels(r)]
            print(f"[{stamp}] {repo}: {len(active)} active job(s), {len(queued)} queued "
                  f"on '{args.label}'; runners[{args.label}]="
                  + (",".join(f"{r.get('name')}:{r.get('status')}"
                              f"{'/busy' if r.get('busy') else '/idle'}" for r in mine) or "NONE"))
            for j in queued:
                print(f"  queued: job {j.get('id')} '{j.get('name')}' age={_job_age_min(j, now)}m")
            if queued:
                avg, src = avg_job_minutes(completed, args.label)
                cap = sum(1 for r in mine if r.get("status") == "online")
                eta = math.ceil(len(queued) / cap) * avg if cap else math.inf
                print(f"  capacity {cap}/{len(mine)} online; avg job {avg:.1f} min ({src}); "
                      f"ETA ~{eta:.0f} min; page if oldest > ETA + {args.threshold_min} (busy case)")
            if not queued and any(r.get("status") != "online" for r in mine):
                print("  runner offline but nothing queued — expected (log only, no page)")
            f = evaluate(repo, args.label, active, runners, completed,
                         args.threshold_min, args.grace_min, now)
            if queued and not f:
                print("  queue is draining / within grace — no page")
            findings += f

        state = load_state(args.state_file)
        to_page, new_state = plan_pages(findings, state, now, args.cooldown_min)
        suppressed = len(findings) - len(to_page)
        print(f"[{stamp}] findings={len(findings)} to_page={len(to_page)} suppressed(cooldown)={suppressed}")

        if args.dry_run:
            for f in to_page:
                subj, _ = render_page(f)
                print(f"  WOULD PAGE [{f['kind']}] {f['key']} -> {TO_AGENT} P1 rr: {subj}")
            print("  (dry-run: nothing sent, state not written)")
            return 0

        # Persist resolved-key clears first, then each successful page individually,
        # so a mid-loop send failure keeps already-sent pages deduped.
        if new_state != state:
            save_state(args.state_file, new_state)
        for f in to_page:
            row_id = _send_page(f)
            new_state = mark_paged(new_state, f, now)
            save_state(args.state_file, new_state)
            print(f"  PAGED [{f['kind']}] {f['key']} -> {TO_AGENT} bus #{row_id}")
        return 0
    except WatchdogError as e:
        print(f"ci_queue_watchdog: FAIL LOUD — {e}", file=sys.stderr)
        return 2
    except Exception as e:  # paging/state failure: still loud, still non-zero
        print(f"ci_queue_watchdog: FAIL LOUD — {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
