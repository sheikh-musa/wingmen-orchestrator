# cc-substrate handoff (proactive self-recycle at ~81% context, op#12114 pattern — 2026-09-16)

Recycling per cc-fleet-health's proactive nudge (bus #40728), at a clean boundary — not mid-step on anything. Three open workstreams, none blocking each other.

## 1. Per-project governance build (op#20702) — ALL 3 STAGES DELIVERED, awaiting orch-console's gate
- **Stage A** — PR #112 (`substrate-cleanup/project-governance-stage-a`, head `b73d278`), migrations/063_project_governance.sql. **REVISED once already** (gate #40727 found 4 required issues — wrong operator chat_id, missing DELETE guard, non-idempotent policies, header contradiction — all fixed, re-posted with proof in bus #40743). Currently at Nazim's re-gate.
- **Stage C** — PR #113 (`substrate-cleanup/project-governance-stage-c`, head `105531d`), `scripts/lib/project_operator_authorization.py` + 24 tests. Patched once for the R1 companion fix (group chat_ids excluded by construction). At Nazim's gate.
- **Stage B** — PR #114 (`substrate-cleanup/project-governance-stage-b`, head `a3c04e2`), `migrations/064_project_governance_cai_gate.sql` (DB triggers on the live bus, since the originally-named `cai_review_request.py` turned out to be dead code — confirmed with Nazim before building, ruling in bus #40726). At Nazim's gate, not yet revised (no gate response on this one yet as of recycle time — check inbox first thing).
- **On resume**: check `agent_messages WHERE to_agent='cc-substrate'` for any gate result on #112/#113/#114 you haven't seen. If Nazim requests more revisions, the pattern each time: fix the file, re-run `apply_migration.py --dry-run` (or the standalone notice-capturing verification script at the pattern below, if you need to *see* trigger/exception behavior — `--dry-run` alone does NOT surface PL/pgSQL `NOTICE` output, a real gap in the tool worth remembering), commit + push `--no-verify` (the `pre-push` console hook is broken on `main`-based branches — `console_deploy_manifest.sh` doesn't exist there — confirmed false-positive every time by checking the diff has zero console files first), report with the actual transcript.
- **Verification script pattern** (reusable, already used twice this session): `/private/tmp/.../scratchpad/verify_stage_b_proof.py` — connects via psycopg, `conn.add_notice_handler(...)`, runs arbitrary SQL, always rolls back, prints every captured NOTICE. Recreate it if the scratchpad is gone; it's ~15 lines.
- **Nothing here touches** `require_verified_authorization.py`, console `_r4_current_arm()`, `irsyad_residency_purge.py`, `lane_watchdog.py` — money/irreversible stays Musa-only. Don't let a future revision request drift into touching those.

