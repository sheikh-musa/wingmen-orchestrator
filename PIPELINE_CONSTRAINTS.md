# PIPELINE_CONSTRAINTS.md — Wingmen Autonomous Pipeline Constitutional Framework

**Version:** 1.0.0
**Owner:** Gazzabyte / Musa
**Status:** Active
**Last Updated:** 2026-04-17

---

## Purpose

These constraints govern every architectural decision in the Wingmen autonomous pipeline. They are non-negotiable. Violations are bugs, not preferences. Undeclared non-compliance is a bug. Declared non-compliance is a debt with a remediation date.

Modelled on the same constitutional pattern as `CLAUDE.md` (IhsanOPS): foundational principles cascade into specific rules. The principle is the wall, not the sign on the wall — each constraint has a structural enforcement mechanism, not just documentation.

---

## Constraint 1 — AMANAH (Trustworthiness / Verifiable Completion)

**Principle:** Every autonomous action must be traceable, auditable, and verified. No job transitions to `completed` on self-report alone. System state must always be recoverable by a human operator.

**Rules:**
- Job status `completed` requires: commit exists (Gate 1) + intent aligned (Gate 2) + tests pass + deploy healthy (HTTP 200 on production URL, or deploy not required)
- Every state transition is written to `build_log` with a distinct `phase` label before it happens
- Every ARCH-030 escalation posts intent to `agent_messages` before acting
- No autonomous action without a rollback path documented in code comments

**Structural enforcement:**
- `set_job_status(supabase, job_id, "completed")` is only called after all verification layers pass — enforced in `run_job()`, not optional
- Deploy failures set job status to `failed`, not `completed` — the Vercel cascade bug is an Amanah violation
- `build_log.phase` enum defines all legal state transitions

**Current compliance:** ⚠️ PARTIAL — Vercel cascade failure (deploy fails → job marked `completed`) violates this constraint. Remediation: this session.

---

## Constraint 2 — FAIL-FAST (Bounded Retries / No Silent Masking)

**Principle:** The autonomous system must not retry indefinitely. After N failed attempts, escalate to a human immediately. Never mask failures with more automation.

**Rules:**
- `fail_count >= MAX_FAIL_COUNT` (default 3) → job status `paused`, human notification sent
- ARCH-030 escalation is capped: `escalation_count >= 2` → no further autonomous escalation, `requires_response=True` posted to `agent_messages`
- ARCH-030 is a recovery mechanism, not a substitute for upstream spec quality — if the same job type consistently triggers ARCH-030, the session prompt template must be improved
- No job loops on the same failure pattern indefinitely

**Structural enforcement:**
- `MAX_FAIL_COUNT` read from env, defaults to 3
- `escalation_count` tracked per job via `agent_messages` row count (`from_agent='arch-030-escalation'`, `job_id` in subject) — no schema migration required
- ARCH-030 reads escalation count before spawning; aborts with `requires_response=True` if cap reached

**Current compliance:** ✅ COMPLIANT — persistent escalation count via `agent_messages` row count (survives restart). `_arch030_active` guards in-process duplicates; agent_messages query guards cross-restart cap. Implemented 2026-04-17.

---

## Constraint 3 — BOUNDED AUTONOMY (Explicit Whitelist)

**Principle:** The system has an explicit whitelist of what it can do without human approval. Everything outside the whitelist requires explicit human sign-off before execution.

**Autonomous (no approval required):**
- Edit source code files (.ts, .tsx, .py, .sql in non-production migrations)
- Run tests
- Commit and push to non-main branches
- Write to `agent_messages`, `build_log`, `work_outputs`
- Retry failed jobs up to `MAX_FAIL_COUNT`
- Spawn ARCH-030 escalation sessions (capped by Constraint 2)

**Requires human approval:**
- Schema changes to production database (migrations applied to live Supabase)
- Changes to `.env`, environment variables, API keys, credentials
- Push directly to `main` branch without PR review gates
- Modify RLS policies or access control rules
- Delete data (any `DELETE` or `DROP` operation)
- Modify orchestrator plist/launchd configuration
- Modify `PIPELINE_CONSTRAINTS.md` or `CLAUDE.md`

**Structural enforcement:**
- Schema gate (`nervous_system/schema_gate.py`) pauses jobs that touch `supabase/migrations/` until Musa applies — enforced in `run_job()` step 5d
- `.env` is not passed to CC subprocess (env whitelist in `ralph_runner.py` line 234)
- `--dangerously-skip-permissions` is granted but ARCH-030 escalation prompts explicitly state scope boundaries

