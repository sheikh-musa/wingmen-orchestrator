#!/usr/bin/env python3
"""stale_pass_verdict_watchdog.py — pages orch-console ONCE for any open PR that
cc-quality PASSed but that has had no merge and no new commit for over
THRESHOLD_S (bus #85014, half 2).

WHY: cc-quality PASSed cosem-adcda PR#852/855/857 but addressed the verdicts to
the wrong instance (bus_send.py's refuse_if_pr_verdict_misrouted, PR#692, closes
that routing hole going forward). This is the backstop for the SYMPTOM, not just
the cause: even with correct routing, a PASS can still silently stall (the
recipient is wedged, the message is missed, whatever) -- this watches outcomes
(did the PR actually move?), not delivery.

Scans recent cc-quality verdict messages for PASS-shaped PR# mentions, resolves
each PR's live state + latest commit time via `gh`, and pages once (ever, per
verdict+PR pair -- not hourly like the other watchdogs, per Musa/console's
"pages ... once" framing) when the PR is still OPEN and nothing has happened to
it (no merge, no commit after the verdict) for over THRESHOLD_S.

GATING (CAI-501 charter §3 shape): DETECT + the operator alert are UNGATED -- a
safety page is never silenced by lease state. Ops-only: own agent_id
(cc-fleet-health, already registered), never a governance write.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

LOOKBACK_MIN = int(os.environ.get("STALE_PASS_LOOKBACK_MIN", str(6 * 60)))    # how far back to scan verdicts
THRESHOLD_S = int(os.environ.get("STALE_PASS_THRESHOLD_S", str(60 * 60)))     # >60min idle since verdict
STATE_PATH = _ORCH_DIR / "state" / "stale_pass_verdict_watchdog.json"
SUBJECT_PREFIX = "[stale-pass-verdict]"   # dedup anchor — keep STABLE
REPO_CACHE_TTL_S = 60 * 60   # known-repo map is expensive (one git call per worktree); refresh hourly


# ── pure decision core (unit-tested; touches nothing) ────────────────────────
def extract_pass_pr_mentions(text):
    """PURE. Scans `text` line by line for a PR# mentioned together with PASS on
    the same line, skipping a line that also reads as a FAIL/BLOCK verdict for
    that PR (cc-quality's own message shape: 'PR#653 @ 58fa81a (...) — PASS.',
    'cosem-adcda PR#857 -- PASS'). -> list of (pr_number:str, repo_hint:str|None).
    A single message can legitimately bundle several PRs, each its own line.

    Also skips a QUOTED-CITATION false positive (bus #85217, console): a
    verdict message can quote a PASS-shaped string as a code example while
    explaining an UNRELATED bug, e.g. cc-quality's own #85078 (a verdict on
    orchestrator PR#692/693/694) illustrating PR#692's coverage gap with
    `refuse_if_pr_verdict_misrouted('cc-quality','cc-cosem-adcda-1',
    'cosem-adcda PR#857 PASS') -> no-op (did NOT fire)` -- which got
    misread as a fresh PASS verdict on cosem-adcda PR#857, paging on a
    9-day-stale citation instead of the real PR#857 verdict (#83927). A
    genuine verdict announcement never closes PASS with a quote mark; a
    quoted code citation does (the string literal's closing quote).
    Checked at the match's own end, not line-wide, since the line can also
    legitimately open quotes earlier (e.g. an apostrophe in prose) without
    that affecting this specific match."""
    mentions = []
    for line in (text or "").splitlines():
        m = re.search(r"PR\s*#\s*(\d+)", line, re.IGNORECASE)
        if not m:
            continue
        pass_m = re.search(r"\bPASS\b", line, re.IGNORECASE)
        if not pass_m:
            continue
        # (?!-) keeps a hyphen-compound ("fail-safe", "fail-closed", a
        # block-list) from being misread as a FAIL/BLOCK verdict keyword --
        # found as a byproduct of writing the quoted-citation test above:
        # \bFAIL\b alone DOES match "fail" inside "fail-safe" (hyphen is a
        # non-word boundary), which would wrongly SUPPRESS a real PASS
        # verdict that happens to use the word "fail-safe" in its own text.
        if re.search(r"\bFAIL(?!-)\b|\bBLOCK(?!-)\b|\bNOT\s+PASS\b", line, re.IGNORECASE):
            continue
        if line[pass_m.end():pass_m.end() + 1] in ("'", '"'):
            continue  # quoted-citation false positive, not a real verdict
        # Anchored to END-of-string (right before THIS match's own "PR#"),
        # not a line-wide search -- a line mentioning several PRs (bus
        # #85217, real incident: "...Area 2 step 2a @ 07440aed — PASS
        # (advisory; cosem-adcda...). Same shape as the Area-1 PR#855 I
        # already passed...") must not borrow a hyphenated word adjacent to
        # a DIFFERENT PR# mention elsewhere on the line as this PR#'s hint.
        hint_m = re.search(
            r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s+$", line[:m.start()], re.IGNORECASE
        )
        mentions.append((m.group(1), hint_m.group(1) if hint_m else None))
    return mentions


def is_stale(pr_state, merged_at, latest_commit_epoch, verdict_epoch, now, threshold_s=THRESHOLD_S):
    """PURE. True iff the PR is still OPEN, no new commit landed after the
    verdict, and the verdict is older than threshold_s. `latest_commit_epoch`
    may be None (couldn't resolve) -- treated as "no commit after the verdict"
    since there's nothing to compare, never guessed as recent."""
    if pr_state != "OPEN" or merged_at:
        return False
    if latest_commit_epoch is not None and latest_commit_epoch > verdict_epoch:
        return False  # a new commit after the verdict is an active response, not a stall
    return (now - verdict_epoch) >= threshold_s


def alert_message(repo, pr_number, verdict_id, to_agent, age_s):
    """PURE (subject, body) for the operator alert."""
    subject = f"{SUBJECT_PREFIX} {repo} PR#{pr_number} PASSed but idle {age_s}s, no merge/commit"
    body = (
        f"TL;DR: cc-quality PASSed {repo} PR#{pr_number} (verdict bus #{verdict_id}, addressed to "
        f"{to_agent}) {age_s}s ago and nothing has happened since -- still open, no merge, no new "
        f"commit.\n\n"
        f"IMPACT: either {to_agent} never saw the verdict, or saw it and stalled -- either way the "
        f"PR is sitting ready-to-merge with no one acting on it.\n"
        f"WHAT TO DO: check {to_agent} is alive and has seen bus #{verdict_id}; if the lane is wedged, "
        f"re-nudge or recycle it; if the verdict itself was misrouted, bus_send.py's "
        f"refuse_if_pr_verdict_misrouted (PR#692) should prevent a fresh instance of this going "
        f"forward, but this PR already shipped before that landed. Detect-only: I did not touch "
        f"the PR or the lane."
    )
    return subject, body


# ── DB / state / gh shell (never touches a PR or a lane; only detects + pages) ──
def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)  # atomic


def _recent_quality_verdicts(cur, lookback_min=LOOKBACK_MIN):
    """(id, to_agent, body, created_epoch) for cc-quality messages in the lookback
    window that mention PASS and a PR# at all (cheap pre-filter; the per-line
    PASS+PR# extraction happens in extract_pass_pr_mentions)."""
    # a %s placeholder INSIDE a quoted interval literal is not substituted as a
    # value -- psycopg splices in the literal text "$1", which postgres parses
    # as a near-zero interval with no error at all (verified live: returned 0
    # rows instead of ~160). Multiplying a plain numeric parameter against a
    # fixed-unit interval, with the parameter OUTSIDE any quotes, is correct.
    cur.execute(
        "SELECT id, to_agent, body, EXTRACT(EPOCH FROM created_at)::bigint "
        "FROM agent_messages WHERE from_agent='cc-quality' "
        "AND created_at > now() - (%s * interval '1 minute') "
        "AND body ~* 'PASS' AND body ~* 'PR\\s*#\\s*[0-9]'",
        (lookback_min,),
    )
    return cur.fetchall()


def _repo_from_remote_url(url: str) -> "tuple[str, str] | None":
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", url or "")
    return (m.group(1), m.group(2)) if m else None


def _known_repos(cur, state: dict, now: int) -> "list[tuple[str, str]]":
    """Distinct (owner, repo) pairs across registered lanes' worktrees, cached in
    state for REPO_CACHE_TTL_S -- one `git remote get-url` per distinct worktree
    is too expensive to repeat every run. Always includes this checkout's own
    repo (self-referential orchestrator PRs)."""
    cached = state.get("known_repos_cache")
    if cached and (now - cached.get("at", 0)) < REPO_CACHE_TTL_S:
        return [tuple(r) for r in cached["repos"]]
    repos = set()
    try:
        out = subprocess.run(
            ["git", "-C", str(_ORCH_DIR), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        self_repo = _repo_from_remote_url(out)
        if self_repo:
            repos.add(self_repo)
    except Exception:
        pass
    cur.execute("SELECT DISTINCT worktree_path FROM fleet_lanes WHERE worktree_path IS NOT NULL")
    for (path,) in cur.fetchall():
        try:
            out = subprocess.run(
                ["git", "-C", path, "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            continue
        r = _repo_from_remote_url(out)
        if r:
            repos.add(r)
    repos = sorted(repos)
    state["known_repos_cache"] = {"at": now, "repos": [list(r) for r in repos]}
    return repos


def _pr_view(owner: str, repo: str, pr_number: str) -> "dict | None":
    """gh pr view's state/mergedAt/commits for one PR, or None on any failure
    (wrong repo guess, unknown PR, network hiccup) -- never blocks the scan."""
    try:
        out = subprocess.run(
            ["gh", "pr", "view", pr_number, "-R", f"{owner}/{repo}",
             "--json", "state,mergedAt,commits"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout
        return json.loads(out)
    except Exception:
        return None


def _resolve_pr(owner_candidates, repo_hint, pr_number):
    """When repo_hint is given, resolve ONLY against that repo -- never fall
    through to a different one (bus #85217, console: the same cross-repo
    collision class PR#697 fixed in bus_send.py -- a PR number is not unique
    fleet-wide, e.g. orchestrator's real PR#636 vs cosem-platform's real
    PR#636, so a hint that happens not to match must mean 'unresolved', not
    license to go looking elsewhere and cite a different PR under the same
    number). Only when NO hint was extracted does this try every known repo
    (no hint to trust OR distrust) and return the first match. Either way:
    None if the PR isn't found in any permitted candidate repo -- never
    guessed."""
    if repo_hint:
        candidates = [(o, r) for (o, r) in owner_candidates if r == repo_hint]
    else:
        candidates = list(owner_candidates)
    for owner, repo in candidates:
        pr = _pr_view(owner, repo, pr_number)
        if pr is not None:
            return owner, repo, pr
    return None


def _latest_commit_epoch(pr_json: dict) -> "int | None":
    commits = pr_json.get("commits") or []
    if not commits:
        return None
    dates = [c.get("committedDate") for c in commits if c.get("committedDate")]
    if not dates:
        return None
    import datetime
    newest = max(dates)
    try:
        return int(datetime.datetime.fromisoformat(newest.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _alert(cur, conn, repo, pr_number, verdict_id, to_agent, age_s, dry: bool) -> str:
    subject, body = alert_message(repo, pr_number, verdict_id, to_agent, age_s)
    if dry:
        print(f"    WOULD-ALERT orch-console: {subject}")
        return "would-alert"
    cur.execute(
        "SELECT * FROM post_agent_message('cc-fleet-health','orch-console','blocker',%s,%s,"
        "'P1',false,%s,%s,%s,%s)", [subject, body, str(uuid.uuid4()), None, False, None])
    conn.commit()
    print(f"    ALERTED orch-console: {subject}")
    return "alerted"


def run(conn, dry: bool = False) -> int:
    state = _load_state()
    now = int(time.time())
    with conn.cursor() as cur:
        verdicts = _recent_quality_verdicts(cur)
        known_repos = _known_repos(cur, state, now)

        print(f"stale-pass-verdict-watchdog — {'DRY-RUN' if dry else 'LIVE'} — "
              f"{len(verdicts)} candidate verdict(s), {len(known_repos)} known repo(s)")

        alerted_keys = set(state.get("alerted", []))
        n_alerted = 0
        # same PR# is commonly mentioned across several verdict messages in the
        # lookback window (re-reviews, bundled batches) -- cache each (repo_hint,
        # pr_number) resolution for the DURATION OF THIS RUN ONLY so it costs one
        # `gh` round trip, not one per mention. Never persisted across runs: a
        # merge/close between runs must be seen fresh every time.
        resolve_cache: dict = {}

        def _resolve_cached(hint, number):
            ck = (hint, number)
            if ck not in resolve_cache:
                resolve_cache[ck] = _resolve_pr(known_repos, hint, number)
            return resolve_cache[ck]

        for verdict_id, to_agent, body, verdict_epoch in verdicts:
            for pr_number, repo_hint in extract_pass_pr_mentions(body):
                key = f"{verdict_id}:{pr_number}"
                if key in alerted_keys:
                    continue
                resolved = _resolve_cached(repo_hint, pr_number)
                if resolved is None:
                    continue  # unresolvable -- never guess, just skip this scan
                owner, repo, pr_json = resolved
                latest_commit = _latest_commit_epoch(pr_json)
                if not is_stale(pr_json.get("state"), pr_json.get("mergedAt"),
                                 latest_commit, verdict_epoch, now):
                    continue
                age = now - verdict_epoch
                result = _alert(cur, conn, repo, pr_number, verdict_id, to_agent, age, dry)
                if result == "alerted":
                    alerted_keys.add(key)
                    n_alerted += 1

        state["alerted"] = sorted(alerted_keys)
        state["_heartbeat_epoch"] = now
        if not dry:
            _save_state(state)

    print(f"    done — {n_alerted} new alert(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="stale cc-quality PASS verdict watchdog (bus #85014)")
    ap.add_argument("--dry-run", action="store_true", help="detect + log only; no alert, no state write")
    args = ap.parse_args()
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(_ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 2
    conn = psycopg.connect(dsn)
    try:
        return run(conn, dry=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
