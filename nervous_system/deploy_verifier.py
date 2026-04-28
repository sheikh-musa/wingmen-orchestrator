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
from datetime import datetime, timezone, timedelta

from nervous_system import error_tracker

logger = logging.getLogger("wingmen.deploy_verifier")