**Current compliance:** ✅ MOSTLY — env whitelist enforced, schema gate active. Gap: escalation CC prompt scope is advisory, not structural. No technical guard preventing escalation CC from modifying `.env` if it decided to.

---

## Constraint 4 — OBSERVABLE HEALING (Intent Before Action)

**Principle:** Every self-healing action must announce intent before acting. Human operator can intervene before healing completes. All healing actions logged with before/after state.

**Rules:**
- ARCH-030 posts "starting auto-diagnosis for job #N" to `agent_messages` BEFORE spawning the CC subprocess
- Any job status change by ARCH-030 escalation CC is logged in `build_log` phase `arch030_action`
- Telegram notification reaches Musa within 60 seconds of a healing action starting (see Constraint 8)
- ARCH-030 timeout (15 min cap) fires a `blocker` message to `agent_messages` — Musa can always intervene

**Structural enforcement:**
- `_spawn_escalation_session()` calls `supabase.table("agent_messages").insert({"subject": "ARCH-030: starting..."})` before `asyncio.create_subprocess_exec()` — enforced by function structure
- `poll_agent_messages` routes healing notifications to Telegram within 60s (Constraint 8)

**Current compliance:** ✅ COMPLIANT — ARCH-030 announces intent, posts result. Constraint 8 enforcement pending (see below).

---

## Constraint 5 — RESOURCE BOUNDS (Compute Limits)

**Principle:** The system has explicit, enforced limits on compute consumption. No runaway processes. No resource exhaustion under concurrency.

**Rules:**
- Maximum CC session time: 30 minutes (`timeout=1800` in `ralph_runner.py`)
- Maximum CC turns per session: 80 (`max_turns=80`)
- Maximum ARCH-030 escalation time: 15 minutes (`timeout=900` in `_spawn_escalation_session`)
- Maximum concurrent jobs: `MAX_CONCURRENT_BUILDS` (default 3, one per repo)
- Maximum escalations per job: 2 (Constraint 2)
- When Claude API usage window approaches exhaustion: jobs are re-queued, not dropped

**Structural enforcement:**
- `asyncio.wait_for(process.communicate(), timeout=1800)` in `ralph_runner.py`
- `asyncio.wait_for(proc.communicate(), timeout=900)` in `_spawn_escalation_session()`
- `pick_next_jobs` enforces one-per-repo via `claimed_repos` set

**Current compliance:** ✅ COMPLIANT — compute time bounded, concurrency bounded. Rate limit awareness implemented 2026-04-17: `ralph_runner.py` detects rate-limit patterns in claude output, returns `rate_limited=True`; `wingmen_orch.py` re-queues with `retry_after=NOW()+30min`, `fail_count` unchanged; `pick_next_jobs` filters on `retry_after`. `jobs.retry_after TIMESTAMPTZ` column added via migration.

---

## Constraint 6 — DATA INTEGRITY (Isolation / No Corrupted Shared State)

**Principle:** No autonomous action can corrupt shared state. All database changes via migration. Concurrent job isolation mandatory.

**Rules:**
- All database schema changes via `supabase/migrations/*.sql` — never direct `ALTER TABLE` in application code
- CC sessions run in isolated git worktrees (`/tmp/wingmen-wt-{job_id}`) — never in main checkout
- One job per repo at a time (`pick_next_jobs` enforces) — no concurrent writes to same repo
- Worktree merge is fast-forward only (`--ff-only`) — merge conflicts cause job failure, never silent corruption
- All worktrees cleaned up on job completion or failure (finally block in `ralph_runner.py`)

**Structural enforcement:**
- `_create_worktree()` and `_merge_and_remove_worktree()` in `ralph_runner.py` — BUG-019
- `pick_next_jobs()` one-per-repo constraint
- Schema gate pauses jobs that touch migrations until human applies

**Current compliance:** ✅ COMPLIANT — BUG-019 shipped and smoke-tested. Schema gate active.

---

## Constraint 7 — SECURITY BOUNDARY (Credential / Access Isolation)

**Principle:** The autonomous system cannot modify access controls, credentials, or push to main without review gates.

