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


def evaluate(silo, live_sha, ready_state, expected_head):
    """Pure detection logic (unit-testable). Returns a list of alert dicts."""
    alerts = []
    if not live_sha:
        alerts.append({"silo": silo, "sev": "ALERT", "code": "NO_GIT_PROVENANCE",
                       "detail": "live-prod deploy carries no githubCommitSha (manual CLI deploy) — provenance not machine-verifiable"})
    elif not (live_sha.startswith(expected_head) or expected_head.startswith(live_sha)):
        alerts.append({"silo": silo, "sev": "ALERT", "code": "PROD_SHA_DRIFT",
                       "detail": "live-prod sha %s != prod-branch HEAD %s" % (live_sha[:8], expected_head[:8])})
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
        alerts = evaluate(app, live_sha, ready, expected)
        results.append({
            "silo": app, "repo": "%s/%s" % (org, repo), "branch": branch,
            "live_sha": live_sha or None, "expected_head": expected,
            "verifiable": bool(live_sha) and (live_sha.startswith(expected) or expected.startswith(live_sha)),
            "ready_state": ready, "creator": creator, "deploy_id": dep_id, "url": url,
            "alerts": alerts,
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
    import psycopg2
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _die("--alert needs DATABASE_URL")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,created_at) "
        "VALUES ('cc-fleet-health',%s,'update',%s,%s,'P2',now()) RETURNING id",
        (ALERT_TO, subject, body))
    mid = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return mid


def _selftest():
    """Prove the eval logic without network (wet-prove the shipped fn)."""
    cases = [
        ("no-git",   evaluate("s", "", "READY", "abc123"),       "NO_GIT_PROVENANCE"),
        ("drift",    evaluate("s", "deadbeef", "READY", "abc123"), "PROD_SHA_DRIFT"),
        ("clean",    evaluate("s", "abc123def", "READY", "abc123def"), None),
        ("prefix-ok",evaluate("s", "abc123", "READY", "abc123def456"), None),
        ("not-ready",evaluate("s", "abc123", "BUILDING", "abc123"), "PROD_NOT_READY"),
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
            for a in r["alerts"]:
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
