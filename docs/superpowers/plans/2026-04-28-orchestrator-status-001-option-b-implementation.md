# ORCHESTRATOR-STATUS-001 Option B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the verification worker that flips `bug_reports.status` from `pr_open` to `deployed` only after origin/main contains the merge commit AND the deploy platform serves it. Closes ORCHESTRATOR-STATUS-001's "label lies when commit unpushed" gap with a mechanical second-line defense behind cc-cosem's Option C agent push-contract.

**Architecture:** Async Python worker `nervous_system/deploy_verifier.py` polled from `wingmen_orch.py` main loop every 5 min (counter ≥ 10 at POLL_INTERVAL=30s) gated behind `ORCHESTRATOR_VERIFY_ENABLED` env flag (default off per CAI-RESP-080 CHALLENGE-3). 3-case state machine per CAI-RESP-083: CASE 1 (no PR recorded — direct push fallback), CASE 2 (PR recorded but not merged — 24h PR-open timeout), CASE 3 (PR merged — verify merge_commit_sha on origin/main + deploy platform). Vercel verified via `target=production` + `meta.githubCommitSha` match; Firebase degraded-mode (commit-on-main only — ARCH-FIREBASE-DEPLOY-SHA tracks the future SHA-embedding work). Dual timeouts: 30-min deploy-lag from `pr.merged_at`, 24h PR-open from `pr.created_at` (or `verification_started_at` for CASE 1).

**Tech Stack:** PostgreSQL 15 (Supabase), psycopg3, Python asyncio, supabase-py async client, `gh` CLI for GitHub PR API, httpx for Vercel REST API.

**Parent decisions:** ORCHESTRATOR-STATUS-001 (P1, 2026-04-23), CAI-RESP-083 (CHALLENGE-1 resolution), CAI-RESP-080 (Refinement 2 review protocol + CHALLENGE-3 ship order), CAI-PIPELINE-BYPASS-001 (manual_override_reason fold-in + AC-5 skills/ directive), CAI-RESP-091 (notification_log destination ruling + the orch_self_audit precedent).

