#!/usr/bin/env python3
"""ci_orphan_watchdog.py — DETECT-ONLY GH Actions orphaned/stuck-job monitor (STAGED).

Detects the CI orphan pattern cc-irsyad-6 flagged (#32321/#32331) + I reconciled to the Studio-runner
flap (studio-runner-ihsanos id21): a job sits status=queued with an OLD started_at (age-in-queue >
THRESHOLD_MIN) because a runner claimed it (stamped started_at) then flapped offline before its status
advanced. Also reports the runner pool state for correlation.

DETECT-ONLY. Default prints a report. `--alert` posts ONE deduped bus message to cc-fleet-health.
It NEVER clears anything — cancel+rerun is a SEPARATE, gated executor (deferred behind an accurate
orphan-vs-healthy gate; never auto-cancel a live job). Fail-LOUD: any gh/api error aborts (a monitor
that fails silently manufactures false confidence).

RUNBOOK to clear a confirmed orphan (human/SRE, cc-irsyad-6 #32331, verified on #466/#464/#463/#467):
  gh run cancel <run-id>            # cancels the WHOLE run (job-level cancel not used)
  gh run rerun <run-id> --failed    # reruns only failed/cancelled jobs; MUST cancel first (else
                                     # "run <id> cannot be rerun; This workflow is already running")
Before clearing a LIVE one, pull `gh api repos/{repo}/actions/jobs/{job_id}` for runner_name (forensic
trail; cancel+rerun destroys it — note a queued-orphan may show a BLANK runner).
"""
import os, sys, json, subprocess, argparse
from datetime import datetime, timezone

REPO = os.environ.get("CI_ORPHAN_REPO", "sheikh-musa/ihsanos")
THRESHOLD_MIN = int(os.environ.get("CI_ORPHAN_THRESHOLD_MIN", "30"))
REALERT_MIN = 60  # don't re-page the same run within this window
STATE_FILE = os.path.expanduser("~/wingmen/orchestrator/state/ci_orphan_watchdog.json")


def gh_api(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL-LOUD: `gh api {path}` exit {r.returncode}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def _now():
    return datetime.now(timezone.utc)


def _age_min(ts):
    return (_now() - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 60.0


def scan():
    runners = gh_api(f"repos/{REPO}/actions/runners")["runners"]
    pool = {r["name"]: {"status": r["status"], "busy": r["busy"],
                        "labels": [l["name"] for l in r["labels"]]} for r in runners}
    queued = gh_api(f"repos/{REPO}/actions/runs?status=queued&per_page=30").get("workflow_runs", [])
    stuck = []
    for run in queued:
        jobs = gh_api(f"repos/{REPO}/actions/runs/{run['id']}/jobs").get("jobs", [])
        for j in jobs:
            if j["status"] == "queued" and j.get("started_at"):
                age = _age_min(j["started_at"])
                if age > THRESHOLD_MIN:
                    stuck.append({"run_id": run["id"], "job": j["name"], "age_min": round(age),
                                  "branch": run.get("head_branch"), "labels": j.get("labels")})
    return stuck, pool


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(s):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)


def _dedup(stuck):
    """Return the subset of stuck runs not paged within REALERT_MIN, and update state."""
    state = _load_state()
    now_iso = _now().isoformat()
    fresh = []
    for s in stuck:
        rid = str(s["run_id"])
        last = state.get(rid)
        if last is None or _age_min(last) > REALERT_MIN:
            fresh.append(s)
            state[rid] = now_iso
    # prune state entries older than 24h to avoid unbounded growth
    state = {k: v for k, v in state.items() if _age_min(v) < 24 * 60}
    _save_state(state)
    return fresh


def _post_bus(stuck, pool, free_studio):
    import psycopg2
    studio = {n: v for n, v in pool.items() if "studio" in v["labels"]}
    studio_line = "; ".join(f"{n}={v['status']}/{'busy' if v['busy'] else 'free'}" for n, v in studio.items())
    lines = [f"- run {s['run_id']} '{s['job']}' queued {s['age_min']}m (branch {s['branch']}, runs-on {s['labels']})"
             for s in stuck]
    body = (
        f"TL;DR: {len(stuck)} GH Actions job(s) STUCK queued > {THRESHOLD_MIN}m on {REPO} while a studio runner "
        f"is ONLINE+FREE ({free_studio}) — a free runner should have taken it => TRUE ORPHAN/scheduler-stall, "
        f"not mere starvation. DETECT-ONLY; I did NOT clear anything.\n\n"
        + "\n".join(lines)
        + f"\n\nStudio runner pool: {studio_line}\n"
        "RUNBOOK: gh run cancel <run-id> ; gh run rerun <run-id> --failed (cancel FIRST, else 'already running'). "
        "Before clearing, `gh api repos/" + REPO + "/actions/jobs/{job_id}` for runner_name (forensics). "
        "Root fix = Studio-runner stability (spec pending). Memory: ci-orphaned-jobs-studio-runner-flap."
    )
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,created_at) "
        "VALUES ('cc-fleet-health','cc-fleet-health','update',%s,%s,now())",
        (f"CI orphan watchdog: {len(stuck)} job(s) stuck queued >{THRESHOLD_MIN}m ({REPO}) — Studio-runner flap; detect-only", body),
    )
    conn.commit()
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="DETECT-ONLY GH Actions orphaned-job monitor (never clears).")
    ap.add_argument("--alert", action="store_true", help="post a deduped bus message when orphans found (staged; off by default)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    stuck, pool = scan()
    if args.json:
        print(json.dumps({"repo": REPO, "threshold_min": THRESHOLD_MIN, "stuck": stuck, "pool": pool}, indent=2))
    else:
        studio = {n: v for n, v in pool.items() if "studio" in v["labels"]}
        print(f"[ci-orphan-watchdog] {REPO} — {len(stuck)} stuck queued >{THRESHOLD_MIN}m")
        for s in stuck:
            print(f"  run {s['run_id']} '{s['job']}' queued {s['age_min']}m (br {s['branch']}, runs-on {s['labels']})")
        print("  studio pool: " + "; ".join(f"{n}={v['status']}/{'busy' if v['busy'] else 'free'}" for n, v in studio.items()))

    # ALERT GATE (orphan-vs-starvation): only page when a studio runner is ONLINE+FREE yet a job is still
    # stuck — a free runner that should have grabbed it => a TRUE orphan/scheduler-stall. If ALL studio
    # runners are offline/busy, stuck jobs are just capacity STARVATION (the known Studio-asleep state,
    # already flagged) — do NOT page on that, it would be pure noise. This makes --alert safe to arm even
    # while the Studio is down.
    free_studio = [n for n, v in pool.items()
                   if "studio" in v["labels"] and v["status"] == "online" and not v["busy"]]
    if args.alert and stuck:
        if not free_studio:
            print(f"[ci-orphan-watchdog] {len(stuck)} stuck but NO online+free studio runner "
                  f"-> STARVATION (Studio down), not a fresh orphan — SUPPRESSING alert (not noise)")
        else:
            fresh = _dedup(stuck)
            if fresh:
                _post_bus(fresh, pool, free_studio)
                print(f"[ci-orphan-watchdog] ALERTED on {len(fresh)} new stuck run(s) "
                      f"(free studio runner present: {free_studio} => true orphan/stall)")
            else:
                print("[ci-orphan-watchdog] stuck runs already paged within the re-alert window — no double-page")


if __name__ == "__main__":
    main()
