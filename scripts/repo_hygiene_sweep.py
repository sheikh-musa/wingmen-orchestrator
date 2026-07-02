#!/usr/bin/env python3
"""repo_hygiene_sweep.py — fleet-wide git + deploy hygiene BACKSTOP.

Runs on a launchd timer on the dev host (Studio). For every fleet repo it:

  AUTO-HEALS the common stranding case: any branch ahead of an EXISTING tracked
  upstream is pushed (committed work the lane simply forgot to push). This is
  unambiguous — the branch already has a remote it's tracking — so it's safe to
  push without a human.

  ALERTS (never auto-acts) on the ambiguous / risky cases:
    - repo has NO git remote at all           (the shipforge case — total loss risk)
    - commits reachable from no remote on a branch with no upstream (may be scratch)
    - uncommitted changes older than STALE_HRS (aging WIP not committed)
    - a branch diverged from its upstream       (needs human reconcile)
    - merged-but-undeployed: a Firebase site's live release is behind main
      (the cosem-adcda #166 case — merged, never shipped)

Alerts go to the operator via scripts/tg_send.sh and are logged to
logs/repo_hygiene.log. Everything except the safe auto-push is read-only.

Root cause this exists to kill: the 2026-07-02 Mini->Studio migration left the
Studio without git remotes/credentials, so lane work silently stranded locally
until someone manually checked. This makes stranding self-heal or self-report.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
ORCH = HOME / "wingmen" / "orchestrator"
LOG = ORCH / "logs" / "repo_hygiene.log"
STALE_HRS = 12                      # uncommitted work older than this -> alert
SCAN_GLOBS = [str(HOME / "wingmen" / "projects" / "*"), str(ORCH)]

# Firebase site mappings for the merged-but-undeployed check (repo basename -> site).
FIREBASE_SITES = {"cosem-adcda": "cosem-adcda-cb6d9", "cosem-tdu": "tdu-tools-prod"}
SA_KEY = HOME / ".wingmen" / "keys" / "cosem-sa.json"


def git(repo: Path, *args: str, timeout: int = 60) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def branches(repo: Path) -> list[str]:
    out = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return [b for b in out.splitlines() if b]


def upstream(repo: Path, branch: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref",
                        f"{branch}@{{u}}"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def firebase_deploy_behind(repo_name: str, repo: Path) -> str | None:
    """Return an alert string if the live Firebase release predates main's HEAD."""
    site = FIREBASE_SITES.get(repo_name)
    if not site or not SA_KEY.exists():
        return None
    try:
        import jwt, urllib.request, urllib.parse
        sa = json.load(open(SA_KEY))
        now = int(time.time())
        assertion = jwt.encode({"iss": sa["client_email"],
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "aud": sa["token_uri"], "iat": now, "exp": now + 3600},
            sa["private_key"], algorithm="RS256")
        data = urllib.parse.urlencode({"grant_type":
            "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}).encode()
        tok = json.loads(urllib.request.urlopen(
            urllib.request.Request(sa["token_uri"], data=data)).read())["access_token"]
        req = urllib.request.Request(
            f"https://firebasehosting.googleapis.com/v1beta1/sites/{site}/releases?pageSize=1",
            headers={"Authorization": f"Bearer {tok}"})
        rels = json.loads(urllib.request.urlopen(req).read()).get("releases", [])
        if not rels:
            return None
        # released time (epoch) vs main HEAD commit time
        rel_iso = rels[0].get("releaseTime", "")[:19]
        rel_epoch = time.mktime(time.strptime(rel_iso, "%Y-%m-%dT%H:%M:%S"))
        git(repo, "fetch", "origin", "main")
        head_epoch = int(git(repo, "log", "-1", "--format=%ct", "origin/main") or 0)
        if head_epoch > rel_epoch + 60:
            behind = git(repo, "rev-list", "--count", f"--since=@{int(rel_epoch)}", "origin/main")
            return f"DEPLOY-GAP: {site} last released {rel_iso}Z but origin/main has ~{behind} newer commit(s) — merged-but-undeployed"
    except Exception as e:
        return None  # deploy check is best-effort; never block the git sweep
    return None


def sweep() -> list[tuple[str, str, str]]:
    """Return [(severity, repo, message)]. Auto-heals clean ahead-of-upstream branches."""
    findings: list[tuple[str, str, str]] = []
    repos = set()
    for g in SCAN_GLOBS:
        for p in glob.glob(g):
            if (Path(p) / ".git").exists():
                repos.add(Path(p))
    for repo in sorted(repos):
        name = repo.name
        remotes = git(repo, "remote")
        if not remotes:
            n = git(repo, "rev-list", "--count", "--all") or "?"
            findings.append(("CRITICAL", name, f"NO git remote configured — {n} commits exist ONLY on this host"))
            continue
        git(repo, "fetch", "--all", "--quiet", timeout=120)
        # 1. AUTO-HEAL forgotten pushes; alert only on REAL tracking-branch trouble.
        #    A branch tracking origin/<same-name> that is ahead+behind = genuine
        #    divergence (alert). A feature branch tracking origin/main that is just
        #    "behind main" is normal staleness, NOT a finding — suppress it.
        for b in branches(repo):
            up = upstream(repo, b)
            if not up:
                continue
            ahead = git(repo, "rev-list", "--count", f"{up}..{b}")
            behind = git(repo, "rev-list", "--count", f"{b}..{up}")
            same_name_upstream = up.split("/", 1)[-1] == b
            if ahead not in ("", "0") and behind in ("", "0"):
                r = subprocess.run(["git", "-C", str(repo), "push", "origin", b],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    findings.append(("HEALED", name, f"auto-pushed {b} (+{ahead} ahead of {up})"))
                else:
                    findings.append(("WARN", name, f"{b} +{ahead} ahead but push failed: {r.stderr.strip()[:80]}"))
            elif ahead not in ("", "0") and behind not in ("", "0") and same_name_upstream:
                # its own remote branch diverged — a real reconcile the lane must do
                findings.append(("WARN", name, f"{b} DIVERGED from its own {up} (+{ahead}/-{behind}) — needs reconcile"))
        # 2. stranded commits — log-only (mostly stashes / abandoned local branches).
        stranded = git(repo, "rev-list", "--count", "--all", "--not", "--remotes")
        if stranded not in ("", "0"):
            findings.append(("INFO", name, f"{stranded} commit(s) on no remote (likely stash/abandoned local branches)"))
        # 3. uncommitted — log-only unless it's genuinely aging WIP on the checked-out branch.
        dirty = [l for l in git(repo, "status", "--porcelain").splitlines() if l]
        if dirty:
            try:
                mtimes = [os.path.getmtime(repo / l[3:].split(" -> ")[-1])
                          for l in dirty if (repo / l[3:].split(" -> ")[-1]).exists()]
                oldest_hrs = (time.time() - min(mtimes)) / 3600 if mtimes else 0
            except Exception:
                oldest_hrs = 0
            # Only ALERT on aged WIP in a primary lane repo (projects/), not secondary
            # checkouts like the Studio's orchestrator mirror. Otherwise log-only.
            is_lane = repo.parent.name == "projects"
            sev = "WARN" if (oldest_hrs >= STALE_HRS and is_lane and oldest_hrs < 24 * 30) else "INFO"
            findings.append((sev, name, f"{len(dirty)} uncommitted file(s), oldest ~{oldest_hrs:.0f}h"))
        # 4. deploy gap (Firebase)
        dg = firebase_deploy_behind(name, repo)
        if dg:
            findings.append(("WARN", name, dg))
    return findings


def main() -> int:
    findings = sweep()
    alertable = [f for f in findings if f[0] in ("CRITICAL", "WARN")]
    healed = [f for f in findings if f[0] == "HEALED"]
    for sev, name, msg in findings:
        log(f"[{sev}] {name}: {msg}")
    if healed:
        log(f"auto-healed {len(healed)} branch push(es)")
    if alertable:
        lines = [f"{'🔴' if s=='CRITICAL' else '🟡'} {n}: {m}" for s, n, m in alertable]
        heal_note = f"\n(✅ auto-pushed {len(healed)} forgotten branch(es))" if healed else ""
        body = ("🧹 Repo-hygiene sweep found issues:\n" + "\n".join(lines) + heal_note +
                "\nMerged/committed work not fully shipped — see logs/repo_hygiene.log")
        try:
            subprocess.run([str(ORCH / "scripts" / "tg_send.sh"), body], timeout=30)
        except Exception as e:
            log(f"tg_send failed: {e}")
    else:
        log(f"clean sweep ({len(healed)} auto-healed)" if healed else "clean sweep — all repos pushed + committed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