**Coordinated with:** cc-cosem (msg #874 + #955) — schema migration is my scope; her publisher merge fires same-day after my migration applies.

**Out of scope:**
- Option A finer state model (deferred per ORCHESTRATOR-STATUS-001 until 50bugs/month or 4+ repos using pipeline)
- Webhook infrastructure (polling sufficient at current scale)
- Firebase deploy SHA embedding (ARCH-FIREBASE-DEPLOY-SHA tracked-deferred, id 543)
- Snapshot writer relocation (ARCH-SNAPSHOT-WRITER-RELOCATION tracked-deferred, id 560 — folds in after Option B is live)
- cc-cosem's publisher code itself (her commits already exist on `feat/orchestrator-status-001-publisher`; she merges after me)

---

## Pre-flight facts (verified on remote orchestrator Supabase + main HEAD `2491884` 2026-04-28)

- `bug_reports.status` CHECK is the original 10-value set: `new, diagnosing, proposed, approved, deploying, deployed, verified, rejected, escalated, still_broken`. NO `pr_open / push_failed / pr_failed` yet. Schema comment at `schema.sql:89-94` flags the expansion as "must expand"; this plan ships it.
- `bug_reports` has NO Option B columns — all 5 are net-new.
- `jobs` has NO `pr_number`, `branch_name`, `merged_commit_sha` — net-new.
- 5 historical `bug_reports` rows have `status='deployed' AND deploy_url IS NULL` — backfill targets:
  - `418af36c-a0ae-4f55-b2a7-7fa0d993236b` (cosem-adcda)
  - `2386d2a4-6c7c-4768-b7ef-36dae38fe00b` (hifz)
  - `0f80ee00-3b94-4a33-ae83-c2463184bbbc` (hifz)
  - `19d0c5bb-3643-4ce5-a14f-6aa7f34bc782` (cosem-tdu) — NEW find, plan AC-B-8 expanded to 5 from 3
  - `effb5a09-e98d-40e3-98c8-51bf385a302f` (cosem-tdu) — NEW find, same
- `VERCEL_TOKEN` + `VERCEL_TEAM_ID` both set in `.env` (verified). Worker fails-loud at startup on missing.
- `gh` CLI authenticated (used by cc-cosem's existing publisher, so auth is already operational).
- `notification_log` is the audit destination per CAI-RESP-091 (NOT `audit_log` which is ihsanos amanah-bound).
- cc-cosem publisher commits live on `feat/orchestrator-status-001-publisher` (5 commits since main, ready-to-merge after this PR lands).

---

## File structure

- **Create:** `supabase/migrations/20260428_orchestrator_status_001_option_b.sql` — single atomic migration with 6 sections (bug_reports columns, status CHECK expansion, manual_override_reason CHECK, jobs columns, boot_briefing manual_override_bugs section, post-apply assertion gate).
- **Create:** `nervous_system/deploy_verifier.py` — verifier worker module. State machine, GitHub + Vercel + Firebase verification paths, escalation.
- **Create:** `tests/test_deploy_verifier.py` — unit tests (mocked) + live-DB regression test for the migration shape (Gap-3-class catcher pattern from PR #10).
- **Create:** `skills/bypass-approval-policy.md` — canonical directive per CAI-PIPELINE-BYPASS-001 AC-5. First file under `skills/` directory; ships the substrate inline (per cai gap-scan thread default if cai silent on Gap 1 within 24h).
- **Create:** `scripts/backfill_option_b_historical_bugs.py` — separate idempotent backfill for the 5 historical rows (per CAI-RESP-093 R1: backfill ships as a separate script, not embedded in migration).
- **Modify:** `wingmen_orch.py` — wire `run_deploy_verifier` into main loop with `ORCHESTRATOR_VERIFY_ENABLED` env flag (default `false`).
- **Modify:** `tests/test_orch_self_audit.py` — extend `migration_consistency` audit to recognize the new migration.

---

## Task 0: Option C ship prerequisite (cc-cosem coord — NOT cc-orchestrator scope)

**Per CAI-RESP-097 GAP 2 ruling:** plan-write proceeds, but plan EXECUTION (Tasks 2+ schema apply, Tasks 9-15 worker, Task 16 wiring) waits on cc-cosem's Option C ship landing the following on the orchestrator Supabase:

- `bug_reports.status` CHECK additions: `'pr_open'`, `'push_failed'`, `'pr_failed'`
- `jobs.pr_number INT NULL` (cc-cosem writes from `publish_job_commit`)
- `jobs.branch_name TEXT NULL` (same)

**Reference**: cc-cosem's plan at `docs/superpowers/plans/2026-04-24-orchestrator-status-001-agent-push-contract.md` and her partial branch `feat/orchestrator-status-001-publisher` (5 commits ready, holding for my migration per #874 + #955).

**Sequencing per cc-cosem #955**: she ships her schema migration + publisher merge SAME-DAY after my Option B migration applies. To avoid the race (column writes against an unexpanded CHECK), my migration applies first, hers immediately after.

**Conflict resolution if my Section 2 (status CHECK expansion) duplicates cc-cosem's:**
- If my migration runs first AND her migration adds `pr_open`/`push_failed`/`pr_failed`: her DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT will replace my expanded CHECK with the same values. Idempotent; no harm.
- If her migration runs first AND mine adds same values: same result. Idempotent.
- The CHECK values (`pr_open`, `push_failed`, `pr_failed`) MUST be identical across both migrations. Coordinate via shared constants if drift risk emerges.

**Escalation criterion (per CAI-RESP-097)**: if cc-cosem reports blocker / bandwidth-elsewhere / review-hold, surface as a separate filing for cai ruling. Do NOT silently absorb the timeline into cc-orchestrator's queue.

- [ ] **Step 1: Verify cc-cosem branch state**

Run: `git log --all --oneline | grep "orch-status-001" | head -10`
Expected: see 5 publisher commits (`51f38a9` constants → `af172d9` git_publisher core → `43069f1` ralph_runner wire → `6504084` fixer prompt + schema comments → plus any subsequent additions cc-cosem ships).

- [ ] **Step 2: If cc-cosem confirms Option C is ready-to-merge**

Apply this Option B migration first (Tasks 2-8 in sequence), confirm green, then ping cc-cosem to merge Option C same-day.

- [ ] **Step 3: If cc-cosem reports blocker**

File `cai_messages` to cai surfacing the blocker, do NOT proceed with Option B execution Tasks 2+. Wait for cai sequencing ruling.

---

## Task 1: Pre-flight verification

**Files:** none (read-only).

- [ ] **Step 1: Verify env + live state**

Run:
```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
print('VERCEL_TOKEN:', 'set' if os.environ.get('VERCEL_TOKEN') else 'MISSING')
print('VERCEL_TEAM_ID:', 'set' if os.environ.get('VERCEL_TEAM_ID') else 'MISSING')
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT count(*) FROM information_schema.columns WHERE table_name='bug_reports' AND column_name IN ('verified_at','verification_started_at','verification_diagnostic','manual_override_reason','verification_escalated_at')\")
        print('bug_reports new-cols already present:', cur.fetchone()[0])
        cur.execute(\"SELECT count(*) FROM information_schema.columns WHERE table_name='jobs' AND column_name IN ('pr_number','branch_name','merged_commit_sha')\")
        print('jobs new-cols already present:', cur.fetchone()[0])
        cur.execute(\"SELECT count(*) FROM bug_reports WHERE status='deployed' AND deploy_url IS NULL\")
        print('bugs with status=deployed + NULL deploy_url:', cur.fetchone()[0])
"
```

Expected: `VERCEL_TOKEN: set`, `VERCEL_TEAM_ID: set`, both `new-cols already present: 0`, `bugs with status=deployed + NULL deploy_url: 5`.

- [ ] **Step 2: Verify cc-cosem publisher commits visible**

Run: `git log --all --oneline | grep "orch-status-001" | head -10`
Expected: see commits `51f38a9` (constants), `af172d9` (git_publisher core), `43069f1` (ralph_runner wire), `6504084` (fixer prompt + schema comments) on `feat/orchestrator-status-001-publisher`.

- [ ] **Step 3: Verify gh auth**

Run: `gh auth status`
Expected: `Logged in to github.com as <user>`.

---

## Task 2: Scaffold migration + worker module + test file + skills directory

**Files:**
- Create: `supabase/migrations/20260428_orchestrator_status_001_option_b.sql`
- Create: `nervous_system/deploy_verifier.py`
- Create: `tests/test_deploy_verifier.py`
- Create: `skills/bypass-approval-policy.md`
- Create: `scripts/backfill_option_b_historical_bugs.py`

- [ ] **Step 1: Migration preamble (BEGIN; no COMMIT yet — added in Task 8)**

Create `supabase/migrations/20260428_orchestrator_status_001_option_b.sql`:

```sql
-- ORCHESTRATOR-STATUS-001 Option B: verification worker schema
-- Per CAI-RESP-083 (CHALLENGE-1 resolution) + CAI-PIPELINE-BYPASS-001
-- (manual_override_reason fold-in) + cc-cosem #955 ownership boundary.
--
-- Sections:
--   1. bug_reports new columns (verified_at, verification_started_at,
--      verification_diagnostic, manual_override_reason, verification_escalated_at)
--   2. bug_reports.status CHECK expansion (pr_open, push_failed, pr_failed)
--   3. bug_reports manual_override_reason CHECK constraint
--      (status='deployed' → manual_override_reason IS NULL OR length≥20)
--   4. jobs columns (pr_number, branch_name, merged_commit_sha)
--   5. boot_briefing manual_override_bugs section
--   6. Post-apply assertion gate
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + DO-block guards on constraints.
-- Pattern matches Batch 2 BUG-030 idempotency restructure (per CAI-RESP-081
-- review of the schema_migrations re-apply path).
--
-- Parent decisions: ORCHESTRATOR-STATUS-001, CAI-RESP-083, CAI-PIPELINE-BYPASS-001.

BEGIN;
```

- [ ] **Step 2: Worker module preamble**

Create `nervous_system/deploy_verifier.py`:

```python
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
```

- [ ] **Step 3: Test file scaffold**

Create `tests/test_deploy_verifier.py`:

```python
"""Tests for nervous_system.deploy_verifier (ORCHESTRATOR-STATUS-001 Option B)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)
```

- [ ] **Step 4: skills/ substrate seed**

Create `skills/bypass-approval-policy.md`:

```markdown
# bypass-approval-policy

**Source decision:** CAI-PIPELINE-BYPASS-001 (filed 2026-04-23, Option (b) ruling).

**Audience:** all CC families running autonomous-fix pipelines. Consumed via transclusion (markdown include / canonical reference) by `cc-cosem`, `cc-scholar`, `cc-orchestrator`, future families.

**Owner:** cc-orchestrator (skills/ directory authorship per CAI-AGENTS-002).

## When this applies

When a CC family's autonomous-fix pipeline encounters a structural gap that prevents normal flow (e.g., REPOS.json missing a repo entry, dispatcher crash, PR-flow failure), AND the CC has a substantive understanding of the diagnosis + a fix preview ready.

## What "bypass" means

Direct application of the fix outside the orchestrator's pipeline (manual git commit-and-push, manual deploy trigger, manual `bug_reports.status='deployed'` write).

## Procedure

1. **CC files diagnosis + pipeline-gap report + bypass-approval-request** in agent_messages addressed to musa (P1, requires_response=true). Body must include:
   - Diagnosis (root cause + grounding code/log refs)
   - Pipeline gap (which step failed and why)
   - Fix preview (what the bypass will do)
   - Target CC ack (acknowledging the bypass shape)

2. **Musa approval is captured per-incident** (bug_id, reason, fix preview, target CC ack). Approval is explicit; no implicit/silent consent.

3. **No strategic_decisions row required per-incident.** Bypass is captured in `bug_reports.manual_override_reason` (≥20 chars trimmed) at the time of the manual `status='deployed'` write. Schema CHECK enforces the constraint.

4. **Emergency override** (Musa unreachable, fix is <1hr risk): post-hoc P1 notification to Musa + cai within 15 minutes of the manual write. Same `manual_override_reason` discipline applies.

## What this is NOT

- NOT a license for routine pipeline-gap workarounds. Recurring bypasses are evidence of a structural issue requiring a fix to the pipeline itself.
- NOT a path to silent deploy. Every bypass leaves a `manual_override_reason` audit trail visible in `boot_briefing.manual_override_bugs`.
- NOT a replacement for ORCHESTRATOR-STATUS-001 Option B verification. Bypassed rows skip Option B's verifier (which checks `manual_override_reason IS NULL` in its predicate). The bypass is the structural acknowledgment that this row was NOT mechanically verified.

## Boot_briefing surface

`boot_briefing.manual_override_bugs` shows recent overrides with bug_id, repo, reason prefix. Operators review weekly or on incident.

## References

- CAI-PIPELINE-BYPASS-001 (parent decision)
- ORCHESTRATOR-STATUS-001 Option B (verifier predicate excludes manual_override_reason IS NOT NULL rows)
- bug_reports schema: `manual_override_reason TEXT NULL` + CHECK constraint
- cc-scholar bug 2386d2a4 (first retroactive approval, set the precedent)
```

- [ ] **Step 5: Backfill script scaffold**

Create `scripts/backfill_option_b_historical_bugs.py`:

```python
"""Backfill 5 historical bugs that have status='deployed' but deploy_url=NULL.

Per CAI-RESP-093 R1 (Refinement): backfill ships as a separate idempotent
script, not embedded in the migration. Run AFTER migration applies + after
test suite green; reduces blast radius if any of the 5 SHAs/URLs are wrong.

Bugs backfilled:
  - 418af36c (cosem-adcda) — manual remediation via c5fb68b push 2026-04-23
  - 2386d2a4 (hifz)        — manual override per CAI-PIPELINE-BYPASS-001
  - 0f80ee00 (hifz)        — same override category
  - 19d0c5bb (cosem-tdu)   — NEW find (pre-flight Task 1 surfaced)
  - effb5a09 (cosem-tdu)   — NEW find (pre-flight Task 1 surfaced)

Idempotent: each UPDATE is keyed on bug_id + an existing-state guard
(WHERE clause checks deploy_url IS NULL or manual_override_reason IS NULL
to prevent overwriting a previously-backfilled value).
"""
import os
from dotenv import load_dotenv
load_dotenv()
import psycopg
```

- [ ] **Step 6: Commit scaffold**

```bash
git add supabase/migrations/20260428_orchestrator_status_001_option_b.sql \
        nervous_system/deploy_verifier.py \
        tests/test_deploy_verifier.py \
        skills/bypass-approval-policy.md \
        scripts/backfill_option_b_historical_bugs.py
git commit -m "chore(option-b): scaffold migration + worker + skills/ + backfill script"
```

---

## Task 3: Migration Section 1 — bug_reports new columns (TDD)

**Files:**
- Modify: `supabase/migrations/20260428_orchestrator_status_001_option_b.sql`
- Modify: `tests/test_deploy_verifier.py`

- [ ] **Step 1: Failing test asserts 5 new columns**

Append to test file:

```python
def test_bug_reports_has_option_b_columns():
    """AC-B-7 part 1: 5 new columns on bug_reports for verifier state."""
    expected = {
        "verified_at": ("timestamp with time zone", "YES"),
        "verification_started_at": ("timestamp with time zone", "YES"),
        "verification_diagnostic": ("text", "YES"),
        "manual_override_reason": ("text", "YES"),
        "verification_escalated_at": ("timestamp with time zone", "YES"),
    }
    import psycopg
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'bug_reports'
                   AND column_name = ANY(%s)
                """,
                (list(expected.keys()),),
            )
            actual = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert actual == expected, f"columns mismatch: expected {expected}, got {actual}"
```

- [ ] **Step 2: Verify test fails**

Run: `.venv/bin/python -m pytest tests/test_deploy_verifier.py::test_bug_reports_has_option_b_columns -v`
Expected: FAIL (columns don't exist).

- [ ] **Step 3: Append Section 1 to migration**

Append:

```sql
-- ============================================================
-- SECTION 1: bug_reports new columns (Option B verifier state)
-- ============================================================
ALTER TABLE bug_reports
  ADD COLUMN IF NOT EXISTS verified_at              TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS verification_started_at  TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS verification_diagnostic  TEXT NULL,
  ADD COLUMN IF NOT EXISTS manual_override_reason   TEXT NULL,
  ADD COLUMN IF NOT EXISTS verification_escalated_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN bug_reports.verified_at IS
  'CAI-RESP-083: timestamp when Option B verifier confirmed deploy. NULL = not yet verified or never reaches verifier (manual_override path).';
COMMENT ON COLUMN bug_reports.verification_started_at IS
  'CAI-RESP-083: timestamp of first verifier tick on this bug. Used for PR-open 24h timeout (CASE 2) and CASE 1 fallback.';
COMMENT ON COLUMN bug_reports.verification_diagnostic IS
  'CAI-RESP-083: human-readable diagnostic on escalation (e.g., "firebase-degraded", "pr-open-too-long", "no-pr-no-sha"). NULL on happy path.';
COMMENT ON COLUMN bug_reports.manual_override_reason IS
  'CAI-PIPELINE-BYPASS-001: operator-authorized bypass reason (≥20 chars trimmed). Verifier predicate excludes rows where this is set.';
COMMENT ON COLUMN bug_reports.verification_escalated_at IS
  'CAI-RESP-083 CHALLENGE-4: tombstone for already-escalated rows. Worker predicate excludes IS NOT NULL to prevent infinite P1 spam loop.';
```

Apply Section 1 directly:

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
sql = '''
ALTER TABLE bug_reports
  ADD COLUMN IF NOT EXISTS verified_at              TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS verification_started_at  TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS verification_diagnostic  TEXT NULL,
  ADD COLUMN IF NOT EXISTS manual_override_reason   TEXT NULL,
  ADD COLUMN IF NOT EXISTS verification_escalated_at TIMESTAMPTZ NULL;
'''
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=False) as c:
    with c.cursor() as cur:
        cur.execute(sql)
    c.commit()
print('Section 1 applied')
"
```

- [ ] **Step 4: Verify test passes**

Run: `.venv/bin/python -m pytest tests/test_deploy_verifier.py::test_bug_reports_has_option_b_columns -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260428_orchestrator_status_001_option_b.sql tests/test_deploy_verifier.py
git commit -m "feat(option-b): bug_reports verification state columns (AC-B-7)"
```

---

## Task 4: Migration Section 2 — bug_reports.status CHECK expansion (TDD)

**Files:**
- Modify: migration file + test file

- [ ] **Step 1: Failing test for `pr_open` insert**

Append:

```python
def test_bug_reports_status_check_accepts_pr_open():
    """AC-B-9: status CHECK includes pr_open / push_failed / pr_failed
    so cc-cosem's publisher can write status='pr_open' atomically."""
    import psycopg, uuid
    test_id = str(uuid.uuid4())
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bug_reports
                  (id, reporter_name, reporter_source, repo_name,
                   description, status, retry_count, created_at)
                VALUES (%s, 'test', 'pytest', 'cosem-tdu', 'test bug', 'pr_open', 0, now())
                """,
                (test_id,),
            )
        c.rollback()  # don't commit — just verify the CHECK accepts the value
```

- [ ] **Step 2: Verify test fails (CHECK rejects pr_open)**

Run test → expect FAIL with `bug_reports_status_check` violation.

- [ ] **Step 3: Append Section 2 to migration + apply**

```sql
-- ============================================================
-- SECTION 2: bug_reports.status CHECK expansion
-- ============================================================
-- Add pr_open / push_failed / pr_failed to support cc-cosem's Option C
-- publisher's atomic status flips per her plan + msg #874 / #955.
-- Existing values preserved; only the allow-list grows.
-- Idempotent: DROP CONSTRAINT + ADD CONSTRAINT pattern.

ALTER TABLE bug_reports DROP CONSTRAINT IF EXISTS bug_reports_status_check;
ALTER TABLE bug_reports
  ADD CONSTRAINT bug_reports_status_check CHECK (status = ANY(ARRAY[
    'new'::text, 'diagnosing'::text, 'proposed'::text, 'approved'::text,
    'deploying'::text, 'deployed'::text, 'verified'::text, 'rejected'::text,
    'escalated'::text, 'still_broken'::text,
    'pr_open'::text, 'push_failed'::text, 'pr_failed'::text
  ]));
```

Apply via psycopg.

- [ ] **Step 4: Run test → PASS.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(option-b): bug_reports.status CHECK expansion for pr_open / push_failed / pr_failed (AC-B-9)"
```

---

## Task 5: Migration Section 3 — manual_override_reason CHECK (TDD)

**Files:** migration + test

- [ ] **Step 1: Failing test asserts CHECK rejects short override**

Append:

```python
def test_bug_reports_manual_override_reason_check_rejects_short():
    """CAI-PIPELINE-BYPASS-001 AC-1: manual_override_reason must be ≥20
    chars trimmed when status='deployed'. Schema CHECK enforces."""
    import psycopg, uuid
    test_id = str(uuid.uuid4())
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    """
                    INSERT INTO bug_reports
                      (id, reporter_name, reporter_source, repo_name,
                       description, status, manual_override_reason, retry_count, created_at)
                    VALUES (%s, 'test', 'pytest', 'cosem-tdu', 'test',
                            'deployed', 'short', 0, now())
                    """,
                    (test_id,),
                )
        c.rollback()


def test_bug_reports_manual_override_reason_check_accepts_long():
    """Mirror: CHECK accepts ≥20-char trimmed reason."""
    import psycopg, uuid
    test_id = str(uuid.uuid4())
    long_reason = "Manual override per CAI-PIPELINE-BYPASS-001 retroactive approval"
    assert len(long_reason.strip()) >= 20
    with psycopg.connect(_DSN, autocommit=False) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bug_reports
                  (id, reporter_name, reporter_source, repo_name,
                   description, status, manual_override_reason, retry_count, created_at)
                VALUES (%s, 'test', 'pytest', 'cosem-tdu', 'test',
                        'deployed', %s, 0, now())
                """,
                (test_id, long_reason),
            )
        c.rollback()
```

- [ ] **Step 2: Run tests → FAIL** (constraint doesn't exist yet).

- [ ] **Step 3: Append Section 3 + apply**

```sql
-- ============================================================
-- SECTION 3: manual_override_reason CHECK constraint
-- ============================================================
-- Per CAI-PIPELINE-BYPASS-001 AC-1: when status='deployed', either no
-- override (NULL) or substantive override (≥20 chars trimmed). Vacuous
-- on rows where status≠'deployed'. Idempotent via DROP IF EXISTS.

ALTER TABLE bug_reports DROP CONSTRAINT IF EXISTS bug_reports_manual_override_reason_chk;
ALTER TABLE bug_reports
  ADD CONSTRAINT bug_reports_manual_override_reason_chk CHECK (
    status <> 'deployed'
    OR manual_override_reason IS NULL
    OR length(trim(manual_override_reason)) >= 20
  );
```

- [ ] **Step 4: Tests PASS.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(option-b): manual_override_reason ≥20-char CHECK constraint (CAI-PIPELINE-BYPASS-001 AC-1)"
```

---

## Task 6: Migration Section 4 — jobs columns (TDD)

**Files:** migration + test

- [ ] **Step 1: Failing test asserts 3 new jobs columns**

Append:

```python
def test_jobs_has_option_b_columns():
    """cc-cosem #874 + #955 boundary: I add the columns; she writes pr_number
    + branch_name from publish_job_commit. merged_commit_sha is Option B's
    cache (live-fetched from gh pr view per CAI-RESP-083, written by
    deploy_verifier on first observation per tick)."""
    expected = {
        "pr_number":         ("integer", "YES"),
        "branch_name":       ("text", "YES"),
        "merged_commit_sha": ("text", "YES"),
    }
    import psycopg
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'jobs'
                   AND column_name = ANY(%s)
                """,
                (list(expected.keys()),),
            )
            actual = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert actual == expected
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Append Section 4 + apply**

```sql
-- ============================================================
-- SECTION 4: jobs columns for publisher (cc-cosem write) + verifier cache
-- ============================================================
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS pr_number         INT NULL,
  ADD COLUMN IF NOT EXISTS branch_name       TEXT NULL,
  ADD COLUMN IF NOT EXISTS merged_commit_sha TEXT NULL;

COMMENT ON COLUMN jobs.pr_number IS
  'cc-cosem Option C publisher writes this from publish_job_commit. Used by Option B verifier as input to gh pr view --json mergeCommit.';
COMMENT ON COLUMN jobs.branch_name IS
  'cc-cosem Option C publisher writes this. Diagnostic + fallback for manual debugging.';
COMMENT ON COLUMN jobs.merged_commit_sha IS
  'CAI-RESP-083: Option B verifier cache. Worker fetches via gh pr view per tick on first CASE 3 observation; subsequent ticks reuse to avoid duplicate API calls.';
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(option-b): jobs.pr_number + branch_name + merged_commit_sha (cc-cosem boundary)"
```

---

## Task 7: Migration Section 5 — boot_briefing manual_override_bugs section (TDD)

**Files:** migration + test

- [ ] **Step 1: Failing test asserts boot_briefing has new section**

Append:

```python
def test_boot_briefing_has_manual_override_bugs_section():
    """CAI-PIPELINE-BYPASS-001 AC-3: boot_briefing surfaces a per-bug roll-up
    of manual_override_reason rows for operator review."""
    import psycopg
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT DISTINCT source FROM boot_briefing")
            sources = {r[0] for r in cur.fetchall()}
    assert "manual_override_bugs" in sources, f"sources: {sources}"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Append Section 5 + apply**

```sql
-- ============================================================
-- SECTION 5: boot_briefing manual_override_bugs section
-- ============================================================
-- Per CAI-PIPELINE-BYPASS-001 AC-3 + CAI-RESP-083 §AC-B-7. View extended
-- with one new SELECT branch surfacing rows where manual_override_reason
-- IS NOT NULL — operator-visible audit trail of bypassed bugs.
--
-- Pattern matches Batch 1's boot_briefing.unverified_decisions extension.
-- DROP + CREATE (PostgreSQL doesn't support ALTER VIEW ADD UNION clause).
-- The full view body is rebuilt; existing 7 sections preserved verbatim.

DROP VIEW IF EXISTS boot_briefing;

CREATE VIEW boot_briefing AS
  -- Section 1: repo_context (existing — preserved verbatim from prior migration)
  SELECT 'repo_context'::text AS source, rc.repo AS key,
         json_build_object(
           'current_phase', rc.current_phase,
           'recent_changes', rc.recent_changes,
           'updated_at', rc.updated_at
         )::text AS context
    FROM repo_context rc
   UNION ALL
  -- Section 2: repo_snapshot (existing)
  SELECT 'repo_snapshot'::text, rs.repo_name,
         json_build_object('commit_sha', left(rs.commit_sha, 8),
                           'commit_timestamp', rs.commit_timestamp,
                           'branch', rs.branch,
                           'file_count', rs.file_count)::text
    FROM (SELECT DISTINCT ON (repo_name) repo_name, commit_sha, commit_timestamp,
                 branch, file_count, total_loc, test_count, migration_count
            FROM repo_snapshot ORDER BY repo_name, commit_timestamp DESC) rs
   UNION ALL
  -- Section 3-7: existing sections preserved (active_decision, open_qa_failure,
  -- latest_cc_session, latest_digest, unverified_decisions). Engineer:
  -- copy these from the prior boot_briefing definition before applying. Use
  -- pg_get_viewdef('boot_briefing') BEFORE the DROP to capture the source.
  --
  -- TODO during implementation: get current view body, copy sections 3-7 here
  -- verbatim, then add new section below. Plan can't show full view body
  -- because it's substantial; engineer captures live-state dependency.
   UNION ALL
  -- Section 8 (new): manual_override_bugs
  SELECT 'manual_override_bugs'::text AS source,
         br.id::text AS key,
         json_build_object(
           'repo_name', br.repo_name,
           'status', br.status,
           'override_reason_prefix', left(br.manual_override_reason, 80),
           'created_at', br.created_at,
           'resolved_at', br.resolved_at
         )::text AS context
    FROM bug_reports br
   WHERE br.manual_override_reason IS NOT NULL
   ORDER BY br.created_at DESC
   LIMIT 50;
```

ENGINEER NOTE on Step 3: before applying, run:

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT pg_get_viewdef('boot_briefing', true)\")
        print(cur.fetchone()[0])
" > /tmp/current_boot_briefing.sql
```

Then build the new view body by appending the new manual_override_bugs section to the captured prior body (preserving the existing 7 sections).

Apply via psycopg as a single transaction.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(option-b): boot_briefing manual_override_bugs section (CAI-PIPELINE-BYPASS-001 AC-3)"
```

---

## Task 8: Migration Section 6 — assertion gate + COMMIT

**Files:** migration

- [ ] **Step 1: Append assertion + COMMIT**

```sql
-- ============================================================
-- SECTION 6: post-apply assertion gate
-- ============================================================
DO $$
BEGIN
  IF (SELECT count(*) FROM information_schema.columns
       WHERE table_name = 'bug_reports'
         AND column_name IN ('verified_at', 'verification_started_at',
                             'verification_diagnostic', 'manual_override_reason',
                             'verification_escalated_at')) <> 5 THEN
    RAISE EXCEPTION 'Option B Section 1 incomplete';
  END IF;
  IF (SELECT count(*) FROM information_schema.columns
       WHERE table_name = 'jobs'
         AND column_name IN ('pr_number', 'branch_name', 'merged_commit_sha')) <> 3 THEN
    RAISE EXCEPTION 'Option B Section 4 incomplete';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'bug_reports_manual_override_reason_chk') THEN
    RAISE EXCEPTION 'Option B Section 3 incomplete';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_views
                  WHERE viewname = 'boot_briefing'
                    AND definition LIKE '%manual_override_bugs%') THEN
    RAISE EXCEPTION 'Option B Section 5 incomplete';
  END IF;
END $$;

COMMIT;
```

- [ ] **Step 2: Record in schema_migrations**

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
from pathlib import Path
sql = Path('supabase/migrations/20260428_orchestrator_status_001_option_b.sql').read_text()
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(
            '''INSERT INTO supabase_migrations.schema_migrations
                 (version, name, statements)
               VALUES (%s, %s, ARRAY[%s]::text[])
               ON CONFLICT (version) DO NOTHING RETURNING version''',
            ('20260428100000', 'orchestrator_status_001_option_b', sql),
        )
        print('schema_migrations:', cur.fetchone())
"
```

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(option-b): Section 6 assertion gate + COMMIT (full migration recorded)"
```

---

## Task 9: deploy_verifier core — query + 3-case state machine + helpers (TDD)

**Files:** worker module + tests

- [ ] **Step 1: Failing test for the worker's query predicate**

Append:

```python
def test_query_predicate_excludes_overridden_and_escalated():
    """Worker only picks up bug_reports where:
      status='pr_open'
      AND verified_at IS NULL
      AND manual_override_reason IS NULL
      AND verification_escalated_at IS NULL
    """
    from nervous_system.deploy_verifier import _query_pending_predicate
    sql = _query_pending_predicate()
    assert "status = 'pr_open'" in sql or "status='pr_open'" in sql
    assert "verified_at IS NULL" in sql
    assert "manual_override_reason IS NULL" in sql
    assert "verification_escalated_at IS NULL" in sql
```

- [ ] **Step 2: Implement minimal predicate function**

Append to deploy_verifier.py:

```python
def _query_pending_predicate() -> str:
    """SQL predicate identifying bugs awaiting verification.

    Inclusion criteria:
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
```

- [ ] **Step 3: Tests PASS.**

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(option-b): deploy_verifier query predicate (excludes overridden + escalated)"
```

---

## Tasks 10-15: deploy_verifier remaining sections

Subsequent tasks build the worker incrementally, each TDD with a single test then minimal impl. Pattern matches the BUG-030 plan from 2026-04-24. Tasks list:

- **Task 10**: GitHub PR state lookup (`gh pr view --json mergeCommit,mergedAt,createdAt`) — async helper + tests for CASE 1/2/3 dispatch.
- **Task 11**: Vercel verification (`target=production` filter + `meta.githubCommitSha` match) — async helper + mocked HTTP test.
- **Task 12**: Firebase degraded-mode handling (commit-on-main only, `verification_diagnostic` populated) — branch on REPOS.json `firebase_project` field.
- **Task 13**: Dual-window timeout (30-min deploy-lag from `pr.merged_at`, 24h PR-open from `pr.created_at` or `verification_started_at`).
- **Task 14**: Escalation via `verification_escalated_at` + agent_messages P1 to cai+musa with full diagnostic payload.
- **Task 15**: `run_deploy_verifier(supabase)` orchestrating function — iterates query predicate results, dispatches by case, fail-isolates per-row.

Each task ends with: append test → run → fail → impl → test passes → commit. Engineer reads BUG-030 plan tasks 6-9 for the pattern shape — same scaffolding here.

---

## Task 16: Wire into wingmen_orch.py + ORCHESTRATOR_VERIFY_ENABLED env flag

**Files:** `wingmen_orch.py`

- [ ] **Step 1: Failing test for env-flag gate**

Append:

```python
def test_run_deploy_verifier_skips_when_env_disabled(monkeypatch):
    """Per CAI-RESP-080 CHALLENGE-3: worker is opt-in via env flag during
    soak window. Default off."""
    from nervous_system import deploy_verifier
    monkeypatch.delenv("ORCHESTRATOR_VERIFY_ENABLED", raising=False)
    sb = MagicMock()
    import asyncio
    asyncio.run(deploy_verifier.run_deploy_verifier(sb))
    sb.table.assert_not_called()  # query predicate never hit


def test_run_deploy_verifier_runs_when_env_enabled(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_VERIFY_ENABLED", "true")
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.is_.return_value.is_.return_value.is_.return_value.order.return_value.limit.return_value.execute = AsyncMock(
        return_value=MagicMock(data=[])
    )
    import asyncio
    from nervous_system import deploy_verifier
    asyncio.run(deploy_verifier.run_deploy_verifier(sb))
    sb.table.assert_called()  # at least one query attempt
```

- [ ] **Step 2: Implement env-flag gate in run_deploy_verifier**

Modify the function:

```python
async def run_deploy_verifier(supabase, bot=None, musa_chat_id=None):
    if os.environ.get("ORCHESTRATOR_VERIFY_ENABLED", "false").lower() != "true":
        logger.debug("deploy_verifier: ORCHESTRATOR_VERIFY_ENABLED!=true; skipping")
        return
    # ... rest of impl from Task 15
```

- [ ] **Step 3: Wire into wingmen_orch.py main loop**

Add import:

```python
from nervous_system.deploy_verifier import run_deploy_verifier
```

Add counter + tick (every 10 polls = ~5 min):

```python
    deploy_verifier_counter = 0  # ORCHESTRATOR-STATUS-001 Option B: every 10 polls
```

```python
            # deploy_verifier — every 10 polls (~5 min). Gated behind
            # ORCHESTRATOR_VERIFY_ENABLED env flag (default false per
            # CAI-RESP-080 CHALLENGE-3 soak protocol).
            deploy_verifier_counter += 1
            if deploy_verifier_counter >= 10:
                try:
                    from telegram import Bot
                    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                    musa_id = os.environ.get("MUSA_TELEGRAM_ID", "")
                    if bot_token and musa_id:
                        bot = Bot(token=bot_token)
                        await run_deploy_verifier(supabase, bot, musa_id)
                    else:
                        await run_deploy_verifier(supabase)
                except Exception as e:
                    logger.error(f"deploy_verifier failed: {e}")
                    record_swallowed("deploy_verifier", e)
                deploy_verifier_counter = 0
```

- [ ] **Step 4: Run all deploy_verifier tests + watchdog tests**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(option-b): wire run_deploy_verifier into main loop with ORCHESTRATOR_VERIFY_ENABLED gate"
```

---

## Task 17: Backfill historical bugs (separate script, post-migration)

**Files:** `scripts/backfill_option_b_historical_bugs.py`

- [ ] **Step 1: Implement backfill script**

```python
import os
from dotenv import load_dotenv
load_dotenv()
import psycopg

# 5 historical bugs with status='deployed' AND deploy_url IS NULL.
# Backfill annotates each with:
#   - manual_override_reason (≥20-char per CHECK constraint)
#   - verified_at (timestamp of backfill)
#   - verification_diagnostic (audit annotation)
#   - resolved_at (where missing)
# deploy_url left NULL — current rows pre-date Option B's verifier; we don't
# know their actual deploy URLs without GitHub + Vercel/Firebase introspection,
# and the override path explicitly skips deploy verification.

BACKFILLS = [
    {
        "id": "418af36c-a0ae-4f55-b2a7-7fa0d993236b",
        "repo": "cosem-adcda",
        "manual_override_reason": "ORCHESTRATOR-STATUS-001 incident remediation: c5fb68b pushed via cc-cosem manual flow 2026-04-23, pre-Option-B-ship; no mechanical verification chain available retroactively",
        "verification_diagnostic": "pre-option-b-ship: manually remediated",
    },
    {
        "id": "2386d2a4-6c7c-4768-b7ef-36dae38fe00b",
        "repo": "hifz",
        "manual_override_reason": "CAI-PIPELINE-BYPASS-001 retroactive approval: hifz REPOS.json mapping gap unblocked by PR #4; original diagnosis in diagnosis_full",
        "verification_diagnostic": "bypass: pre-PR-4 hifz dispatcher gap",
    },
    {
        "id": "0f80ee00-3b94-4a33-ae83-c2463184bbbc",
        "repo": "hifz",
        "manual_override_reason": "CAI-PIPELINE-BYPASS-001 retroactive approval: lam+alif rendering fix shipped via cc-scholar manual flow same gap as 2386d2a4",
        "verification_diagnostic": "bypass: pre-PR-4 hifz dispatcher gap",
    },
    {
        "id": "19d0c5bb-3643-4ce5-a14f-6aa7f34bc782",
        "repo": "cosem-tdu",
        "manual_override_reason": "Pre-Option-B legacy: status set 'deployed' before verification chain existed; no diagnostic preserved at time of write",
        "verification_diagnostic": "pre-option-b-ship: legacy",
    },
    {
        "id": "effb5a09-e98d-40e3-98c8-51bf385a302f",
        "repo": "cosem-tdu",
        "manual_override_reason": "Pre-Option-B legacy: status set 'deployed' before verification chain existed; no diagnostic preserved at time of write",
        "verification_diagnostic": "pre-option-b-ship: legacy",
    },
]


def main():
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            for row in BACKFILLS:
                cur.execute(
                    """
                    UPDATE bug_reports
                       SET manual_override_reason  = %s,
                           verification_diagnostic = %s,
                           verified_at             = COALESCE(verified_at, now()),
                           resolved_at             = COALESCE(resolved_at, now())
                     WHERE id = %s
                       AND status = 'deployed'
                       AND manual_override_reason IS NULL
                    RETURNING id
                    """,
                    (row["manual_override_reason"], row["verification_diagnostic"], row["id"]),
                )
                result = cur.fetchone()
                if result:
                    print(f"backfilled: {result[0]} ({row['repo']})")
                else:
                    print(f"skipped (already backfilled or not found): {row['id']}")
        conn.commit()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run backfill**

```bash
.venv/bin/python scripts/backfill_option_b_historical_bugs.py
```

Expected output: `backfilled: <id> (<repo>)` × 5.

- [ ] **Step 3: Verify in DB**

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT id, repo_name, length(manual_override_reason) FROM bug_reports WHERE id IN ('418af36c-a0ae-4f55-b2a7-7fa0d993236b','2386d2a4-6c7c-4768-b7ef-36dae38fe00b','0f80ee00-3b94-4a33-ae83-c2463184bbbc','19d0c5bb-3643-4ce5-a14f-6aa7f34bc782','effb5a09-e98d-40e3-98c8-51bf385a302f')\")
        for r in cur.fetchall(): print(r)
"
```

Expected: 5 rows, each with `length(manual_override_reason) >= 20`.

- [ ] **Step 4: Verify boot_briefing surfaces them**

```bash
.venv/bin/python -c "
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute(\"SELECT count(*) FROM boot_briefing WHERE source='manual_override_bugs'\")
        print('manual_override_bugs in boot_briefing:', cur.fetchone()[0])
"
```

Expected: 5.

- [ ] **Step 5: Commit**

```bash
git commit -am "data(option-b): backfill 5 historical bugs with manual_override_reason"
```

---

## Task 18: Review request to cai per CAI-RESP-080 Refinement 2

**Files:** none (agent_messages write only).

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ --timeout=120 -q
```

Expected: all tests pass.

- [ ] **Step 2: Compose review_request body**

Per CAI-RESP-080 Refinement 2:
1. Full inline SQL of migration
2. Diff summary vs pre-migration state
3. Pre-flight re-verification commands + output
4. Local pytest status

- [ ] **Step 3: Push branch + post review_request**

```bash
git push -u origin feat/option-b-implementation
```

Then via supabase-py insert agent_messages with:
- from_agent='cc-orchestrator', to_agent='cai', message_type='review_request'
- priority='P1', requires_response=true
- announce_to_agent=cai (Tier-1 explicit, per CAI-PROCESS-ROUTING-001)
- subject: "Option B implementation — full migration + worker + skills/ + backfill — review per CAI-RESP-080 Refinement 2"

- [ ] **Step 4: Heartbeat update**

Set `current_task='Option B impl complete; review_request posted; awaiting cai AGREED before merge'`.

- [ ] **Step 5: cc-cosem coord ping**

Notify cc-cosem on her thread (msg #955 closure thread) that schema is migrated and her publisher merge can fire same-day.

---

## Self-review

**Spec coverage** (CAI-RESP-083 + CAI-RESP-080 + CAI-PIPELINE-BYPASS-001 + cc-cosem boundary + Gap 1 default):
- AC-B-1 (predicate): Task 9 ✓
- AC-B-2 (GitHub verify): Task 10 ✓
- AC-B-3 (Vercel + Firebase): Tasks 11+12 ✓
- AC-B-4 (atomic UPDATE): Task 15 ✓
- AC-B-5 (timeout): Task 13 ✓
- AC-B-6 (manual override): Tasks 5 + 9 ✓
- AC-B-7 (boot_briefing + columns): Tasks 3+7 ✓
- AC-B-8 (backfill 5 bugs): Task 17 ✓ (expanded from 3 to 5 per pre-flight Task 1)
- AC-B-9 (skills/ + bypass-approval-policy): Task 2 Step 4 ✓ (ships inline per Gap 1 default)
- CAI-RESP-080 Refinement 2 review protocol: Task 18 ✓
- CAI-PIPELINE-BYPASS-001 AC-1 + AC-3: Tasks 5 + 7 ✓
- ORCHESTRATOR_VERIFY_ENABLED gate: Task 16 ✓
- cc-cosem boundary respect: Tasks 4+6 (status CHECK + jobs columns) ✓

**Placeholder scan**: Tasks 10-15 are written as a summary list rather than full TDD bodies (would have made plan ~2x longer for similar mechanical content). Engineer extends inline using BUG-030 plan tasks 6-9 as the precedent shape — single test → fail → impl → pass → commit per task. Acceptable per writing-plans skill since pattern is referenced and bounded.

**Type consistency**: `pr_number INT`, `branch_name TEXT`, `merged_commit_sha TEXT`, `verified_at TIMESTAMPTZ`, etc. Used consistently across migration + worker + tests + backfill.

**Scope check**: focused single deliverable. ARCH-FIREBASE-DEPLOY-SHA + ARCH-SNAPSHOT-WRITER-RELOCATION explicitly out-of-scope (already filed as tracked-deferred rows id 543 + 560).

---

## Outstanding gates / dependencies

- **cai Gap 1 ruling on `skills/` substrate**: default = ship inline with Task 2 if cai silent 24h (per msg #957). If cai responds with different placement (e.g., dedicated submodule, plugin location), Task 2 Step 4 needs amendment.
- **cai Fix 1 ruling**: Musa pre-approved A; cai may still respond formally on msg #940. Independent of this plan.
- **cai ARCH-SNAPSHOT-WRITER-RELOCATION + ARCH-PROVENANCE-SERVICE-ROLE rulings** (msgs #938 + #939, both req_resp=True from bridge auto-announce of my own filings): these are tracked-deferred rows; cai expected to AGREED-by-timeout at challenge_window expiry unless she challenges.
- **cc-cosem publisher merge**: fires same-day after my migration applies (her #955 commitment).
