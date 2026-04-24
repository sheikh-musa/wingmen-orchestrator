# ORCHESTRATOR-STATUS-001 Option B — Verification Worker Design

**Author:** cc-orchestrator (msg #634 scope inherited from cc-ihsanos via CAI-AGENTS-002)
**Date:** 2026-04-24
**Parent decisions:** ORCHESTRATOR-STATUS-001 (P1, 2026-04-23), CAI-PIPELINE-BYPASS-001 (folds in)
**Status:** draft, raised to cai for adversarial review per CAI-RESP-080 Refinement 2 protocol

---

## 1. Goal

Stop `bug_reports.status='deployed'` from lying. The label must mean "commit landed on origin/main AND the deploy platform is serving that commit." Agent-discipline (Option C, cc-cosem scope, partially shipped on `feat/orchestrator-status-001-publisher`) handles push/PR. This worker is the independent verification layer that catches everything C misses (transient git failures, worktree isolation, permission drift, branch collisions, deploy anomalies).

## 2. Scope (ACs composed from both parent decisions)

- **AC-B-1** Worker polls `bug_reports` with `status='completed'` AND `manual_override_reason IS NULL` AND `verified_at IS NULL`. Oldest first. Cadence: 5 min.
- **AC-B-2** For each row, verify commit SHA on origin/main via GitHub API. Use `gh api repos/{owner}/{repo}/compare/{sha}...{default_branch}` and treat `status ∈ {"identical", "ahead"}` as on-main.
- **AC-B-3** For each row on-main, verify deploy platform. Per-platform strategy:
  - **Vercel (ihsanos, hifz-companion, dookana, etc):** `GET /v6/deployments?projectId=...&state=READY&limit=5`, match `meta.githubCommitSha` against target SHA.
  - **Firebase (cosem-tdu, cosem-adcda):** **degraded mode** — confirm origin/main is sufficient to flip to 'deployed'. Record `verification_diagnostic='firebase-degraded: commit on origin/main, deploy platform not independently verified'`. ARCH-follow-up to embed SHA in Firebase build metadata.
- **AC-B-4** On BOTH signals confirmed (or GitHub-only for Firebase repos): atomic UPDATE — `status='deployed'`, `deploy_url` populated from REPOS.json or Vercel API response, `resolved_at=now()`, `verified_at=now()`. CHECK constraint from CAI-PIPELINE-BYPASS-001 is vacuously satisfied (`manual_override_reason IS NULL`).
- **AC-B-5** Timeout: 30 min from `bug_reports.created_at`'s-nearest-completed-transition (stored in `verification_started_at` — new column). On timeout: post P1 `agent_messages` to cai + musa with `bug_id`, target commit SHA, last-seen main HEAD, last deploy-query result, and elapsed time. `requires_response=true`. Do NOT auto-flip to `failed`. Stop further retries on this bug until operator intervention.
- **AC-B-6** Bypass path: `manual_override_reason` populated → worker skips. Schema per CAI-PIPELINE-BYPASS-001: `ALTER TABLE bug_reports ADD COLUMN manual_override_reason TEXT NULL; ALTER TABLE bug_reports ADD CONSTRAINT bug_reports_status_manual_override_chk CHECK (status <> 'deployed' OR manual_override_reason IS NULL OR length(trim(manual_override_reason)) >= 20);`
- **AC-B-7** `boot_briefing` view extends with `manual_override_bugs` section — count + 3-char prefixes of recent overrides, per the multi-section pattern.
- **AC-B-8** Backfill incidents at migration time:
  - Bug `418af36c` (cosem-adcda trainer-self-onboarding): set `deploy_url='https://tdu-tools-prod.web.app'`, `verified_at=now()`, `verification_diagnostic='manual remediation via c5fb68b push 2026-04-23 per ORCHESTRATOR-STATUS-001 incident'`.
  - Bug `2386d2a4` (hifz report-button-blocks-playback): set `manual_override_reason='CAI-PIPELINE-BYPASS-001 retroactive approval: REPOS.json hifz-mapping gap unblocked by PR #4. Original diagnosis in diagnosis_full.'` (length ≥ 20 ✓).
  - Bug `0f80ee00` (hifz lam+alif rendering): same override pattern if it also has `status='deployed'` with unverifiable deploy chain. Resolve per cc-scholar msg #815 context.
- **AC-B-9** Canonical governance directive ships under `skills/` (per CAI-PIPELINE-BYPASS-001 AC-5) documenting the bypass approval protocol for consumption-via-transclusion by all CC families. Scope-minimal for this ship: single directive file; broader skills/ directory build-out is separate.

## 3. Architecture

### 3.1 Runtime

**Decision: Python asyncio worker, new module `nervous_system/deploy_verifier.py`.**

Why not pg_cron: Supabase doesn't have pg_http available; the worker needs outbound HTTP to GitHub + Vercel APIs. pg_cron in this stack schedules pg-side SQL only.

Why `nervous_system/`: parallel to `agent_messages_poll.py` (also a polling worker). Kept out of `agents/` because `agents/` is autonomous-fix agent modules (diagnostic, fixer, publisher). Verification is platform infrastructure, not per-bug reasoning.

Lifecycle: launched from `wingmen_orch.py` main loop as an asyncio task alongside existing pollers. Single-instance via launchd managed by the existing orchestrator process (no new daemon).

### 3.2 State machine (per bug_reports row)

```
completed + verified_at=NULL + manual_override_reason=NULL
  |
  ↓ tick (every 5 min)
  |
  ├─ not on main → stay; retry next tick
  ├─ on main + Vercel deploy confirmed → flip to deployed (atomic UPDATE)
  ├─ on main + Firebase repo → flip to deployed (degraded verification recorded)
  ├─ elapsed > 30 min → post P1 agent_message; stop tick for this bug
  └─ operator populates manual_override_reason → next tick skips (query predicate)
```

### 3.3 New columns on `bug_reports`

- `verified_at TIMESTAMPTZ NULL` — success timestamp, populated by worker.
- `verification_started_at TIMESTAMPTZ NULL` — set on first tick, used for 30-min timeout math. (Alternative: derive from status-transition audit — rejected, too heavy for this gate.)
- `verification_diagnostic TEXT NULL` — last-known state when worker escalates or records degraded verification.
- `manual_override_reason TEXT NULL` — per CAI-PIPELINE-BYPASS-001 AC-1.

`deploy_url` already exists; `resolved_at` already exists.

### 3.4 Check constraint

Per CAI-PIPELINE-BYPASS-001:
```sql
ALTER TABLE bug_reports
  ADD CONSTRAINT bug_reports_status_manual_override_chk
  CHECK (
    status <> 'deployed'
    OR manual_override_reason IS NULL
    OR length(trim(manual_override_reason)) >= 20
  );
```

Reading: when `status='deployed'`, either `manual_override_reason IS NULL` (normal verified path) or the reason is substantive (≥ 20 trimmed chars — enough to be a human-readable justification, not a stub).

### 3.5 Query filter (worker's polling loop)

```sql
SELECT id, repo_name, job_id, ...
  FROM bug_reports
 WHERE status = 'completed'
   AND verified_at IS NULL
   AND manual_override_reason IS NULL
 ORDER BY created_at ASC
 LIMIT 20;  -- per-tick batch cap; stops runaway API calls if queue balloons
```

Per-bug: set `verification_started_at` if NULL on first contact.

### 3.6 GitHub verification detail

```
gh api repos/<owner>/<repo>/compare/<target_sha>...<default_branch>
```

JSON response `.status`:
- `"identical"` → target IS main HEAD → verified
- `"ahead"` → main is ahead of target (target IS an ancestor of main) → verified
- `"behind"` → main is behind target (target NOT on main, fork-style) → not yet
- `"diverged"` → shared base only (target NOT on main) → not yet

Default branch: from REPOS.json (default `main` if unspecified). Owner/repo: parsed from `REPOS.json[repo].github`.

Commit SHA source: `jobs.last_commit_sha` (populated by Option C's `ralph_runner.publish_job_commit`) — OR if this column doesn't exist or is NULL, skip this bug with diagnostic `no-commit-sha-on-job`.

### 3.7 Vercel verification detail

```
GET https://api.vercel.com/v6/deployments?projectId=<vercel_project>&state=READY&limit=5
Authorization: Bearer <VERCEL_TOKEN>
Team context: whichever team the project belongs to — passed via teamId query param
```

Each deployment has `meta.githubCommitSha`. Match against target SHA. On match: populate `deploy_url` from `url` field (with `https://`).

Env vars: `VERCEL_TOKEN` + `VERCEL_TEAM_ID`. Both present in current `.env` (verified). Worker fails loud at startup on missing vars — no silent degraded-mode fallback for Vercel repos.

**deploy_url precedence on success:**
- Vercel: use the Vercel API response's `url` field (actual deployment hostname including `https://`), fall back to REPOS.json `deploy_url` only if Vercel response is missing it (shouldn't happen for READY state).
- Firebase (degraded): use REPOS.json `deploy_url` directly.

### 3.8 Failure / escalation path

On 30-min timeout, INSERT `agent_messages`:
- `from_agent='cc-orchestrator'`, `to_agent='cai'`, `priority='P1'`, `requires_response=True`
- Subject: `Deploy verification timeout — bug <short_id> stuck in completed for 30+ min`
- Body: bug_id, repo_name, target commit_sha, last GitHub response, last deploy-query response, elapsed minutes, link to `bug_reports` row
- `manual_override_reason` remains NULL (operator sets it if they approve a bypass, which clears the bug from the worker's queue)

Parallel Telegram routing via existing notifier — the P1 priority picks it up.

### 3.9 Concurrency

Single worker instance managed by `wingmen_orch.py`. No advisory lock needed. If operator restarts orchestrator mid-tick, worst case is one duplicate tick after restart — idempotent (UPDATE WHERE verified_at IS NULL).

## 4. Open questions resolved (from ORCHESTRATOR-STATUS-001 Q1-Q4)

**Q1 (polling cadence):** 5 min. Tight enough for Vercel deploy latency (~30-90s), not too noisy for 300-row backlogs. 6 ticks per 30-min timeout window.

**Q2 (low-risk auto-merge for Option C):** Out of scope for this worker. cc-cosem's Option C plan already handles it via `AUTO_MERGE_LOW_RISK` env flag (default off). Worker doesn't care — it observes merge state, doesn't produce it.

**Q3 (escalation path if notifier broken):** Escalation uses the standard agent_messages P1 path — same path that NOTIFIER-FIX-001 affects. Not a blocker; if notifier is broken, that's a NOTIFIER-FIX-001 incident which produces its own visibility. Flagging: the long-tail Fix 1 (notifier DLQ + repair) should ship before this worker hits heavy use, otherwise a timeout-without-delivery is invisible. Ordering: Fix 1 P2 → this worker → Fix 3/5 remaining P2.

**Q4 (deploy_url column):** Exists on `bug_reports` today. Worker writes it on success.

## 5. Scope boundaries

**In:** worker + schema additions (4 new columns + 1 CHECK) + backfill of 3 historical bugs + `boot_briefing` manual_override_bugs section + single skills/ directive for the bypass policy.

**Out (explicit):**
- Option A finer state model (per ORCHESTRATOR-STATUS-001, deferred until >50 bugs/month or 4+ repos).
- Webhook infrastructure (GitHub push events, deploy platform callbacks) — polling is sufficient until scale changes.
- pg_cron-based worker alternative — not available in Supabase without extensions.
- Firebase-deploy SHA embedding — ARCH-follow-up.
- cc-cosem's Option C push-contract implementation — already in-flight on `feat/orchestrator-status-001-publisher`.
- Changes to `jobs.status` enum — Option C scope per the parent decision.
- Broader skills/ directory build-out — this spec ships one directive file; the CAI-SKILLS-001 initiative is separate.

## 6. Testing strategy

- **Unit tests** on the verifier state machine with mocked `gh api` + Vercel HTTP responses. Cover: on-main no-deploy, on-main + deploy-match, on-main + deploy-mismatch, not-on-main, timeout path, manual_override_reason skip, Firebase degraded mode.
- **Integration test** against a test `bug_reports` row with `status='completed'` + real GitHub/Vercel APIs against a known-deployed commit (e.g. PR #2 or #3 post-merge). Verifies end-to-end flip.
- **Live smoke** post-deploy: next autonomous-fix bug through the pipeline. cc-cosem + cc-scholar cooperate.
- **Schema tests** for the CHECK constraint (rejects `status='deployed'` + 5-char `manual_override_reason`, accepts `status='deployed'` + 25-char reason, etc).

## 7. Risks / limitations

1. **Firebase degraded mode**: commit-on-main is a weaker signal than SHA-on-live-deploy. Mitigated by recording `verification_diagnostic`; accepted because SHA-embedding is a bigger platform change.
2. **GitHub + Vercel rate limits**: at 20 bugs × 2 API calls every 5 min = ~1500 req/hour. GitHub unauthenticated is 60/hr; authenticated is 5000/hr; worker uses existing `gh` auth → fine. Vercel is 1000/hr per token → fine.
3. **Token leak surface**: worker reads `VERCEL_TOKEN` from `.env`. No new secret exposure beyond what already exists.
4. **30-min timeout is calendar-time, not work-time**: if orchestrator restarts mid-window and re-polls immediately, the timeout math uses `verification_started_at` (persistent) so no reset. Good.

## 8. Ship order (post-AGREED)

1. Implementation plan via superpowers:writing-plans skill.
2. review_request per CAI-RESP-080 Refinement 2 on the plan.
3. Post-AGREED: migration + worker module + tests, incremental TDD.
4. Backfill the 3 historical bugs in the same migration transaction.
5. Enable worker in `wingmen_orch.py` main loop.
6. Live smoke on first autonomous-fix bug post-deploy.
7. SHIPPED message + STATUS.md + PR.

---

## Questions raised to cai (for this review)

1. **Firebase degraded mode acceptable?** Or do you want SHA-embedding in Firebase builds as part of this ship (expands scope significantly, needs cc-cosem coordination)?

2. **5-min cadence vs configurable?** Open to making it a `orchestrator_runtime_config` setting like `challenge_enforcer_mode` — would keep shape consistent with Batch 1. Worth the surface area or YAGNI?

3. **`verification_started_at` as a new column vs derived?** I chose the column for clarity + cheap timeout math. Alternative: derive from `agent_status_history` or similar audit log. Simpler to add the column; open to push back if you see drift risk.

4. **Canonical directive file location**: `skills/bypass-approval-directive.md` or under `docs/governance/`? AGENTS.md handoff manifest said I own both surfaces. My instinct: `skills/` since it's meant for transclusion. Your call if there's a convention I'm missing.

5. **Order of ship vs NOTIFIER-FIX-001 Fix 1**: escalation-path concern in Q3. Should Fix 1 (notifier DLQ + repair) ship BEFORE Option B hits production use, or are we comfortable with Option B's P1 agent_messages relying on the current notifier's best-effort behavior? If BEFORE: re-sequence Fix 1 ahead of Option B. If OK-with-current: document the dependency in a follow-up.

Ready for adversarial review.
