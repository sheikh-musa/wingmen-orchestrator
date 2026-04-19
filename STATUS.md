# wingmen-orchestrator STATUS

Last Updated: 2026-04-19 SGT (BUG-025 shipped)
Build Status: green
Deploy: 0893d7a pushed (migration applied via dashboard SQL editor)

## Last Completed (2026-04-19 — BUG-025 acceptance-path announce trigger)

### BUG-025 — shipped
Plan: `docs/superpowers/plans/2026-04-19-bug-025-acceptance-path-trigger.md`
Spec: CAI-RESP-040 (B1 + A1 + A2 + concession on simpler announce-all-CAI variant)
Migration: `supabase/migrations/20260419_bug025_acceptance_path_announce.sql` (commit `0893d7a`, applied live via dashboard)

**Behaviour change vs BUG-020 (357a135):**
- Announceable status set widened: `'challenge_window'` → `('challenge_window', 'accepted')`
- Message shape branches on `challenge_status`:
  - `challenge_window` → `message_type='review_request'`, subject `"<ref>: <title> — for review + challenge"`, `requires_response=true` (BUG-020 preserved)
  - `accepted` → `message_type='decision'`, subject `"<ref>: <title>"`, `requires_response=false` (BUG-025 new)
- `OLD.challenge_status='challenge_window'` state-transition guard dropped — `announced_by_msg_id IS NOT NULL` is the universal dedup
- No SIMILAR TO regex on `decision_ref` — `source='claude_ai_session'` is the canonical "from CAI" signal (A1)

**Live verification (4-case matrix per CAI-RESP-040 A2):**
- CAI-TEST-001 (accepted path): announced as `decision` + `requires_response=false`, no challenge suffix → PASS
- CAI-TEST-002 (challenge_window regression): announced as `review_request` + challenge suffix preserved → PASS
- CAI-TEST-003 (bypass_review escape hatch): no announce, no notified_at → PASS
- CAI-TEST-004 (state-transition dedup): insert as challenge_window then UPDATE to accepted yielded exactly 1 announce, `announced_by_msg_id` unchanged → PASS
- All 4 synthetic `BUG-025-VERIFY-NNN` rows hard-deleted from `strategic_decisions` and `agent_messages` post-verify

**Schema NOT NULL discoveries (live testing, not in plan):**
- `strategic_decisions` requires `decision`, `reasoning`, `domain` (no `body` column). Plan's verification scripts assumed a `body` column and used Node `@supabase/supabase-js` which isn't installed in the Python orchestrator — used Python `.venv` + correct field set instead.

**Outcome:** CAI-RESP-* and CAI-* acceptance-path rulings now appear in cc-ihsanos's inbox automatically. No more manual paste-by-Musa for accepted decisions. Closes the third bug in the BUG-019/020/025 governance-comms family.

Next P0: ARCH-035 (agent_status table + channel split per CAI-RESP-036).

## Previous Completed (2026-04-19 — governance comms v1 hardening)

### BUG-020 + BUG-021 — shipped
Plan: `docs/superpowers/plans/2026-04-19-governance-comms-pipeline-hardening.md`
Spec: `docs/superpowers/specs/2026-04-18-governance-comms-pipeline-hardening-design.md`

