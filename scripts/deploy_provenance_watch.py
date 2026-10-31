#!/usr/bin/env python3
"""
deploy_provenance_watch.py — cc-fleet-health deploy-integrity monitor (op#34832).

Watches the LIVE production target of each ihsanos silo on Vercel and alerts when
prod provenance is not machine-verifiable:

  ALERT NO_GIT_PROVENANCE  — live-prod deploy carries no githubCommitSha (a manual
                             `vercel --prod` CLI deploy) → "prod rests on someone's
                             word, not metadata."
  ALERT PROD_SHA_DRIFT     — live-prod githubCommitSha != the linked repo's prod-branch
                             HEAD → prod is behind/ahead of main.
  INFO  CI_ONLY_DRIFT      — prod sha != HEAD, but EVERY file changed between them is
                             under NON_DEPLOY_PATH_PREFIXES (e.g. `.github/` workflows)
                             → nothing deployable changed; quiet, not an alert. If the
                             changed-file list cannot be read, PROD_SHA_DRIFT fires as
                             before (fail SAFE) and says the list was unreadable.
  WARN  PROD_NOT_READY     — live-prod deploy readyState != READY.

DEAD-MAN'S SWITCH: any Vercel/GitHub call that fails makes this exit non-zero and
print LOUD to stderr. A monitor that fails silent is worse than none — never swallow.

Detect-only. It NEVER promotes/deploys/rolls back — it only reports + (with --alert)
posts a deduped bus row from cc-fleet-health. The deploy action stays a human's.

Usage:
  python3 scripts/deploy_provenance_watch.py --json      # machine-readable state
  python3 scripts/deploy_provenance_watch.py             # human summary
  python3 scripts/deploy_provenance_watch.py --alert     # + post deduped bus alert
  python3 scripts/deploy_provenance_watch.py --selftest  # prove the eval logic (no network)

Env: VERCEL_TOKEN (required), DATABASE_URL (only for --alert).
Note: uses `gh api` for the GitHub HEAD; if armed under launchd, verify gh-auth works
in that context (see [[ci-orphaned-jobs-studio-runner-flap]] launchd-gh-auth caveat) or
swap to a GH token.
"""
import os, sys, json, subprocess, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import bus_send  # noqa: E402

VERCEL_API = "https://api.vercel.com"
TEAM_ID = "team_mYxOkemmlg8a3HnKFAE9di7N"  # wingmen-aa9356e1 (NOT the default VERCEL_TEAM_ID)
SILOS = ["ihsanos", "ihsanos-irsyad"]      # both link github sheikh-musa/ihsanos @ main
STATE_DIR = os.path.expanduser("~/.wingmen/state")
STATE_FILE = os.path.join(STATE_DIR, "deploy_provenance_watch_state.json")
ALERT_TO = "cc-fleet-health"               # I triage, then escalate genuine forks to console
SETTLE_RUNS = 2                            # only alert once a silo's NO-GIT/drift state PERSISTS
                                           # across N runs (~30min at 30-min cadence) — the
                                           # signal is naturally transient during a normal
                                           # cli-deploy -> promote-to-git window, so an
                                           # instantaneous alert would cry wolf on routine deploys.
# Repo paths whose changes never need a production deploy. When EVERY file changed
# between the live-prod sha and the prod-branch HEAD starts with one of these prefixes,
# the sha mismatch is reported as INFO CI_ONLY_DRIFT instead of ALERT PROD_SHA_DRIFT.
# A documented constant (the Vercel project config exposes no machine-readable
# ignored-paths list to derive it from). Applies to every silo; no repo names here.
NON_DEPLOY_PATH_PREFIXES = (".github/",)
COMPARE_FILE_CAP = 300                     # GitHub compare API truncates .files at 300 —
                                           # a list that long is treated as unreadable (fail safe).


def _die(msg):
    sys.stderr.write("\033[31m[deploy_provenance_watch] FATAL: %s\033[0m\n" % msg)
    sys.exit(2)


