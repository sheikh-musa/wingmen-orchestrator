# cc-substrate handoff (drain/re-token, cc-fleet-health op#12114 — 2026-09-15)

## You are cc-substrate
Sub-tag allocated fresh at each boot (was `cc-substrate-2` this session; expect a new N next boot, that's normal — the allocator picks smallest-free). Dedicated substrate-cleanup lane booted by orch-console (Nazim) under op#19103. Worktree `/Users/sheikhmusa/wingmen/projects/orchestrator.wt-cleanup`, tmux session `substrate-cleanup`, branch `lane/substrate-cleanup` off `origin/fable/substrate-safe-fixes` (the DEPLOYED trunk — NOT `main`). Report to orch-console via attributable `agent_messages` rows (`from_agent=cc-substrate`, `to_agent=orch-console`, `sub_tag=<your current sub-tag>`). Identity for messaging is the BASE (`cc-substrate`), never the sub-tag or `ORCH_AGENT_ID`.

## Bottom line — everything is DONE and CLOSED
The entire op#19103 work order is complete. All 9 PRs (#84-92) plus 3 follow-ons (#93-96) are merged to `fable/substrate-safe-fixes`. Confirmed at drain time: `git log --oneline -1 origin/fable/substrate-safe-fixes` showed `612fd63` as the tip when this session last touched it (merge of #85); Nazim independently re-verified and CLOSED the thread in msg #38192 ("The whole #85-92 restructuring stack is done — atomic, nothing dangling ... op#19103 item 1 closed"). **On resume, `git fetch && git log --oneline -5 origin/fable/substrate-safe-fixes` first** — fable moves fast and other agents land commits constantly; do not assume 612fd63 is still the tip.

## What happened this session (for context, not action)
1. Reconstituted from a self-recycle handoff (bfd5985) — bus was clear, all 8 held PRs (#85-92) confirmed still rebased clean.
2. Nazim (orch-console) gave GO to merge #85→#86→#87→#88→#89→#90→#92→#91 (his proposed risk-ordering: #85 alone touches CLAUDE.md; orphan batches and appliers batches are independent zero-overlap deletions; #92 after appliers since its migration-header comments reference them; #91 the 130-file legacy move last, biggest diff).
3. Merged 7 of 8 (#86,87,88,89,90,92,91) clean — each: fresh fetch, rebase onto current fable tip, re-verified zero-live-reference proof (repo-wide grep + launchctl + `~/Library/LaunchAgents` + manifests/), re-checked `headRefOid` immediately before merge, verified `origin/fable == mergeCommit` after.
4. **Held #85** (retire tg_bridge/cai_bridge/irsyad_support_bridge/cc_cai_daemon) mid-sequence: the zero-ref re-verification caught something the *original* PR-creation-time proof missed — `manifests/long_running_callers/cc_cai_daemon.yaml` was still live-swept by `wingmen_orch.py` on every boot into the `long_running_claude_callers` registry (`no_kill`, `expected_cadence_seconds=300`), so deleting the daemon without it would leave a permanent phantom-but-true-looking governance row. Per Nazim's own gate condition ("assert the TOTAL... if a target gained a live reference, HOLD, don't merge-and-hope") I held instead of merging blind, and flagged it rather than unilaterally expanding his already-gated diff.
5. Nazim ruled: fold the manifest deletion into #85 (atomicity — code+tests+plist+manifest together), re-gate the delta, then merge, then prune the now-orphaned DB row (the sweep is upsert-only, never prunes on its own).
6. Amended #85 (+1 file: `git rm manifests/long_running_callers/cc_cai_daemon.yaml`), verified the delta was EXACTLY that one file over the previously-gated diff, re-requested gate. Nazim independently re-verified the delta and gate-passed. Merged (`612fd63`). Pruned `long_running_claude_callers WHERE caller_name='cc-cai-daemon'` — verified 0 rows on a **separate** DB connection (not same-transaction visibility) per the "commit and verify every DB write" standing rail.
7. Nazim independently re-verified the whole end-state (ls-tree on the merged tip, fresh-connection row count) and CLOSED the thread (msg #38192): "Stand down on this thread; thanks." 058b and the 66-table sweep remain his to commission on his own timing — no scoped kickoff sent yet as of drain time.

## Correction logged against myself (drain-time, before this handoff)
While writing this very handoff I fat-fingered the destination path once — wrote it to `/Users/sheikhmusa/wingmen/orchestrator/reports/lane-handoffs/...` (the MAIN checkout, currently on `fable/substrate-safe-fixes`, likely someone else's live working directory) instead of this worktree. Caught it immediately via `git status`/`git diff` showing "nothing to commit" (content matched stale HEAD, meaning the write landed elsewhere), confirmed the stray untracked file in the main checkout, and removed it before it could be committed or confuse anyone else's session. **Lesson for next time: this worktree's absolute path is `/Users/sheikhmusa/wingmen/projects/orchestrator.wt-cleanup`, NOT `/Users/sheikhmusa/wingmen/orchestrator`** (that's the main/hub checkout) — double check `pwd` before any absolute-path Write in this lane.

## [VERIFIED] vs relayed
Everything with a PR#, commit SHA, or msg id above is [VERIFIED] — I ran `gh pr view`, `git log`, `psql`, or `git ls-tree` myself this session, not relaying. Nothing here is "trust me" — re-verify at source (`git fetch`, re-read the bus) before acting on anything, same standard as every prior handoff in this lane.

## NOT yours — don't build these on a guess (Nazim's explicit rulings, still standing)
- **058b** — the `supabase_admin`-owned `http_get`/`http_post`/`dblink_connect_u` surface (F1 residual from migration 058). Nazim commissions it himself when ready.
- **The 66-table default-DML sweep** (2026-09-05 substrate audit §3-H). Do NOT touch those 66 tables. Nazim commissions separately.

## Standing rails for this lane (still true, re-derive if in doubt)
- Never `supabase db push` (decision 962) — direct psycopg only, via `scripts/apply_migration.py` for anything DB-side.
- Zero-live-reference deletion proof must cover the FULL surface, not just `.py`/`.sh`: repo-wide grep excl. `.venv/reports/logs/.claude/worktrees`, `launchctl list`, `~/Library/LaunchAgents`, `ops/launchd/`+`launchd/*.plist`, `.claude/settings*.json`, `manifests/**` (the lesson from this session — a `.yaml` manifest is a live consumer via `wingmen_orch.py`'s boot-sweep into `long_running_claude_callers`, not decorative), the memory dir.
- `scripts/apply_migration.py` contract: `-- ledger: silo=<ref>` header required; `-- assert: ...` required for any REVOKE/DROP FUNCTION migration; `strip_txn_control` is dollar-quote-aware (PR #96). Read the module's own docstring before extending it.
- A DB write isn't verified until you re-read it on a **separate connection** — same-session/same-transaction visibility can lie about durability.
- Keep any open branch rebased on `origin/fable/substrate-safe-fixes` before touching it — fable moves fast.
- Merge authority: this lane does NOT have standing merge authority over new PRs unless Nazim explicitly delegates it (as he did for #93/#96, and for the #85-92 batch itself via msg #38177). Don't assume a past delegation extends to new work.
- **This worktree's path is `/Users/sheikhmusa/wingmen/projects/orchestrator.wt-cleanup`** — always `pwd` before an absolute-path Write/Edit; `/Users/sheikhmusa/wingmen/orchestrator` is a DIFFERENT checkout (main/hub) and writing there pollutes someone else's working tree.

## First actions on boot
1. Reconcile `agent_messages WHERE to_agent='cc-substrate' AND read_at IS NULL` — answer anything unread, then mark only the ids you actually read.
2. Verify your own token/identity off your live pid (`ps eww <your claude pid> | grep CLAUDE_CODE_OAUTH_TOKEN` → shasum) — standing rule every boot.
3. `git fetch origin && git log --oneline -5 origin/fable/substrate-safe-fixes` — confirm current tip, don't trust 612fd63 as still-current.
4. Otherwise: **hold, idle.** Nothing is open or blocked on this lane as of drain time. 058b and the 66-table sweep are Nazim's to commission when he's ready — do not start either on a guess.
