#!/usr/bin/env python3
"""ci_queue_watchdog.py — page when a self-hosted CI job sits QUEUED too long.

Why (cc-fleet-health, orch-console bus #57952): wingmen-orchestrator's
"Lint & Test" job runs on the self-hosted runner `cubeasht-orchestrator`
(labels self-hosted, Linux, X64, cubeasht, win-wsl) on an operator's Windows
desktop (WSL). When that desktop is off or asleep, jobs do not FAIL — they
QUEUE silently, forever. Nobody watches GitHub Actions queues, so a PR can sit
un-tested for hours. This watchdog closes that gap.

What it checks, per --repo:
  1. Runs with status=queued AND status=in_progress (a run can be in progress
     while one of its jobs is still queued), then each run's jobs. A job whose
     status is "queued", whose `labels` include --label, and whose age exceeds
     --threshold-min is a `queued_job` finding.
  2. The repo's self-hosted runners. If a runner carrying --label is OFFLINE
     and at least one --label job is queued (any age), that's a
     `runner_offline` finding. If NO registered runner carries the label at all
     while a job is queued, that's a `no_runner` finding. Offline with nothing
     queued is only LOGGED — the desktop being off overnight is expected.

Age field: `created_at`, not `started_at`. created_at is when GitHub created
(enqueued) the job — exactly "how long has this been waiting". started_at is
only meaningful once a runner picks the job up; on a still-queued job GitHub
either leaves it null or fills it with a placeholder (commonly ≈ created_at, and
it can be rewritten when the job is re-queued), so it cannot be trusted to
measure queue wait. We fall back to started_at only if created_at is absent.

Paging: one deduped bus page per finding, P1 + requires_response, from
cc-fleet-health to orch-console, via scripts/bus_send.send(). Dedup state is a
JSON file {finding_key: last_paged_utc}; a key re-pages after --cooldown-min
(default 60) and is CLEARED when the finding resolves.

Fail LOUD: any gh error, non-JSON output, or unexpected shape raises
WatchdogError → exit 2 with a clear stderr message. A monitor that silently
reports "nothing queued" because its query failed is worse than no monitor.
A paging failure exits non-zero too (state for already-sent pages is kept so
the next run retries only what failed).

--dry-run: scan + print what WOULD be paged; never sends, never writes state.

Not lease-gated: this is detection + an operator-facing alert (charter §3:
detection and alerts stay ungated; only self-healing actions are gated).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_REPO = "sheikh-musa/wingmen-orchestrator"
DEFAULT_LABEL = "cubeasht"
DEFAULT_THRESHOLD_MIN = 30
DEFAULT_COOLDOWN_MIN = 60
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


def _list_field(data: dict, field: str, path: str) -> list:
    v = data.get(field)
    if not isinstance(v, list):
        raise WatchdogError(f"gh api {path}: response missing list field {field!r} (keys={sorted(data)})")
    total = data.get("total_count")
    if isinstance(total, int) and total > len(v):
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


def classify_jobs(repo: str, jobs: list[dict], label: str, threshold_min: int, now: datetime) -> list[dict]:
    out = []
    for j in queued_label_jobs(jobs, label):
        age = _job_age_min(j, now)
        if age > threshold_min:
            out.append({
                "kind": "queued_job",
                "key": f"queued_job:{repo}:{j['id']}",
                "repo": repo, "label": label,
                "job_id": j["id"], "job_name": j.get("name") or "?",
                "run_id": j.get("run_id"), "url": j.get("html_url") or "",
                "age_min": age, "threshold_min": threshold_min,
            })
    return out


def _runner_labels(r: dict) -> list[str]:
    return [(l.get("name") if isinstance(l, dict) else l) for l in (r.get("labels") or [])]


def classify_runners(repo: str, runners: list[dict], label: str, queued_count: int) -> list[dict]:
    if queued_count <= 0:
        return []  # offline with nothing waiting = expected (desktop off overnight); log only
    mine = [r for r in runners if label in _runner_labels(r)]
    if not mine:
        return [{
            "kind": "no_runner", "key": f"no_runner:{repo}:{label}",
            "repo": repo, "label": label, "queued_count": queued_count,
        }]
    return [{
        "kind": "runner_offline", "key": f"runner_offline:{repo}:{r.get('name')}",
        "repo": repo, "label": label, "runner": r.get("name"), "queued_count": queued_count,
    } for r in mine if r.get("status") != "online"]


def evaluate(repo: str, jobs: list[dict], runners: list[dict], label: str,
             threshold_min: int, now: datetime) -> list[dict]:
    queued = queued_label_jobs(jobs, label)
    return (classify_jobs(repo, jobs, label, threshold_min, now)
            + classify_runners(repo, runners, label, len(queued)))


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


def plan_pages(findings: list[dict], state: dict, now: datetime, cooldown_min: int) -> tuple[list[dict], dict]:
    """→ (findings to page now, new state with resolved keys cleared)."""
    live = {f["key"] for f in findings}
    new_state = {k: v for k, v in state.items() if k in live}
    to_page = []
    for f in findings:
        last = new_state.get(f["key"])
        if last and now - _parse_ts(last) < timedelta(minutes=cooldown_min):
            continue
        to_page.append(f)
    return to_page, new_state


def mark_paged(state: dict, key: str, now: datetime) -> dict:
    s = dict(state)
    s[key] = _fmt_ts(now)
    return s


# ---------------------------------------------------------------- page text

def render_page(f: dict) -> tuple[str, str]:
    repo_short = f["repo"].split("/")[-1]
    if f["kind"] == "queued_job":
        subj = (f"CI stuck: {repo_short} '{f['job_name']}' queued {f['age_min']} min "
                f"waiting for a '{f['label']}' runner")
        body = (
            f"TL;DR: a CI job has been waiting {f['age_min']} minutes for the self-hosted "
            f"'{f['label']}' runner and nothing has picked it up.\n\n"
            f"WHAT: {f['repo']} job '{f['job_name']}' (job {f['job_id']}, run {f['run_id']}) "
            f"has been QUEUED for {f['age_min']} min (threshold {f['threshold_min']} min).\n"
            f"{f['url']}\n\n"
            "WHY: this job only runs on Musa's Windows desktop (WSL runner "
            "cubeasht-orchestrator). If that desktop is off or asleep, GitHub does not "
            "fail the job, it just waits. CI is waiting, so PRs are not being tested.\n\n"
            "WHAT TO DO: wake/turn on the desktop (and check the WSL runner service is "
            "running). If the desktop will be off for a while, cancel/re-route the job. "
            "This page repeats at most hourly while the job stays queued."
        )
    elif f["kind"] == "runner_offline":
        subj = (f"CI runner {f['runner']} OFFLINE with {f['queued_count']} {repo_short} "
                f"job(s) queued")
        body = (
            f"TL;DR: the self-hosted CI runner {f['runner']} is offline while "
            f"{f['queued_count']} job(s) are waiting for it.\n\n"
            f"WHAT: GitHub reports runner {f['runner']} (label '{f['label']}') as offline, "
            f"and {f['queued_count']} '{f['label']}' job(s) in {f['repo']} are queued.\n\n"
            "WHY: Musa's desktop may be off or asleep, or the WSL runner service stopped. "
            "CI is waiting; nothing will run until the runner comes back.\n\n"
            "WHAT TO DO: wake/turn on the desktop and confirm the runner shows Idle in "
            f"https://github.com/{f['repo']}/settings/actions/runners . Offline with "
            "nothing queued is expected overnight and is NOT paged."
        )
    elif f["kind"] == "no_runner":
        subj = (f"CI: no runner registered for label '{f['label']}' — "
                f"{f['queued_count']} {repo_short} job(s) queued")
        body = (
            f"TL;DR: {f['queued_count']} CI job(s) in {f['repo']} need a '{f['label']}' "
            "runner, but no runner with that label is registered at all.\n\n"
            "WHY: the runner may have been removed/re-registered under different labels. "
            "These jobs will wait forever.\n\n"
            "WHAT TO DO: re-register the self-hosted runner with the label, or change the "
            "workflow's runs-on."
        )
    else:  # pragma: no cover - defensive
        raise WatchdogError(f"unknown finding kind {f['kind']!r}")
    return subj, body


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
    p.add_argument("--threshold-min", type=int, default=DEFAULT_THRESHOLD_MIN)
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
            jobs = fetch_label_jobs(repo)
            runners = fetch_runners(repo)
            queued = queued_label_jobs(jobs, args.label)
            mine = [r for r in runners if args.label in _runner_labels(r)]
            print(f"[{stamp}] {repo}: {len(jobs)} active job(s), {len(queued)} queued "
                  f"on '{args.label}'; runners[{args.label}]="
                  + (",".join(f"{r.get('name')}:{r.get('status')}" for r in mine) or "NONE"))
            for j in queued:
                print(f"  queued: job {j.get('id')} '{j.get('name')}' age={_job_age_min(j, now)}m")
            if not queued and any(r.get("status") != "online" for r in mine):
                print("  runner offline but nothing queued — expected (log only, no page)")
            findings += evaluate(repo, jobs, runners, args.label, args.threshold_min, now)

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
            new_state = mark_paged(new_state, f["key"], now)
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