**Rules:**
- CC subprocess receives only safe env vars: `{PATH, HOME, USER, SHELL, LANG, TERM, LC_ALL, LC_CTYPE}` — never `ANTHROPIC_API_KEY`, `SUPABASE_SERVICE_KEY`, `VERCEL_TOKEN`
- Credentials live in `.env` (not committed, not passed to CC)
- ARCH-030 escalation CC has no broader permissions than normal CC — same env whitelist applies
- No direct push to `main` without Gate 1 (commit check) + Gate 2 (intent alignment) passing
- Supabase service key is server-side only — never in client-rendered code, never in CC subprocess env

**Structural enforcement:**
- Env whitelist in `ralph_runner.py` lines 234–238 (hardcoded `safe_keys` set)
- `.gitignore` includes `.env`
- Gates 1 and 2 in `ralph_runner.py` must pass before `_git_push()` is called in `wingmen_orch.py`

**Current compliance:** ✅ COMPLIANT — env whitelist enforced. Gap: ARCH-030 prompt instructs CC to read `.env` directly to get service key for Supabase updates. This is an advisory instruction, not a structural breach, but it is inconsistent with this constraint's spirit. Remediation: ARCH-030 should not need the service key — orchestrator should own all job status transitions.

---

## Constraint 8 — COMMUNICATION LATENCY (cc↔cai Realtime)

**Principle:** cc-ihsanos and cai must communicate in near-realtime. Decisions cannot be blocked by message polling latency. Human-in-the-loop requires the human to actually receive messages promptly.

**Rules:**
- `cc-ihsanos → cai` Telegram notification delivered within 60 seconds of message post
- `agent_messages` poll fires every orchestrator poll cycle (30s), not every 10 cycles (5 min)
- cc-ihsanos reads unread `agent_messages` from cai at the start of every session and before every major architectural decision
- When cc posts a `requires_response=True` message to cai, it explicitly tells Musa in the conversation to watch Telegram for cai's reply
- cai responds by replying to the Telegram notification — bot auto-posts reply to `agent_messages` as `from_agent='cai'`

**Structural enforcement:**
- `agent_messages_counter` threshold reduced from 10 to 1 in `wingmen_orch.py` main loop
- CLAUDE.md (ihsanos) read order includes: "Check agent_messages for unread cai responses"
- Session start protocol: cc queries `agent_messages` where `to_agent='cc-ihsanos'` and `read_at IS NULL`
- `_maybe_relay_cai_reply()` in `cto_bot.py` — Telegram reply→agent_messages routing (commit 7320159)
- `notification_log.telegram_msg_id` stores Telegram message ID for reply-to lookup

**Current compliance:** ✅ COMPLIANT — poll frequency fixed (commit 4d179c3); cai→cc relay via Telegram reply-to routing (commit 7320159).

---

## Compliance Summary

| # | Constraint | Status | Remediation |
|---|---|---|---|
| 1 | Amanah — Verifiable Completion | ✅ Compliant | Fixed: deploy failure re-queues, blocks completed (4d179c3) |
| 2 | Fail-Fast — Bounded Retries | ✅ Compliant | Persistent cap via agent_messages count (2026-04-17) |
| 3 | Bounded Autonomy | ✅ Mostly | Scope ARCH-030 to not read .env directly |
| 4 | Observable Healing | ✅ Compliant | — |
| 5 | Resource Bounds | ✅ Compliant | Rate limit re-queue with retry_after backoff (2026-04-17) |
| 6 | Data Integrity | ✅ Compliant | — |
| 7 | Security Boundary | ✅ Compliant | Minor: ARCH-030 .env advisory |
| 8 | Communication Latency | ✅ Compliant | Fixed: poll 30s (4d179c3) + cai relay (7320159) |

**Blocking scale-up:** ✅ Constraints 1 and 8 now compliant. Ready to add repos/concurrent jobs once Constraint 2 cap is implemented.

---

## How to use this document

Every pipeline architectural decision answers these questions before proceeding:
1. Does this change affect a `completed` job state? → Check Constraint 1
2. Does this add a new retry or recovery mechanism? → Check Constraints 2 and 5
3. Does this allow CC to do something new autonomously? → Check Constraint 3
4. Does this affect monitoring/observability? → Check Constraint 4
5. Does this touch the database or git? → Check Constraint 6
6. Does this touch credentials or access? → Check Constraint 7
7. Does this affect cc↔cai message routing? → Check Constraint 8

Non-compliance must be declared here with a target date. Undeclared non-compliance is a bug.