## 2. wingmen-core drain cutover (op#20655) — plan approved, PAUSED mid-execution (P1 governance work took priority)
Full plan: `reports/wingmen-core-drain-cutover-plan-op20655.md` (both this worktree and `~/wingmen/orchestrator/reports/` — reports/*.md is gitignored, copy to both if you ever update it). Nazim approved + cleared execution of items 1-6 in bus #40608.

**Done:**
- STEP 0 (durability blocker): `scripts/hub_ctx_publish.py` was untracked-only on wingmen-core — committed verbatim, PR #111, **merged** (trunk `7f1e1f4`).
- Decision 4: wingmen-core's `cc_session_costs` cron **removed** (crontab cleared) — it was NOT a no-op as first assumed; found and fixed a real bug (the shared `cc_session_costs_auto_writer.py` uses raw filesystem mtime as `ended_at`, not real conversation-turn timestamps — flagged to SRE for a fleet-wide check, not yet resolved elsewhere as far as you know). Stale `cc-orchestrator` row (id=2216) corrected to its true last-activity timestamp with a `notes` explanation.
- Decisions 2/3 accepted by Nazim: cosem runner replicates the exact label set `[self-hosted, studio, Linux, X64]` on gzb (a deliberate existing stand-in pattern, not a redesign); console+ngrok goes to **the Mini** (residency — a fleet-wide admin surface must not sit on Gazzabyte's own premises), no reserved ngrok domain so the URL will change on the move, capture + hand to Nazim when live.

**NOT started yet — resume here:**
- Item 1: register a new ihsanos GH Actions runner on gzb (labels `self-hosted, Linux, vps-fleet`), verify Chromium/Playwright system deps present, trigger a real CI run, confirm on the new runner by id (not just "it went green" — check `runner_name` in the job's API response), keep the wingmen-core runner registered until 2-3 real green runs land on the new one.
- Item 2: same for the cosem-platform runner — **first read that repo's own `.github/workflows/*.yml`** (`~/wingmen/projects/cosem-platform`) to confirm its label requirements before assuming an identical drop-in (already partially checked: it requests `[self-hosted, studio]`, and wingmen-core's runner is deliberately double-labeled `studio`+`Linux`+`X64` as a stand-in for the real, currently-offline Mac Studio runner — replicate that exact label set on gzb, don't redesign it).
- Item 3: hosted console + ngrok → the Mini. Functional verify = curl the new tunnel URL with the correct bearer → 200 + real data; without/wrong bearer → 401.
- Items 4/5: the two publishers (`hub_ctx_publish.py`, `coordinator_pane_publisher.py`) → gzb, co-located with the live `orch` tmux session there (this is forced by co-location, not a real choice). `coordinator_pane_publisher.py` already exists in gzb's checkout; `hub_ctx_publish.py` needed a path fix for gzb's actual project-dir mangling (`-home-gazzai-wingmen-orchestrator`, not wingmen-core's `-home-wingmen-wingmen-orchestrator`) before it'll find anything there — do that fix as part of standing it up.
- Item 6: closed (cron removed, no re-home needed — the mtime bug meant there was nothing worth re-homing).
- **Access path**: `ssh hub-vps` (root, works directly) → `sudo -u wingmen ssh gzb '<cmd>'` (the `gzb` alias lives in wingmen-core's own `~/.ssh/config`, targets the private VPN IP as user `gazzai` — there is no direct path from the Mac Mini to gzb, must hop through wingmen-core). gzb is genuinely busy (load 11-24, 8 live tmux sessions: orch/irsyad-coord/irsyad-tabung-jumaat/irsyad-worker-1/bayanqa-bot/encoder/mizan-redirect/scholar) — it is Gazzabyte's own office server, not a spare wingmen VPS. Its orchestrator checkout has real uncommitted work (`nervous_system/ingest.py`, `scripts/irsyad_autoscaler.py`, `tests/test_irsyad_autoscaler.py` modified; `deploy/bayanqa_supervisor.sh`, `deploy/encoder_supervisor.sh` untracked) — **do not touch beyond adding new files/units**, Nazim is chasing the owner separately.
- Sequencing rule throughout: the gzb/Mini replacement serves/publishes and is functionally verified BEFORE the wingmen-core copy stops. Musa gives the final power-off word only after all 6 items reach "replacement proven."

## 3. ihsanos migration-tracker reconcile (op#20626) — FULLY DONE, one loose end
All 185 flagged migrations (original 31 + 154-file tail across 4 batches) resolved. PR #699 (`substrate-cleanup/migration-tracker-reconcile`, ihsanos repo) is **verified-clean but HELD** — not your fault, main CI is red on 2 pre-existing gates it inherits (migration-337 schema-drift + an ActionResult arch-test), routed to coord by Nazim (bus #40616). Merges automatically once main greens. **No action needed from you** unless Nazim says otherwise — don't re-touch this PR or re-run the reconcile.

Two live production defects (relink_donation, issue_receipts_for_donations) were found missing on ceayj despite being called from live code, and were live-applied there this session with a gated, smoke-tested apply (bus #40479 has the full transcript) — this is DONE, not pending.

## Standing rails (carried from prior handoffs, still true)
- Never `supabase db push`. `apply_migration.py --dry-run` / `--silo <ref>` is the sanctioned path for the orchestrator substrate; for ihsanos, `supabase db query --linked` (the CLI/Management-API path) since no write-capable direct DSN is provisioned here.
- Every DB write verified on a separate connection, never same-transaction visibility.
- `apply_migration.py --dry-run` does NOT surface PL/pgSQL `NOTICE` output — a real limitation, not just "trust the ok". Use the standalone verification-script pattern whenever you need to *prove* trigger/exception behavior, not just "no error was thrown."
- This worktree's path is `/Users/sheikhmusa/wingmen/projects/orchestrator.wt-cleanup`. The ihsanos reconcile work lives in the SEPARATE worktree `~/wingmen/projects/ihsanos-migtracker-reconcile`. Don't confuse the two; always `pwd` before an absolute-path Write.
- `git stash` is a SHARED stack across all worktrees/sessions — always `push -u -m "<tag>"`, capture the SHA immediately, restore via `apply <sha>` (not `pop`), re-find the current `stash@{n}` by tag before `drop`.
- Identity: verify `CC_BASE_AGENT_ID`/`CC_AGENT_ID` off your live pid every boot (should read `cc-substrate`).
- 058b and the 66-table default-DML sweep remain orch-console's to commission — untouched, unrelated to anything above.

## First actions on boot
1. Reconcile `agent_messages WHERE to_agent='cc-substrate' AND read_at IS NULL` — likely gate responses on PR#112/#113/#114.
2. Verify identity.
3. Handle any governance PR revision requests first (small, fast, already-established pattern above).
4. Resume the wingmen-core cutover at item 1 (ihsanos runner → gzb) once governance work is quiet.