def _vercel(path):
    tok = os.environ.get("VERCEL_TOKEN")
    if not tok:
        _die("VERCEL_TOKEN not in env")
    sep = "&" if "?" in path else "?"
    url = VERCEL_API + path + sep + "teamId=" + TEAM_ID
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:
        _die("Vercel API %s -> %s" % (path, e))


def _github_head(org, repo, branch):
    """Prod-branch HEAD sha via gh. Fail LOUD (dead-man) — a missing HEAD must not
    silently pass the drift check."""
    try:
        out = subprocess.run(
            ["gh", "api", "repos/%s/%s/commits/%s" % (org, repo, branch), "--jq", ".sha"],
            capture_output=True, text=True, timeout=30)
    except Exception as e:
        _die("gh api for %s/%s@%s raised %s" % (org, repo, branch, e))
    if out.returncode != 0 or not out.stdout.strip():
        _die("gh api for %s/%s@%s failed rc=%s err=%s" % (org, repo, branch, out.returncode, out.stderr.strip()[:200]))
    return out.stdout.strip()


def _github_changed_files(org, repo, base, head):
    """Files changed from the live-prod sha (base) to HEAD, via the GitHub compare API.

    Returns a list of paths, or None when the list cannot be determined (gh failure,
    HEAD not strictly ahead of base, or a truncated list). None is NOT fatal: the
    caller keeps today's PROD_SHA_DRIFT alert (fail SAFE) — missing data must never
    suppress an alert."""
    try:
        out = subprocess.run(
            ["gh", "api", "repos/%s/%s/compare/%s...%s" % (org, repo, base, head),
             "--jq", "{status: .status, files: [.files[].filename]}"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
    except Exception:
        return None
    files = data.get("files")
    if data.get("status") != "ahead" or not isinstance(files, list) or not files \
            or len(files) >= COMPARE_FILE_CAP:
        return None
    return files


def is_ci_only(changed_files):
    """True only when the changed-file list is known, non-empty, and EVERY path is
    under NON_DEPLOY_PATH_PREFIXES. Unknown (None) or empty -> False (fail safe)."""
    if not changed_files:
        return False
    return all(any(f.startswith(p) for p in NON_DEPLOY_PATH_PREFIXES) for f in changed_files)


def evaluate(silo, live_sha, ready_state, expected_head, changed_files=None, info=None):
    """Pure detection logic (unit-testable). Returns a list of alert dicts.

    changed_files: paths changed live_sha..HEAD, or None if unknown/unreadable.
    info: optional list; a non-alert CI_ONLY_DRIFT note is appended to it."""
    alerts = []
    if not live_sha:
        alerts.append({"silo": silo, "sev": "ALERT", "code": "NO_GIT_PROVENANCE",
                       "detail": "live-prod deploy carries no githubCommitSha (manual CLI deploy) — provenance not machine-verifiable"})
    elif not (live_sha.startswith(expected_head) or expected_head.startswith(live_sha)):
        if is_ci_only(changed_files):
            if info is not None:
                info.append({"silo": silo, "sev": "INFO", "code": "CI_ONLY_DRIFT",
                             "detail": "live-prod sha %s != prod-branch HEAD %s, but only non-deploy paths changed (%s)"
                                       % (live_sha[:8], expected_head[:8], ", ".join(changed_files))})
        else:
            detail = "live-prod sha %s != prod-branch HEAD %s" % (live_sha[:8], expected_head[:8])
            if changed_files is None:
                detail += " (changed-file list could not be read — alerting fail-safe)"
            alerts.append({"silo": silo, "sev": "ALERT", "code": "PROD_SHA_DRIFT", "detail": detail})
    if ready_state and ready_state != "READY":
        alerts.append({"silo": silo, "sev": "WARN", "code": "PROD_NOT_READY",
                       "detail": "live-prod deploy readyState=%s" % ready_state})
    return alerts


def _silo_sig(r):
    """Signature of a silo's alert state — sorted codes + the offending sha."""
    return "|".join(sorted(a["code"] for a in r["alerts"])) + "@" + (r["live_sha"] or "none")


def decide_alerts(results, prev_state):
    """Pure settle-count + dedup decision (unit-testable, no I/O).

    Returns (to_post, new_state). A silo is POSTED only once its SAME alert signature
    has persisted across SETTLE_RUNS consecutive runs (settle) AND it has not already
    been posted for that signature (dedup). A silo that goes clean drops from state so a
    future regression settles+pages afresh.
    """
    new_state, to_post = {}, []
    for r in results:
        if not r["alerts"]:
            continue  # clean → not carried in state
        sig = _silo_sig(r)
        prev = prev_state.get(r["silo"]) or {}
        if prev.get("sig") == sig:
            count = int(prev.get("count", 1)) + 1
            alerted = bool(prev.get("alerted", False))
        else:
            count, alerted = 1, False
        if count >= SETTLE_RUNS and not alerted:
            to_post.append(r)
            alerted = True
        new_state[r["silo"]] = {"sig": sig, "count": count, "alerted": alerted}
    return to_post, new_state


def collect():
    results = []
    for app in SILOS:
        proj = _vercel("/v9/projects/%s" % app)
        link = proj.get("link") or {}
        org = link.get("org") or link.get("owner")
        repo = link.get("repo")
        branch = link.get("productionBranch") or proj.get("productionBranch") or "main"
        prod = (proj.get("targets") or {}).get("production") or {}
        meta = prod.get("meta") or {}
        live_sha = meta.get("githubCommitSha") or ""
        ready = prod.get("readyState") or prod.get("state") or ""
        creator = (prod.get("creator") or {}).get("username")
        dep_id = prod.get("id") or prod.get("uid")
        url = prod.get("url")
        if not (org and repo):
            _die("project %s has no linked git repo (link=%s)" % (app, link))
        expected = _github_head(org, repo, branch)
        changed = None
        if live_sha and not (live_sha.startswith(expected) or expected.startswith(live_sha)):
            changed = _github_changed_files(org, repo, live_sha, expected)
        info = []
        alerts = evaluate(app, live_sha, ready, expected, changed, info)
        results.append({
            "silo": app, "repo": "%s/%s" % (org, repo), "branch": branch,
            "live_sha": live_sha or None, "expected_head": expected,
            "verifiable": bool(live_sha) and (live_sha.startswith(expected) or expected.startswith(live_sha)),
            "ready_state": ready, "creator": creator, "deploy_id": dep_id, "url": url,
            "alerts": alerts, "info": info,
        })
    return results


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=2)


def _post_bus(subject, body):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _die("--alert needs DATABASE_URL")
    mid, _thread = bus_send.send(from_agent="cc-fleet-health", to=ALERT_TO, mtype="update",
                                  subject=subject, body=body, priority="P2", req=False, dsn=dsn)
    return mid


def _selftest():
    """Prove the eval logic without network (wet-prove the shipped fn)."""
    cases = [
        ("no-git",   evaluate("s", "", "READY", "abc123"),       "NO_GIT_PROVENANCE"),
        ("drift",    evaluate("s", "deadbeef", "READY", "abc123"), "PROD_SHA_DRIFT"),
        ("clean",    evaluate("s", "abc123def", "READY", "abc123def"), None),
        ("prefix-ok",evaluate("s", "abc123", "READY", "abc123def456"), None),
        ("not-ready",evaluate("s", "abc123", "BUILDING", "abc123"), "PROD_NOT_READY"),
        ("ci-only",  evaluate("s", "deadbeef", "READY", "abc123", [".github/workflows/x.yml"]), None),
        ("ci+app",   evaluate("s", "deadbeef", "READY", "abc123", [".github/workflows/x.yml", "app/page.tsx"]), "PROD_SHA_DRIFT"),
    ]
    ok = True
    for name, alerts, expect in cases:
        codes = [a["code"] for a in alerts]
        got = (expect in codes) if expect else (len(alerts) == 0)
        print("  [%s] %-9s -> %s  %s" % ("PASS" if got else "FAIL", name, codes or "clean", "" if got else "(expected %s)" % expect))
        ok = ok and got

    # settle-count + dedup (decide_alerts) — a synthetic silo carrying a NO-GIT alert
    def _res(has_alert, sha=""):
        return [{"silo": "s", "url": "u", "live_sha": sha, "expected_head": "abc123",
                 "ready_state": "READY", "creator": "x",
                 "alerts": ([{"silo": "s", "sev": "ALERT", "code": "NO_GIT_PROVENANCE", "detail": "d"}] if has_alert else [])}]
    st = {}
    steps = []
    tp, st = decide_alerts(_res(True), st); steps.append(("run1-firing", len(tp) == 0))       # not settled yet
    tp, st = decide_alerts(_res(True), st); steps.append(("run2-settled-post", len(tp) == 1))  # settles -> post
    tp, st = decide_alerts(_res(True), st); steps.append(("run3-dedup", len(tp) == 0))          # already paged
    tp, st = decide_alerts(_res(False), st); steps.append(("run4-clean-drops", "s" not in st))  # clean drops state
    # a pure transient (fires once, clean next) must NEVER post
    st2 = {}
    tp, st2 = decide_alerts(_res(True), st2)
    tp, st2 = decide_alerts(_res(False), st2)
    steps.append(("transient-never-posts", len(tp) == 0 and "s" not in st2))
    for name, good in steps:
        print("  [%s] %s" % ("PASS" if good else "FAIL", name))
        ok = ok and good

    print("SELFTEST", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def main():
    args = set(sys.argv[1:])
    if "--selftest" in args:
        _selftest()
    results = collect()
    firing = [a for r in results for a in r["alerts"]]

    if "--json" in args:
        print(json.dumps({"silos": results, "alerts": firing}, indent=2))
    else:
        for r in results:
            tag = "OK " if not r["alerts"] else "!! "
            print("%s%-15s prod=%s (%s) sha=%s head=%s verifiable=%s ready=%s by=%s" % (
                tag, r["silo"], r["deploy_id"], r["url"],
                (r["live_sha"] or "NO-GIT")[:8], r["expected_head"][:8], r["verifiable"], r["ready_state"], r["creator"]))
            for a in r["alerts"] + r.get("info", []):
                print("     %s %s: %s" % (a["sev"], a["code"], a["detail"]))
        print("\n%d alert(s) firing." % len(firing))

    if "--alert" in args and "--dry-run" not in args:
        to_post, new_state = decide_alerts(results, _load_state())
        if to_post:
            lines = ["TL;DR: deploy-provenance watch fired — a silo's live prod has been NOT machine-verifiable "
                     "across %d+ runs (not a transient deploy window). Detect-only; no deploy action taken.\n" % SETTLE_RUNS]
            for r in to_post:
                lines.append("%s: prod=%s sha=%s head=%s ready=%s by=%s" % (
                    r["silo"], r["url"], (r["live_sha"] or "NO-GIT")[:8], r["expected_head"][:8], r["ready_state"], r["creator"]))
                for a in r["alerts"]:
                    lines.append("  %s %s: %s" % (a["sev"], a["code"], a["detail"]))
            lines.append("\nFix: promote the git-integration (sha-bearing) deploy of the intended commit, or sha-stamp the cli deploy.")
            mid = _post_bus("deploy-provenance watch: %d silo(s) on unverifiable/drifted prod" % len(to_post), "\n".join(lines))
            print("posted bus alert id=%s (%d settled of %d firing)" % (mid, len(to_post), len(firing)))
        elif firing:
            print("%d alert(s) firing but not yet settled/already-paged — no re-page." % len(firing))
        _save_state(new_state)

    sys.exit(1 if firing else 0)


if __name__ == "__main__":
    main()