**Schema migration** (`supabase/migrations/20260419_bug020_bug021_governance_comms_hardening.sql`, applied live):
- `agent_messages.forwarded_to_telegram_at TIMESTAMPTZ` (BUG-021 — middleware's own stamp column)
- `strategic_decisions.announced_by_msg_id BIGINT REFERENCES agent_messages(id) ON DELETE SET NULL` (BUG-020 — FK dedup guard)
- Partial indexes on NULL subsets of both columns (hot-set optimisation)
- `trigger_cai_decision_announce()` + BEFORE INSERT/UPDATE triggers: fires only for `source='claude_ai_session' AND challenge_status='challenge_window' AND bypass_review=false AND announced_by_msg_id IS NULL`; UPDATE variant guards against state-noise by checking `OLD.challenge_status != 'challenge_window'`
- Per-orphan atomic backfill DO block (notified_at IS NULL filter — preserves historical manual announcements)

**Code changes:**
- `nervous_system/agent_messages_poll.py` (commit `3472771`): `.is_("forwarded_to_telegram_at","null")` added to polling query; `_mark_read` replaced with `_mark_forwarded` on both live + dedup paths; cc-* guard removed (middleware no longer clobbers `read_at`); docstring updated
- `scripts/build_launch_context.py` (commit `d61e8ff`): stamps `forwarded_to_telegram_at` on surfaced inbox rows instead of `read_at`; classified as middleware per plan (forwards inbox digest to Musa via Telegram)

**Live verification:**
- Test msg id=242 (smoke): forwarder stamped `forwarded_to_telegram_at`, left `read_at IS NULL` ✓
- `strategic_decisions` id=263 BUG-020-VERIFY → trigger created `agent_messages` id=247 + set `announced_by_msg_id` ✓
- FK `ON DELETE SET NULL` verified on cleanup
- Orphan sweep: 0 (14 historical rows correctly excluded by `notified_at IS NULL` filter — manually notified 2026-04-18T11:33:57Z)
- pytest full-suite: 355 pass, 7 pre-existing failures (not regressions — verified by git-stash rollback)

**Outcome:** Governance pipeline now end-to-end: CAI writes strategic_decisions → trigger queues review_request → notifier forwards to Musa → cc-ihsanos detects unprocessed mail via `read_at IS NULL` regardless of forwarder state. No more governance blackouts from the 2026-04-18 pattern.

## Previous session (2026-04-18 evening)

### TASK-043 Phase 3 — Baseline verdict: MARGINAL (cc-ihsanos-3, 22:30 SGT)
- 2h baseline complete (PID 55042, 24 samples @ 5min): avg idle 62.6%, avg user 21.4%, avg sys 16.1%, min idle 3.8%, max idle 82.9%
- Verdict **MARGINAL** (62.6% between 40–70% band) — revisit after ARCH-030 cutover reduces load
- Full report: `reports/mac-mini-baseline-2026-04-18-2230.md`
- Posted as agent_messages #235
- REVIEW bucket follow-up (msg #234): 5/6 flagged processes died naturally between Phase 2 and Phase 3; PID 47922 remaining is ChromeRemoteDesktopHost (launchd-managed, legit) — no action required

### TASK-043 Phase 2 — Mac Mini process audit script shipped
- `scripts/audit_mac_mini.py` fully operational: Phase 1 dry-run, Phase 2 SIGTERM kills, Phase 3 CPU baseline
- Fixed `SUPABASE_SERVICE_KEY` env var name (was `SUPABASE_SERVICE_ROLE_KEY`); added `dotenv` auto-load for standalone use
- Phase 2 cleared 5 orphaned pytest workers (test_queue_stall_detector, 4+ days stale, ~180% CPU freed)
- Commit: `b33db53`

### BUG-022 — agent_messages claim/lock pattern (CC-2 prerequisite)
- `claimed_by TEXT` + `claimed_at TIMESTAMPTZ` added to `agent_messages` in orchestrator Supabase
- `idx_agent_messages_claim` index + `agent_message_stale_claims` view (stale = >15 min old with `responded_at IS NULL`)
- Claim pattern: atomic `UPDATE WHERE claimed_by IS NULL RETURNING *` — 0 rows = lost race
- Verified end-to-end via Python: first claim wins, second returns 0 rows, release works
- CC-2 launch now safe: `CC_AGENT_ID=cc-ihsanos-2 ~/wingmen/orchestrator/scripts/launch_dangerous_cc.sh`

### BUG-023 — pytest-timeout enforcement (hung-test orphan prevention)
- `pytest-timeout==2.4.0` added to `requirements.txt`
- `pytest.ini`: `timeout = 60`, `timeout_method = thread` (asyncio-safe)
- `@pytest.mark.timeout(10)` added to `test_recovery_clears_dedup` (the test that spawned the zombies)
- Commit: `aea4ce2` (pushed)

### TASK-044 and ARCH-033 — reviews posted to CAI
- TASK-044: daily 08:00 SGT cron, allowlist seeded, polling self-deprecation, 24h Telegram escalation
- ARCH-033: Tier 2 optimistic-lock checks, pre-commit runner, repo-specific prompts, bypass allowlist

## Last Completed Job (previous)

## Result Summary
Docs-only change: created `README.md` with H1 + appended `<!-- PIPELINE-TEST-001: pipeline marker 2026-04-17 -->` marker. No code paths touched, no restart required. Audit row written to `work_outputs` by orchestrator.

## Completed (Last 5)
- [green] Job #106: ihsanos — [BUG] still see an empty dashboard for qurban supplier (18m 0s, deploy: https://ihsanos-a1ok6x0o6-musaaaaaaas-projects.vercel.app)
- [green] Job #105: ihsanos — [BUG] where does the qurban supplier add the slaughterman information? (5m 52s, deploy: https://ihsanos-fpshtw62t-musaaaaaaas-projects.vercel.app)
- [red] Job #93: wingmen-orchestrator — PIPELINE-TEST-001: Add test marker comment to README.md (1m 1s, deploy: N/A)
- [green] Job #93: wingmen-orchestrator — [PIPELINE-TEST-001] README pipeline marker (deploy: N/A)
- [red] Job #91: ihsanos — QURBAN-GAP-004 (concrete): age_months column + Islamic fiqh validation (5m 45s, deploy: N/A)

##                Recent Jobs (auto-tracked)

Last Updated: 2026-04-18 04:00 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #65 | [TASK-028] Paused job Telegram escalation — no more silent deaths | green | N/A |
| #65 | [TASK-028] Paused job Telegram escalation — tiered alerts with dedup | green | N/A |
| #64 | [TASK-027] Pre-flight dirty-tree check before Claude Code runs | green | N/A |
| #63 | [TASK-026] Decision auto-flip on job completion — close the state-tracking gap | green | N/A |

---

## Recent Jobs (auto-tracked)

Last Updated: 2026-04-18 04:00 SGT

| Job | Description | Status | Deploy |
|-----|-------------|--------|--------|
| #93 | PIPELINE-TEST-001: Add test marker comment to README.md | red | N/A |
| #85 | [BUG-018] strategic_decisions_poll queues jobs for already-shipped decisions — a | red | N/A |
| #85 | [BUG-018] strategic_decisions_poll shipped-decision filter — evidence_commit_sha IS NULL + challenge_status != 'implemented' | green | N/A |
| #90 | [SMOKE-001] BUG-019 worktree isolation smoke test — append a comment to STATUS.m | red | N/A |
| #84 | [BUG-013] qa_findings.created_at migration — column + index added, bridge unblocked | green | N/A |
| #83 | [BUG-016] Safe-restart procedure — launchctl kickstart helper + runbook; nohup forbidden | green | N/A |
| #82 | [BUG-015] Graceful shutdown asyncio cleanup — cancel pending tasks before loop close | green | N/A |
| #79 | [BUG-012] Gate 6 Haiku empty-JSON fix — ANTHROPIC_API_KEY guard + fail-loud | green | N/A |
| #71 | Queue stall detector — alert CTO on 30min+ queued jobs with dedup | green | N/A |
| #70 | [TASK-033] Zombie running-row cleanup on orchestrator startup | green | N/A |