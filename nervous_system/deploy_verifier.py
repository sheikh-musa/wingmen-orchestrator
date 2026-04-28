"""deploy_verifier — ORCHESTRATOR-STATUS-001 Option B verification worker.

Per CAI-RESP-083: bug_reports flagged status='pr_open' by cc-cosem's publisher
(Option C) get verified to status='deployed' only after BOTH (a) the PR's
merge_commit_sha lives on origin/main of the affected repo, and (b) the
deploy platform serves that commit. Vercel: target=production + meta.githubCommitSha
match. Firebase: degraded mode (origin/main sufficient; ARCH-FIREBASE-DEPLOY-SHA
tracks future SHA-embedding work).

State machine (3 cases per CAI-RESP-083):
  CASE 1: jobs.pr_number IS NULL — direct push, no PR. Verify last_commit_sha
          on default_branch via gh-api compare. Fallback path; not the norm.
  CASE 2: pr_number set, pr.merged=false. PR-open timeout: 24h from
          pr.created_at (fallback verification_started_at). Escalate after.
  CASE 3: pr_number set, pr.merged=true. target_sha = pr.merge_commit_sha.
          Verify on default_branch + deploy. Deploy-lag timeout: 30 min from
          pr.merged_at. Escalate after.

Worker is gated behind ORCHESTRATOR_VERIFY_ENABLED env flag (default false per
CAI-RESP-080 CHALLENGE-3 — Fix 1 has been dropped per Musa "proceed with A",
but the env-flag gate stays for ops safety until the worker has a soak
window post-ship).

Telegram escalation via existing nervous_system pattern (notification_log
dedup + bot.send_message). Per-row failures isolated; sweep continues.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from nervous_system import error_tracker

logger = logging.getLogger("wingmen.deploy_verifier")

REPOS_JSON = Path(__file__).parent.parent / "REPOS.json"

# Timeouts per CAI-RESP-083
_DEPLOY_LAG_TIMEOUT_MIN = 30
_PR_OPEN_TIMEOUT_HOURS = 24


# ============================================================================
# Task 9: query predicate
# ============================================================================

def _query_pending_predicate() -> str:
    """SQL predicate identifying bugs awaiting verification.

    Inclusion criteria (per CAI-RESP-083):
      - status='pr_open'                 (cc-cosem publisher's post-publish state)
      - verified_at IS NULL              (not yet verified)
      - manual_override_reason IS NULL   (not bypassed)
      - verification_escalated_at IS NULL (not already escalated — CHALLENGE-4)

    Order: oldest first (FIFO). Per-tick batch cap 20 to bound API calls.
    """
    return """
    SELECT id, repo_name, job_id, created_at, verification_started_at
      FROM bug_reports
     WHERE status = 'pr_open'
       AND verified_at IS NULL
       AND manual_override_reason IS NULL
       AND verification_escalated_at IS NULL
     ORDER BY created_at ASC
     LIMIT 20
    """


# ============================================================================
# REPOS.json + URL parsing helpers
# ============================================================================

def _load_repos() -> list[dict]:
    """Load REPOS.json. Returns list of repo dicts."""
    with open(REPOS_JSON) as f:
        return json.load(f)["repos"]


def _owner_repo_from_github_url(url: str) -> tuple[str, str] | None:
    """Parse 'https://github.com/owner/repo' → ('owner', 'repo').
    Returns None on malformed input.
    """
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return (m.group(1), m.group(2)) if m else None


def _repo_for_bug(repo_name: str) -> dict | None:
    """Look up REPOS.json entry matching repo_name (handles aliases per
    context_loader pattern)."""
    for r in _load_repos():
        if r.get("name") == repo_name:
            return r
        if repo_name in (r.get("aliases") or []):
            return r
    return None


# ============================================================================
# Task 10: GitHub PR state lookup
# ============================================================================

async def _fetch_pr_state(repo_owner: str, repo_name: str, pr_number: int) -> dict | None:
    """Fetch PR state via `gh pr view <N> --repo <owner>/<repo> --json mergeCommit,mergedAt,createdAt`.

    Returns dict with keys:
      - merged: bool
      - merge_commit_sha: str | None
      - merged_at: str | None  (ISO8601)
      - created_at: str | None (ISO8601)
    Returns None on gh failure (PR not found, network, auth).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "view", str(pr_number),
            "--repo", f"{repo_owner}/{repo_name}",
            "--json", "mergeCommit,mergedAt,createdAt",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    except (asyncio.TimeoutError, OSError) as e:
        logger.warning(f"_fetch_pr_state: gh subprocess error for {repo_owner}/{repo_name}#{pr_number}: {e}")
        return None
    if proc.returncode != 0:
        logger.debug(
            f"_fetch_pr_state: gh exit {proc.returncode} for {repo_owner}/{repo_name}#{pr_number}: "
            f"{stderr.decode(errors='replace')[:200]}"
        )
        return None
    try:
        data = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return None
    merge_commit = data.get("mergeCommit")
    return {
        "merged": bool(merge_commit and data.get("mergedAt")),
        "merge_commit_sha": merge_commit.get("oid") if merge_commit else None,
        "merged_at": data.get("mergedAt"),
        "created_at": data.get("createdAt"),
    }


async def _verify_commit_on_main(
    repo_owner: str, repo_name: str, target_sha: str, default_branch: str = "main"
) -> bool:
    """Verify target_sha is on default_branch via `gh api compare/<sha>...<branch>`.

    `gh api repos/<owner>/<repo>/compare/<sha>...<branch>` returns JSON with `status`
    field. Status meaning:
      - "identical"  → same commit (target IS branch HEAD) → on-main
      - "ahead"      → branch is ahead of target (target IS an ancestor) → on-main
      - "behind"     → branch is behind target (target NOT on branch) → not yet
      - "diverged"   → shared base only → not yet

    Returns True iff target_sha is reachable from default_branch HEAD.
    """
    if not target_sha:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "api",
            f"repos/{repo_owner}/{repo_name}/compare/{target_sha}...{default_branch}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except (asyncio.TimeoutError, OSError):
        return False
    if proc.returncode != 0:
        return False
    try:
        data = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return False
    return data.get("status") in ("identical", "ahead")


# ============================================================================
# Task 11: Vercel verification
# ============================================================================

async def _verify_vercel_deploy(
    vercel_project_id: str, target_sha: str
) -> tuple[bool, str | None]:
    """Verify the target_sha is deployed to Vercel production.

    Per CAI-RESP-083 CHALLENGE-2: must filter on `target=production` so a
    preview-READY deploy of the same SHA does NOT satisfy verification while
    production still serves older code.

    Returns (verified: bool, deploy_url: str | None).
    """
    token = os.environ.get("VERCEL_TOKEN")
    team_id = os.environ.get("VERCEL_TEAM_ID")
    if not token:
        logger.error("_verify_vercel_deploy: VERCEL_TOKEN missing — fail-loud per CAI-RESP-083")
        return False, None

    params = {
        "projectId": vercel_project_id,
        "state": "READY",
        "target": "production",
        "limit": "5",
    }
    if team_id:
        params["teamId"] = team_id

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.vercel.com/v6/deployments",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as e:
        logger.warning(f"_verify_vercel_deploy: HTTP error for {vercel_project_id}: {e}")
        return False, None
    if resp.status_code != 200:
        logger.warning(
            f"_verify_vercel_deploy: status={resp.status_code} for {vercel_project_id}: "
            f"{resp.text[:200]}"
        )
        return False, None

    body = resp.json()
    for dep in body.get("deployments", []):
        if dep.get("target") != "production":
            continue  # belt-and-braces in case the API filter loosens
        meta = dep.get("meta") or {}
        if meta.get("githubCommitSha") == target_sha:
            url = dep.get("url")
            return True, (f"https://{url}" if url and not url.startswith("http") else url)
    return False, None


# ============================================================================
# Task 12: Firebase degraded mode
# ============================================================================

def _verify_firebase_deploy(deploy_url: str | None) -> tuple[bool, str]:
    """Firebase Hosting verification — degraded mode per CAI-RESP-083.

    Returns (verified=True, diagnostic_string). The caller is responsible for
    first verifying commit-on-main via _verify_commit_on_main; if that's true
    AND we have any deploy_url to attribute, we accept the deploy.

    ARCH-FIREBASE-DEPLOY-SHA tracks the future SHA-embedding work that would
    let us verify the deploy SHA matches the on-main SHA. Until then: degraded.
    """
    return True, "firebase-degraded: commit on origin/main, deploy not independently verified"


# ============================================================================
# Task 13: dual-window timeout
# ============================================================================

def _check_timeouts(
    bug_row: dict, pr_state: dict | None, last_commit_sha: str | None,
    *, now: datetime | None = None
) -> tuple[str, str | None]:
    """Apply dual-window timeout per CAI-RESP-083.

    Returns:
      ("ok", None) — within all timeouts
      ("deploy_lag_timeout", diag) — CASE 3 with pr.merged_at > 30 min ago, not verified
      ("pr_open_timeout", diag) — CASE 2 with PR open > 24h
      ("no_pr_no_sha", diag) — CASE 1 with no PR and no last_commit_sha (cannot verify)
    """
    now = now or datetime.now(timezone.utc)

    # CASE 1: no PR recorded
    if not bug_row.get("job_id") or pr_state is None or pr_state.get("merge_commit_sha") is None and not pr_state.get("created_at"):
        # Note: pr_state=None means we couldn't fetch PR (no pr_number on job, or gh failure).
        # Treat as CASE 1 for timeout purposes.
        if not last_commit_sha:
            return "no_pr_no_sha", "no PR recorded and no last_commit_sha — verification not possible"
        return "ok", None

    # CASE 2: PR recorded, not merged → PR-open timeout
    if not pr_state.get("merged"):
        # Use pr.created_at if available, else verification_started_at
        anchor_str = pr_state.get("created_at") or bug_row.get("verification_started_at")
        if not anchor_str:
            return "ok", None  # Can't compute timeout without anchor
        try:
            anchor = datetime.fromisoformat(anchor_str.replace("Z", "+00:00"))
        except ValueError:
            return "ok", None
        if (now - anchor) > timedelta(hours=_PR_OPEN_TIMEOUT_HOURS):
            elapsed_h = int((now - anchor).total_seconds() / 3600)
            return "pr_open_timeout", f"PR open {elapsed_h}h (threshold {_PR_OPEN_TIMEOUT_HOURS}h)"
        return "ok", None

    # CASE 3: PR merged → deploy-lag timeout from pr.merged_at
    merged_at_str = pr_state.get("merged_at")
    if not merged_at_str:
        return "ok", None
    try:
        merged_at = datetime.fromisoformat(merged_at_str.replace("Z", "+00:00"))
    except ValueError:
        return "ok", None
    if (now - merged_at) > timedelta(minutes=_DEPLOY_LAG_TIMEOUT_MIN):
        elapsed_min = int((now - merged_at).total_seconds() / 60)
        return "deploy_lag_timeout", f"deploy-lag {elapsed_min} min since pr.mergedAt (threshold {_DEPLOY_LAG_TIMEOUT_MIN} min)"
    return "ok", None


# ============================================================================
# Task 14: escalation
# ============================================================================

async def _escalate_bug(
    supabase, bug_row: dict, diagnostic: str,
    *, bot=None, musa_chat_id: str | None = None,
) -> None:
    """Escalate a stuck bug per CAI-RESP-083 CHALLENGE-4 + CAI-PROCESS-ROUTING-001.

    1. UPDATE bug_reports SET verification_escalated_at = now(), verification_diagnostic = %s.
       Tombstones the row so the worker predicate excludes it next tick.
    2. INSERT P1 agent_messages addressed to cai (announce_to_agent='cai' explicit Tier-1).
    3. Optional Telegram alert with notification_log dedup_key on bug_id (one alert ever).
    """
    bug_id = bug_row["id"]

    # Step 1: tombstone via UPDATE
    try:
        await supabase.table("bug_reports").update({
            "verification_escalated_at": datetime.now(timezone.utc).isoformat(),
            "verification_diagnostic": diagnostic[:500],
        }).eq("id", bug_id).execute()
    except Exception as e:
        logger.error(f"_escalate_bug: UPDATE failed for {bug_id}: {e}")
        error_tracker.track_exception("deploy_verifier.escalate_update", e)
        return  # Don't fan out P1 if we couldn't tombstone — would re-fire next tick

    # Step 2: P1 agent_messages to cai (Tier-1 explicit)
    body = (
        f"Bug `{bug_id}` ({bug_row.get('repo_name')}) stuck in verification — escalation.\n\n"
        f"Diagnostic: {diagnostic}\n"
        f"verification_started_at: {bug_row.get('verification_started_at')}\n"
        f"job_id: {bug_row.get('job_id')}\n\n"
        f"Operator action: investigate via `bug_reports WHERE id = '{bug_id}'` + check the underlying repo's PR/deploy state. "
        f"To clear: either (a) populate `manual_override_reason` (≥20 chars) per CAI-PIPELINE-BYPASS-001, or "
        f"(b) NULL `verification_escalated_at` to retry, or (c) flip `verified_at` manually if eye-checked."
    )
    try:
        await supabase.table("agent_messages").insert({
            "from_agent": "cc-orchestrator",
            "to_agent": "cai",
            "message_type": "blocker",
            "subject": f"Deploy verification escalation — bug {str(bug_id)[:8]} ({bug_row.get('repo_name')})",
            "body": body,
            "priority": "P1",
            "requires_response": True,
            "sub_tag": "cc-orchestrator-2",
        }).execute()
    except Exception as e:
        logger.error(f"_escalate_bug: agent_messages INSERT failed for {bug_id}: {e}")
        error_tracker.track_exception("deploy_verifier.escalate_message", e)

    # Step 3: optional Telegram alert (one per bug, dedup'd)
    if bot and musa_chat_id:
        dedup_key = f"deploy_verifier:escalation:{bug_id}"
        try:
            existing = await supabase.table("notification_log").select("id").eq(
                "dedup_key", dedup_key
            ).limit(1).execute()
            if existing.data:
                return  # already alerted
            sent = await bot.send_message(
                chat_id=musa_chat_id,
                text=f"⚠️ Deploy verification escalation\n\nBug {str(bug_id)[:8]} ({bug_row.get('repo_name')}): {diagnostic}",
            )
            await supabase.table("notification_log").insert({
                "source": "deploy_verifier.escalation",
                "decision_ref": "CAI-RESP-083",
                "channel": "telegram",
                "recipient": musa_chat_id,
                "message_text": diagnostic[:500],
                "telegram_msg_id": getattr(sent, "message_id", None),
                "dedup_key": dedup_key,
            }).execute()
        except Exception as e:
            logger.warning(f"_escalate_bug: Telegram path failed for {bug_id}: {e}")
            error_tracker.track_exception("deploy_verifier.escalate_telegram", e)


# ============================================================================
# Task 15: run_deploy_verifier orchestrator
# ============================================================================

async def _verify_one_bug(
    supabase, bug_row: dict, *, bot=None, musa_chat_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """Process a single bug. Returns one of: 'verified', 'wait', 'escalated', 'skipped'.

    Per-bug try/except in the caller; this function may raise — caller logs + continues.
    """
    now = now or datetime.now(timezone.utc)
    bug_id = bug_row["id"]
    repo_name = bug_row["repo_name"]
    job_id = bug_row.get("job_id")

    repo_cfg = _repo_for_bug(repo_name)
    if not repo_cfg:
        await _escalate_bug(
            supabase, bug_row,
            f"REPOS.json has no entry for {repo_name!r}",
            bot=bot, musa_chat_id=musa_chat_id,
        )
        return "escalated"

    owner_repo = _owner_repo_from_github_url(repo_cfg.get("github") or "")
    if not owner_repo:
        await _escalate_bug(
            supabase, bug_row,
            f"REPOS.json[{repo_name}].github cannot be parsed as github.com/owner/repo",
            bot=bot, musa_chat_id=musa_chat_id,
        )
        return "escalated"
    owner, name = owner_repo

    # Set verification_started_at on first contact
    if not bug_row.get("verification_started_at"):
        try:
            await supabase.table("bug_reports").update({
                "verification_started_at": now.isoformat(),
            }).eq("id", bug_id).execute()
            bug_row["verification_started_at"] = now.isoformat()
        except Exception as e:
            logger.warning(f"_verify_one_bug: failed to set verification_started_at for {bug_id}: {e}")

    # Load jobs row to get pr_number / branch_name / merged_commit_sha + last_commit_sha
    pr_number = None
    last_commit_sha = None
    merged_commit_sha_cached = None
    if job_id:
        try:
            job_resp = await supabase.table("jobs").select(
                "pr_number, branch_name, merged_commit_sha, last_commit_sha"
            ).eq("id", job_id).limit(1).execute()
            if job_resp.data:
                j = job_resp.data[0]
                pr_number = j.get("pr_number")
                last_commit_sha = j.get("last_commit_sha")
                merged_commit_sha_cached = j.get("merged_commit_sha")
        except Exception as e:
            logger.warning(f"_verify_one_bug: jobs lookup failed for job_id={job_id}: {e}")

    # Fetch PR state if pr_number available
    pr_state = None
    if pr_number:
        pr_state = await _fetch_pr_state(owner, name, int(pr_number))

    # Apply timeouts first — escalate if elapsed
    timeout_status, timeout_diag = _check_timeouts(bug_row, pr_state, last_commit_sha, now=now)
    if timeout_status != "ok":
        await _escalate_bug(supabase, bug_row, timeout_diag, bot=bot, musa_chat_id=musa_chat_id)
        return "escalated"

    # CASE 1: no PR recorded — direct-push fallback
    if pr_state is None or pr_number is None:
        if not last_commit_sha:
            # No PR + no SHA: handled by timeout above (no_pr_no_sha)
            return "wait"
        on_main = await _verify_commit_on_main(owner, name, last_commit_sha)
        if not on_main:
            return "wait"
        # CASE 1 verified — flip via deploy verification
        verified, deploy_url = await _verify_deploy_for_repo(repo_cfg, last_commit_sha)
        if verified:
            await _flip_to_deployed(supabase, bug_id, deploy_url, now)
            return "verified"
        return "wait"

    # CASE 2: PR open, not merged
    if not pr_state.get("merged"):
        return "wait"

    # CASE 3: PR merged
    target_sha = pr_state["merge_commit_sha"]
    if not target_sha:
        return "wait"

    # Cache merged_commit_sha into jobs on first observation
    if merged_commit_sha_cached != target_sha and job_id:
        try:
            await supabase.table("jobs").update({
                "merged_commit_sha": target_sha,
            }).eq("id", job_id).execute()
        except Exception as e:
            logger.warning(f"_verify_one_bug: jobs.merged_commit_sha cache write failed: {e}")

    on_main = await _verify_commit_on_main(owner, name, target_sha)
    if not on_main:
        return "wait"  # GitHub eventual-consistency window — retry next tick

    verified, deploy_url = await _verify_deploy_for_repo(repo_cfg, target_sha)
    if verified:
        await _flip_to_deployed(supabase, bug_id, deploy_url, now)
        return "verified"
    return "wait"


async def _verify_deploy_for_repo(repo_cfg: dict, target_sha: str) -> tuple[bool, str | None]:
    """Dispatch to Vercel or Firebase verification based on repo_cfg."""
    if repo_cfg.get("vercel_project"):
        return await _verify_vercel_deploy(repo_cfg["vercel_project"], target_sha)
    if repo_cfg.get("firebase_project"):
        # Degraded mode — caller already verified commit-on-main
        verified, _diag = _verify_firebase_deploy(repo_cfg.get("deploy_url"))
        return verified, repo_cfg.get("deploy_url")
    return False, None


async def _flip_to_deployed(
    supabase, bug_id, deploy_url: str | None, now: datetime
) -> None:
    """Atomic UPDATE bug_reports → status='deployed' on verification success."""
    payload = {
        "status": "deployed",
        "verified_at": now.isoformat(),
        "resolved_at": now.isoformat(),
    }
    if deploy_url:
        payload["deploy_url"] = deploy_url
    await supabase.table("bug_reports").update(payload).eq("id", bug_id).execute()
    logger.info(f"deploy_verifier: bug {str(bug_id)[:8]} verified → deployed (url={deploy_url})")


async def run_deploy_verifier(
    supabase, bot=None, musa_chat_id: str | None = None,
) -> None:
    """Main entry — gated behind ORCHESTRATOR_VERIFY_ENABLED env flag.

    Per CAI-RESP-080 CHALLENGE-3: default off during soak window. Operator
    flips ORCHESTRATOR_VERIFY_ENABLED=true when ready.
    """
    if os.environ.get("ORCHESTRATOR_VERIFY_ENABLED", "false").lower() != "true":
        logger.debug("deploy_verifier: ORCHESTRATOR_VERIFY_ENABLED!=true; skipping tick")
        return

    try:
        result = await supabase.table("bug_reports").select(
            "id, repo_name, job_id, created_at, verification_started_at"
        ).eq("status", "pr_open").is_("verified_at", "null").is_(
            "manual_override_reason", "null"
        ).is_("verification_escalated_at", "null").order("created_at", desc=False).limit(20).execute()
    except Exception as e:
        logger.error(f"run_deploy_verifier: query failed: {e}")
        error_tracker.track_exception("deploy_verifier.query", e)
        return

    bugs = result.data or []
    if not bugs:
        logger.debug("deploy_verifier: no pending bugs")
        return

    verified_count = 0
    escalated_count = 0
    waiting_count = 0
    for bug in bugs:
        try:
            outcome = await _verify_one_bug(supabase, bug, bot=bot, musa_chat_id=musa_chat_id)
            if outcome == "verified":
                verified_count += 1
            elif outcome == "escalated":
                escalated_count += 1
            else:
                waiting_count += 1
        except Exception as e:
            logger.error(f"run_deploy_verifier: per-bug exception for {bug.get('id')}: {e}")
            error_tracker.track_exception("deploy_verifier.per_bug", e)
            waiting_count += 1
            continue

    logger.info(
        f"deploy_verifier: swept {len(bugs)} bugs — "
        f"{verified_count} verified, {escalated_count} escalated, {waiting_count} waiting"
    )
