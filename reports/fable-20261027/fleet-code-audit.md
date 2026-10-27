# Fleet code audit — orchestrator repo + Mac Mini launchd (+ gzb where visible)

- **Requested by:** Musa op#34969 (relayed by orch-console, bus #78605)
- **Auditor:** cc-substrate-3 (temporary Fable 5.1 lane, irsyad pool), 2026-10-27 19:11Z–20:50Z
- **Mode:** report only. Read-only on every store (substrate SELECT-only; no client DB touched). No PRs, no merges, no deploys. Seven parallel read-only subagents, each on one dimension; findings de-duplicated and re-ranked by the lane.
- **Base commit audited:** 7f10ed1 (merge of PR #545), worktree `orchestrator.wt-fable-3`.
- **Scope asked for:** PreToolUse hook/guard gaps · prose-only rules (enforce-in-code holes) · dead/duplicate scripts and watchdogs · launchd jobs whose script no longer exists · sleeping failure modes (except-pass, silent fallbacks) · bare `git pull` on the live checkout · worktrees without `.venv` · top-10 simplifications. Ranked by impact over effort, each with `file:line` evidence.

> Ranking key — **Impact** high/med/low = what breaks or how much pages/money/safety it touches. **Effort** S (<1h, one PR), M (half-day), L (multi-day/needs design). "Quick win" = high/med impact at S effort.

## 0. Executive summary — ranked quick wins (impact ÷ effort)

Seven dimensions, ~110 findings with `file:line` evidence (sections 1–7 below, each auditor's own numbering kept: H-, E-, L-, P-, S-, X-, C-). This list is the cross-dimension ranking; "S" = one small PR.

**Headline pattern.** The fleet's doctrine is "enforced in code, not by promise", and most of the code exists. What the audit found is that the *enforcers themselves* fail open at their edges: a missing `.venv` in a worktree, a DB blip, an unset env var, a swallowed exit code, or a `|| true` turns a guard into a no-op while the audit trail still says PASSED/PAGED. The ten highest-leverage fixes are all small and all of this shape.

### Tier 1 — fix this week (high impact, S effort)

| # | Finding | Evidence (one anchor) | Fix in one line | Refs |
|---|---|---|---|---|
| 1 | **Claude Code guards fail OPEN in any worktree without `.venv`** — 9 of 13 sibling worktrees today. Hooks are wired to the literal `"$CLAUDE_PROJECT_DIR"/.venv/bin/python3`; exec failure = exit 127 = non-blocking, so console_irsyad_guard, lane_prod_key_guard, governance-floor guard never run there. No boot step creates or checks the venv; the fable worktrees only work because of hand-made symlinks. | `.claude/settings.json:8,18,30,41,51,61`; `scripts/launch_dangerous_cc.sh:44`; `scripts/lanes.sh:129-131` | Tracked `scripts/hooks/run_hook.sh` that resolves the interpreter (worktree → live → system) and `exit 2` if none; lanes.sh/launch pre-flight that symlinks `.venv`; a test that runs the hook command in a bare worktree. | S-1, H-* (§1) |
| 2 | **Lane launcher swallows the Musa-key FREEZE refusal (exit 42) and boots on the Musa account anyway.** `2>/dev/null \|\| true` discards the code the resolver deliberately emits "so a launcher can ABORT". | `scripts/launch_dangerous_cc.sh:164` vs `scripts/lib/lane_token_resolver.py:352-358` | Capture `rc`, abort on 42, drop `\|\| true`, keep stderr. | P-1 |
| 3 | **Pen-(iv) Telegram and pen-(iii) watchdog lease gates ALLOW on DB error, missing row, missing `.env` or empty role** — "fail-safe for the holder" is indistinguishable from "fail-open for anyone unconfigured". Lanes have `ORCH_BODY_ROLE` scrubbed, so a lane on the Mini can `tg_send` on the hub bot during a substrate outage. | `scripts/lib/orch_lease.py:253,263-272`; `scripts/lib/fleet_health_lease.py:106-119,183-189`; `scripts/launch_dangerous_cc.sh:188-193` | Fail-CLOSED for destructive/pen actions (tg_send, reset_lane, red `/clear`), fail-open only for paging; refuse when DSN is None; no default acting identity. | P-3, C-2, S-6, P-21 |
| 4 | **Three unleased back doors into the hub's operator channel**: `tg_out.enqueue()` has no lease check and hard-codes identity `cc-orchestrator`; `health_check.sh` and `daily_backup.sh` curl the hub bot token directly from Mini launchd with no lease and no `operator_messages` log. | `nervous_system/tg_out.py:181-195`; `scripts/health_check.sh:128-130`; `scripts/daily_backup.sh:79-81` | Lease-check hub channels inside `enqueue()`; route both scripts through `tg_send.sh`; test that the set of files touching `api.telegram.org/bot…WINGMEN` is exactly `{tg_send.sh, tg_send_file.sh, tg_out.py}`. | E-2, E-3, S-5 |
| 5 | **Watchdogs latch "PAGED/alerted/escalated" even when the send failed** — page-once logic then never retries; `nazim_send.sh` fails silently off the hub host, so the audit trail says PAGED while the phone is silent. | `scripts/priority_sla_watchdog.py:2053-2062`; `scripts/context_health_watchdog.py:874-878,1626-1629`; `nervous_system/lane_wedge_watchdog.py:947-961`; `nervous_system/singleton_liveness.py:406-408` | Every `_page()` returns bool; stamp `paged` and write the PAGED audit row only on True. | P-2, P-14, P-15 |
| 6 | **`.env.pre-*` secret backups in the live checkout are NOT gitignored and `pre-commit` has no secret/`.env` scan** — one `git add -A` commits the whole secret set. | `.gitignore:4,23,24`; `scripts/git-hooks/pre-commit:1-51`; `git check-ignore .env.pre-rotation-…` → not ignored | `.env*` + `!.env.example` in `.gitignore`; pre-commit refuses staged `^\.env($\|\.)` and runs `secret_shape_patterns.py` over the staged diff. | E-1 |
| 7 | **`com.cosem.powersync` has respawned 34,322 times on a deleted script** (KeepAlive, exit 126) and **`irsyad-pii-containment-monitor` has exited 1 every hour since ~Aug 28 (1,469 runs)** with its last log line two months old — the P0 minors-PII containment assertion has not run, and nothing pages on it. | `launchctl print gui/501/com.cosem.powersync`; `scripts/irsyad_pii_containment_monitor.py:710`; log mtime Aug 28 | Bootout + delete the cosem plist; have irsyad-coord run the monitor once by hand to surface the error; add a heartbeat row + `fleet_health.py` page when >2h stale. | L-1, L-2 |
| 8 | **`tg_out` outbound reliability**: retry backoff keyed on `created_at` (a 10 s Telegram blip burns all 5 attempts → `dead` for 24 h); error path has no `rollback()` so a post-send DB error re-sends the same message every 120 s forever; 429 `retry_after` ignored; chunk at 4096 code points vs Telegram's UTF-16 count; durable log row written unredacted. | `nervous_system/tg_out.py:300-307,315-323,63,225/295` | `last_attempt_at` + exponential backoff, honour 429, `rollback()` first, `CHUNK=4000`, audit via `operator_log.log()`. | C-3, P-6, P-7, C-12, P-13 |
| 9 | **No gate on `supabase db push`** against any production store — the decision-962 arm-loss class is still one command away; the only check is a CI-only warning. | `scripts/check_additive_migration.py:456-493`; `scripts/hooks/*` 0 hits | PreToolUse Bash hook refusing `supabase db (push\|reset\|link)` unless `--local`; add to the lane deny list. | E-5 |
| 10 | **`bus_send.send()` raises `SystemExit` on refusal; daemons catch `Exception` only** — a refused page can take down `ingest` (asyncio) or lose the "dropped" note in `tg_out`. | `scripts/bus_send.py:265,591`; `nervous_system/ingest.py:538-539,1397`; `operator_log.py:688-699` | `BusSendRefused(Exception)`; only CLI `main()` converts to an exit code. | P-8 |

### Tier 2 — this sprint (high/med impact, S–M effort)

| # | Finding | Anchor | Refs |
|---|---|---|---|
| 11 | **Action-taking watchdogs run ungated by `fleet_health_lease`** (pen iii): `idle_with_work_watchdog` nudges lanes, `sre_lane_recycle` checkpoints→`/clear`→reboots, plus `checkpoint_recycle_driver`, `lane_recycle_deadman`, `lane_watchdog`, `supervised_draft_deadman` — "never two concurrent holders" does not hold on SRE expiry. | `scripts/idle_with_work_watchdog.py:38,126`; `scripts/sre_lane_recycle.py` (0 lease refs) | E-4 |
| 12 | **Live checkout carries 12 uncommitted tracked edits + 242 untracked files**, including a 2026-10-27 hot-patch to `tg_send.sh`/`nazim_send.sh` that GitHub has never seen; launchd daemons and `deploy_console.sh` execute/ship that tree; the next `pull --ff-only` touching those files will refuse and tempt a `checkout -- .` or a shared-stash push. | `git -C ../orchestrator status --short`; `scripts/deploy_console.sh:30-31` | S-2 |
| 13 | **Ingest poison pill**: a `DataError`/RLS denial on the `operator_messages` INSERT is logged as "DB unreachable" and the offset is never acked → the whole bot token is deaf while the DB is healthy. Separately, a crash between the log commit and the offset ack drops the asks-ledger row and nudge on redelivery (dedupe says already-processed). | `nervous_system/ingest.py:1289-1299,1526-1533`; `:1334→1357-1424` | P-9, C-1 |
| 14 | **Permanent DB fault = clean scan forever**: `lane_wedge_watchdog --loop` turns any DB error into `observations=[]` (zero wedges, zero pages, heartbeat keeps ticking); `priority_sla_watchdog` exits 1 per tick with no streak/page; `fleet_health.live_tmux_sessions()` returns an EMPTY set on tmux error → live lanes marked offline and their mail archived. | `nervous_system/lane_wedge_watchdog.py:1657-1659`; `scripts/priority_sla_watchdog.py:1899-1904`; `scripts/fleet_health.py:96-101,391-395` | P-10, P-4 |
| 15 | **Archive never runs; `agent_messages` grows unbounded (73k rows, +6k/week), no partial index for the inbox query; `ingest_dedup` never pruned.** `archive.py`'s only caller is the retired `legacy/wingmen_orch.py`. | `nervous_system/archive.py:4`; `legacy/README.md:8-10`; `pg_indexes` | C-4 |
| 16 | **`lane_nudge.sh:190` hand-written INSERT omits `priority`** → a "P1" REVERT-FAIL blocker lands at the P2 table default, below the hub wake floor (bus #43651 recurrence). ~95 hand INSERTs bypass `bus_send.py`; this is the only one that omits priority — drop the `'P2'` DEFAULT so the class fails loudly. | `scripts/lane_nudge.sh:190-191`; `supabase/migrations/20260420_arch036_priority_column.sql:19` | E-6 |
| 17 | **Launchd estate is unreproducible**: 44–48 installed plists untracked (incl. `nazim-session`, `fleet-console`, `fleet-health`, `front-desk`); 2 tracked plists drifted live (`lane-proactive-recycle-nudge` is ARMED on disk, unarmed in git); three competing plist dirs (`launchd/`, `ops/launchd/`, `scripts/launchd/`); 21 `.bak/.disabled` litter files; two plists target files moved to `legacy/` and `restart_orch.sh` still kickstarts that dead label; expired one-offs still polling (`op11911-syed-trigger` every 300 s since Aug 12, tripwire-24h/48h from May, gzb bapa cron "REMOVE after 24h"). | §3 of L; `scripts/restart_orch.sh:24` | L-3, L-4, L-6, S-4, X-4 |
| 18 | **`orch_lease` is renewed by a host timer independent of the hub session** — fresh lease ≠ live hub; blocks DR `take`, and `commitment_sweeper` routes due commitments into a dead hub; `singleton_liveness` collapses "unreadable lease" into ALIVE and uses prefix-matching `tmux has-session -t name` (no `=`). | `scripts/lib/orch_lease.py:455-462,520-523`; `nervous_system/singleton_liveness.py:173,328-333`; `commitment_sweeper.py:117-123` | C-5, C-9, P-23 |
| 19 | **`core.hooksPath` is an absolute path into the live checkout** — every worktree runs the live tree's git hooks, a branch that fixes `pre-push` is never exercised, and `pre-push` gate 1 warns-and-passes when pytest is absent. `--no-verify` is advertised inside the hooks. | `git config core.hooksPath`; `scripts/git-hooks/pre-push:94,110-111` | S-3 |
| 20 | **Swallowed-except harness guards nothing live** — all 68 `record_swallowed()` sites are in `legacy/wingmen_orch.py`; the ~470 `except: pass/continue/return` sites in the live psycopg daemons have no counter. `operator_log` import silently overwrites `os.environ["DATABASE_URL"]` from whichever `.env` sits beside the imported copy. | `nervous_system/swallowed_except_harness.py`; `nervous_system/operator_log.py:32-38` | P-5, P-17 |

### Tier 3 — simplifications (the top 10, ranked; §6 has evidence)

X-1 one `scripts/lib/db.py` connect helper replacing **80** hand-rolled `_dsn()/_connect()` in 71 files (the op#24342 file-first DSN fix reached 11 of ~147 sites) · X-2 collapse the **seven** context-bloat/recycle readers (bars 70/80/80/85/88/90/95 %) into one `lane_context_policy` tick · X-3 collapse the four `*_bus_notify.py` daemons (nazim's copy diverged by 269 lines of fixes the others never got) · X-4 one scheduler/registry for ~60 watchdog entrypoints instead of ~85 launchd jobs in three naming conventions · X-5 delete `legacy/` (6.3k lines) + 7 nervous_system modules only it imports + the 2 ghost plists; fix CLAUDE.md "Key Files" (`wingmen_orch.py` no longer exists at root) · X-6 delete the 58 scripts (8.6k lines) executed by nothing — the 2026-10-06 orphan list already named most of them and the count did not drop · X-7 one parameterised Telegram sender for 24 `*_send*.sh` + 21 direct API callers · X-8 split the three ~2,100-line watchdogs along the probe/paging seams · X-9 one constants module for project refs/agent ids (`orch-console` literal appears 112× in 53 files) · X-10 fix migration number collisions 037/043/072 and retire the three bespoke appliers.

### What was checked and is sound
Ingest dedupe + offset-after-commit ordering; `operator_log` body-scoped id-exact `mark_handled`; `bus_send.py` fail-closed identity and required `--priority`; `require_verified_authorization.py` fail-closed on every branch; `hub_self_recovery` observe-only and host-gated; console_irsyad_guard / consult-floor guard / lane_prod_key_guard return 2 on internal crash when they run; `restart_orch.sh` nohup refusal; no tracked secrets; no `shell=True`/`eval`/`chmod 777` in live code; no script or launchd job runs a mutating git op against the live checkout (all ~25 recent advances were hand-typed `pull --ff-only`); crontab empty; gzb units all point at existing paths with 0 failed wingmen units.

### Fleet-wide root causes (what to change about *how* code is added)
1. **Enforcers have no self-test.** Nothing proves a hook, lease gate or watchdog page actually fires. → policy-as-code + drills (NOVEL IDEAS N1, N4).
2. **Every op# fix adds a file**: a new plist, a new wrapper, a new `_dsn()`, a new watchdog layer "the previous one missed". → registry + scheduler (X-1, X-4), orphan-count test that can only go down (X-6).
3. **Runtime config lives outside git**: 48 untracked plists, hand-edited arm flags, `.bak` files as the change log. → `fleetctl` change ledger (N3).
4. **"Best-effort" paging**: `|| true`, `2>/dev/null`, `except: pass` around the one call that must not fail. → return bool, stamp only on success (P-2), a lint that forbids `except: pass` within N lines of `send|page|nudge|audit`.

---

# 1. PreToolUse / PostToolUse hook and guard gaps (auditor A)

_(auditor did not return before the deadline; see bus summary)_

---

# 2. Enforce-in-code holes — prose-only rules (auditor B)

## B — "enforce-in-code holes" audit (read-only, worktree `orchestrator.wt-fable-3`, 2026-10-28)

Scope: every "never/always/only via/must" in CLAUDE.md + `docs/governance-floors.md`, skimmed `docs/runbooks`, `docs/playbooks`, `templates/lane_claude_template.md`. For each: is there a hook / git hook / refusing wrapper / DB constraint-trigger / launchd watchdog? All paths relative to the worktree root unless absolute.

### Summary table

| Rule | Source | Status | Enforcer or gap |
|---|---|---|---|
| Never commit `.env` | CLAUDE.md "Rules" | **PARTIAL** | `.gitignore:4,23,24` covers `.env`, `.env.bak*`, `.env.*.bak` only. On-disk live-checkout files `.env.pre-rotation-20261001T150319Z`, `.env.pre-model-switch-…`, `.env.pre-nazim-model-…`, `.env.pre-frontdesk-disable-…` are **NOT ignored** (`git check-ignore` → not ignored); `.env.local/.env.production/.env.hub/.env.console` also not ignored. `scripts/git-hooks/pre-commit:1-51` has **no secret/`.env` check** (it only blocks SRE commits in the live checkout). No tracked secret-shaped strings found (all hits are test fixtures / fakes). |
| Never `supabase db push` vs production | CLAUDE.md Rules; `docs/governance-floors.md` | **PROSE-ONLY** | Only `scripts/check_additive_migration.py:456-493` emits a *warning* when its parent process is the supabase CLI — and that script is only invoked by CI (`.github/workflows/ci.yml:91`), not on the `db push` path. No PreToolUse hook matches `supabase db push` (`scripts/hooks/*`: 0 hits for `db push`). |
| `agent_messages` only via `scripts/bus_send.py` | CLAUDE.md Rules; floors doc L265 | **PARTIAL** | `bus_send.py` makes `--priority` required (ENFORCED for the ad-hoc path). But ~95 hand-written `INSERT INTO agent_messages` exist across `scripts/` + `nervous_system/` daemons (list below) with no guard; `supabase/migrations/20260420_arch036_priority_column.sql:19` keeps `DEFAULT 'P2'`, so an omitted `priority` still silently lands at P2. Live offender: `scripts/lane_nudge.sh:190` — subject says "P1" but column list omits `priority` → lands P2 (exact bus #43651 failure class). |
| Never write explicit id into serial/identity insert | CLAUDE.md Rules; floors L266 | **PARTIAL** | No guard. All current `INSERT … (id,` hits target `agents`/`storage.buckets` (text PKs — fine) or `hub_self_recovery_settings`/`profiles`. Backstop only: `scripts/fleet_health.py` `sequence_drift()` self-heal every ~10 min. |
| Restart orch only via `scripts/restart_orch.sh`, never nohup | CLAUDE.md Rules | **ENFORCED** (for that script) | `scripts/restart_orch.sh:6-17` refuses when SIGHUP is ignored (nohup/disown); `tests/test_restart_script.py`. Gap: nothing stops `python wingmen_orch.py &`/`nohup` directly — acceptable. |
| Deploy console only via `scripts/deploy_console.sh` | CLAUDE.md Rules | **PARTIAL** | `scripts/git-hooks/pre-push:115-148` gate 2 + `deploy_console.sh` 4 gates; `core.hooksPath` IS set to `/Users/sheikhmusa/wingmen/orchestrator/scripts/git-hooks` in both this worktree and the live checkout (verified read-only). Gap: nothing refuses a raw `launchctl kickstart dev.wingmen.hosted-console` (0 hits for `kickstart` in `scripts/hooks`, `scripts/lib`, `scripts/git-hooks`); pre-push gate 2 degrades to no-op on branches lacking the manifest (`pre-push:116-119`) and both gates advertise `--no-verify`. Console serves the live checkout, so a kickstart ships whatever is there. |
| Never checkout inside live `~/wingmen/orchestrator` | CLAUDE.md Fleet lanes | **PARTIAL** (detect/page only) | `scripts/git-hooks/post-checkout:22-36` pages after the fact (exit 0 always, L17-19); `scripts/live_checkout_branch_watchdog.py` pages on >2 min drift. Nothing PREVENTS (git has no pre-checkout hook; no wrapper). `pre-commit:29-40` blocks only the SRE identity committing there — console/lane identities pass. Scripts that git-mutate the live checkout: `scripts/setup_mirror_wsl.sh:75` (`git -C $HOME/wingmen/orchestrator pull --ff-only`, ORCH_DIR at L23). Read-only fetches: `scripts/lib/deploy_safety_guard.sh:49`, `nervous_system/repo_context_writer.py:163`, `scripts/repo_hygiene_sweep.py:101,126` (target = managed repos, not orch). |
| Always update STATUS.md after state changes | CLAUDE.md Rules | **PROSE-ONLY** (and stale rule) | No check anywhere (`STATUS.md` referenced only by readers). `STATUS.md:1-5` itself says it is no longer the changelog; last commit 2026-09-07. Rule is dead letter — delete or repoint. |
| Log all build ops to `build_log`; deliverables in `work_outputs` | CLAUDE.md Rules; floors L269 | **PROSE-ONLY** | No hook/DB check. `build_log` writer in `scripts/`: only `scripts/launch_dangerous_cc.sh`. `work_outputs`: no writer in `scripts/hooks`, `scripts/lib`, `scripts/git-hooks`, `nervous_system`. |
| Pen (iv) `tg_send`/tg-out = `orch_lease` holder only; console fail-closed OUT | CLAUDE.md ORCH-TOPOLOGY-001; floors L173,186,196 | **PARTIAL** | ENFORCED: `scripts/tg_send.sh:21-25`, `scripts/tg_send_file.sh` call `orch_lease.py check`. **GAPS:** (a) `nervous_system/tg_out.py:181-195 enqueue()` has **no lease check** and hard-codes `set_config('app.current_agent_id','cc-orchestrator')` (L185) — any body with the substrate DSN can enqueue to `operator-orch` and the hub-token gateway delivers it (only a `deny_all_tg_out` RLS policy for `public`, `migrations/014_bot_channels_ingest.sql:76`; service role bypasses). (b) Direct hub-bot curls bypassing lease AND `operator_messages` logging: `scripts/health_check.sh:128-130` and `scripts/daily_backup.sh:80-81` (both `WINGMEN_BOT_TOKEN` → `MUSA_TELEGRAM_ID`; both loaded in launchd on this Mini: `dev.wingmen.health-check`, `dev.wingmen.daily-backup`). (c) `scripts/nazim_send.sh` has no `ORCH_BODY_ROLE` check — the hub body can speak on `nazim-console` (thread-ownership rule one-directional). |
| Pens (iii)/(v) via `fleet_health_lease` on watchdog ACTIONS | CLAUDE.md CAI-RESP-501; floors L196-200 | **PARTIAL** | Gated: `context_health_watchdog.py`, `priority_sla_watchdog.py`, `queue_age_watchdog.py`, `lane_wedge_watchdog.py`, `hub_bus_currency_monitor.py`, `lane_proactive_recycle_nudge.py`, `sre_liveness_watchdog.py`, `wake_backstop_sweep.py`, `hub_self_recovery.py`. **Ungated action-takers running under launchd on this host:** `scripts/idle_with_work_watchdog.py` (nudges lanes via `lane_nudge.sh`, L38/126; `dev.wingmen.idle-with-work-watchdog` loaded), `scripts/sre_lane_recycle.py` (checkpoint→/clear→reboot lanes; uses `fleet_health_boundaries` but 0 `fleet_health_lease` refs; `dev.wingmen.sre-lane-recycle` loaded), `scripts/checkpoint_recycle_driver.py`, `scripts/lane_recycle_deadman.py`, `nervous_system/lane_watchdog.py`, `scripts/supervised_draft_deadman.py`. If the hub reclaims the lease on SRE expiry, these keep acting → "NEVER two concurrent holders" (floors L192) is not held for them. |
| `assert_no_sre_red_reset` / `assert_ops_only` | CLAUDE.md CAI-RESP-501 | **ENFORCED** | `scripts/context_health_watchdog.py:2021`; `scripts/lib/a3_invariant_sink.py:25,35`; `assert_sre_never_targets_singleton` in `checkpoint_recycle_driver.py:162,335`, `lane_proactive_recycle_nudge.py:239`, `lane_selfrecycle_detect.py:333`. |
| Lane must pass `tag=` to `unprocessed()/mark_handled()` | CLAUDE.md bridge section | **ENFORCED** | `nervous_system/operator_log.py:242-247` (`_channel_scope` raises for role `lane` without tag); `mark_handled_through` raises for lanes L958-963; UNSET role = lane L53. |
| Triage every ask (never leave `captured`) | CLAUDE.md migration-082 bullet; floors L267 | **PARTIAL** | `scripts/asks_reconcile_daily.py:24,160` fail-loud guard for `captured` rows >24h (daily, `dev.wingmen.asks-reconcile`); digest shows counts only. No real-time check; no DB constraint. Acceptable backstop. |
| Irreversible ops need bridge-verified artifact | CLAUDE.md CAI-RESP-375/376 | **PARTIAL** | `scripts/lib/require_verified_authorization.py` called by `deploy_console.sh`, `irsyad_residency_purge.py`, `stage_client_file.py`, `nervous_system/console/app.py`. Other irreversible paths (`switch_lane_token.sh`, `reset_*.sh`, token/model flips) do not call it. |
| Console never drives irsyad execution | CLAUDE.md Rules | **ENFORCED** | `scripts/hooks/console_irsyad_guard.py:9-31` PreToolUse (wired `.claude/settings.json`). |
| Consult floor SSOT before asserting | floors L52-53 | **ENFORCED** | `scripts/hooks/consult_governance_floor_guard.py` (wired). |
| cai = advisory only, never blocker | floors L23-31, L124 | **ENFORCED-ish (no hard block found)** | No code refuses on missing cai grant (grep for cai_grant/cosign/approve refusals: 0). Only DB trigger `trg_agent_messages_cai_gate` (`migrations/infra/bedrock_substrate_core.sql:291-337`) blocks messages TO cai for `cai_enabled=false` projects — consistent with doctrine. `scripts/hooks/consult_governance_floor_guard.py` keys on "cai+Nazim" language (advisory). |
| `audit_log` INSERT-only / append-only | floors L271 | **PROSE-ONLY** | No trigger/REVOKE UPDATE,DELETE/RULE found on `public.audit_log` in `migrations/`, `supabase/migrations/` (only RLS lockdown on `cc_cai_audit_log`/`ecosystem_audit_log`, `migrations/013:97-151`). Service-role connections can UPDATE/DELETE the hash chain. |
| Never `SELECT *` from `strategic_decisions` | CLAUDE.md Boot; floors L270 | **PROSE-ONLY** | No hook (`scripts/hooks`: 0 refs to `strategic_decisions`), no column-level RLS/view redirect. |
| Lane for new repo seeded with CLAUDE.md before first task | CLAUDE.md Fleet lanes | **PROSE-ONLY** | `scripts/seed_lane_claude_md.py` exists; nothing in `lanes.sh up`/`launch_dangerous_cc.sh` refuses to boot a lane whose repo lacks `CLAUDE.md`. |
| Never raw `launchctl kickstart` for console / only `restart_orch.sh` for orch | CLAUDE.md Rules | **PROSE-ONLY** (kickstart) | 0 hits for `kickstart` in any hook/guard. |
| `supabase db push` for shadow/local only; direct psycopg apply w/ 12-char sha gate | CLAUDE.md | **PARTIAL** | `scripts/apply_migration.py` enforces its own gate; see db push row above. |

### Hand-written `INSERT INTO agent_messages` outside `bus_send.py` (non-test, non-migration)
`scripts/lane_nudge.sh:190,233,293` · `scripts/lane_reply.sh:146` · `scripts/priority_sla_watchdog.py:570,713,757,1174,1401,1463,1808,1855` · `scripts/channel_liveness_watchdog.py:159` · `scripts/repo_hygiene_sweep.py:201` · `scripts/adcda_port_watch.py:101` · `scripts/disk_space_monitor.sh:88` · `scripts/checkpoint_recycle_driver.py:349` · `scripts/auto_recycle_on_bloat.py:420,475` · `scripts/irsyad_confirm_executor.py:91` · `scripts/reset_fleet_health.sh:211` · `scripts/reset_cai.sh:277` · `scripts/reset_nazim.sh:320` · `scripts/reset_auditor.sh:236` · `scripts/lane_proactive_recycle_nudge.py:180` · `scripts/deploy_provenance_watch.py:178` · `scripts/live_checkout_branch_watchdog.py:115` · `scripts/sentry_new_error_watch.py:198` · `scripts/adcda_fn_error_bus_watch.py:220` · `scripts/switch_lane_token.sh:679` · `scripts/sre_liveness_watchdog.py:241,272` · `scripts/gazzabyte_board_autopublish.py:76` · `scripts/client_channel_nodrop_watch.py:287` · `scripts/lane_recycle_deadman.py:244,262` · `scripts/gazzabyte_request_nodrop_watch.py:159` · `scripts/fleet_health.py:209` · `scripts/pooler_breaker_snapshot.py:182` · `scripts/irsyad_worker_reaper.py:194,216` · `scripts/irsyad_shadow_watch.py:115` · `scripts/fleet_stall_watch.py:121,134` · `scripts/irsyad_autoscaler.py:505` · `scripts/lane_drill_seed.py:51,69` · `scripts/sre_lane_recycle.py:379,661` · `scripts/irsyad_spin_worker.py:176` · `scripts/watchdog_tripwire_report.py:162` · `scripts/file_history_prune.py:232` · `scripts/hub_self_recovery_staleness_watchdog.py:123` · `scripts/hub_bus_currency_monitor.py:200,312` · `scripts/agent_worktree_reaper.py:259` · `scripts/supervised_draft_deadman.py:69` · `scripts/memory_index_audit.py:526,533` · `scripts/render_gate/render_gate.sh:47` · `scripts/context_health_watchdog.py:1007` · `scripts/console_assign.py:91` · `scripts/cisco_reconcile_watch.py:126` · `scripts/lib/reset_audit_row.py:51` · `scripts/address_variant_miss_watchdog.py:188` · `scripts/irsyad_pii_containment_monitor.py:390` · `scripts/disk_autoremediate.py:619,713` · `scripts/queue_age_watchdog.py:199` · `scripts/programme_stall_guard.py:213` · `scripts/opus_reprobe_storefront.py:69` · `scripts/fire_fix1a_grant_ping.sh:21` · `scripts/op11911_syed_trigger.py:59` · `nervous_system/singleton_liveness.py:260,376,434` · `nervous_system/bug_report_channel_poll.py:87` · `nervous_system/wake_backstop_sweep.py:624,1084` · `nervous_system/lane_wedge_watchdog.py:915` · `nervous_system/media_reaper.py:98` · `nervous_system/commitment_sweeper.py:135` · `nervous_system/qwen_lane.py:253` · `nervous_system/weekly_limit_monitor.py:391` · `nervous_system/lane_watchdog.py:197` · `nervous_system/hub_self_recovery.py:284` · `ops/audit-chain-weekly/weekly_audit_chain_check.py:171` · `legacy/ihsanos_drain/main.py:24`.
Only ONE of these omits `priority` from its column list (multi-line-aware scan): `scripts/lane_nudge.sh:190`.

### Direct Telegram API callers (bypass `tg_send.sh`/`tg_out.py`)
Hub-token (`WINGMEN_BOT_TOKEN`), **unleased**: `scripts/health_check.sh:128`, `scripts/daily_backup.sh:80`. Other bots (own channels, by design): `scripts/nazim_send.sh`, `nazim_say.sh`, `nazim_send_file.sh`, `nazim_send_photo.sh`, `cai_send.sh`, `hk_send.sh`, `oeh_send_photo.sh`, `mamadah_send_photo.sh`, `angullia_send_photo.sh`, `cosem_tdu_support_send_photo.sh`, `irsyad_support_send_file.sh`, `reviewer_send_file.sh`, `dev_group_edit.sh`, `scripts/lib/tg_group_send.py`, `scripts/_tg_chunked_send.py`, `scripts/lane_watch.py`, `nervous_system/council_relay.py`, `nervous_system/council_summary.py`, `nervous_system/ingest.py` (poll), `scripts/check_send_paths_report_failure.py`.

---

### Findings (ranked by impact / effort)

#### [E-1] `.env.pre-*` backups in the live checkout are NOT gitignored; pre-commit has no secret scan
Impact: HIGH — four `.env.pre-*` files (full secret copies) sit untracked-but-unignored in `/Users/sheikhmusa/wingmen/orchestrator`; one `git add -A` / `git add .` from any body commits the fleet's entire secret set (same class as the op#11761 "always commit and push" near-miss that drove the token-pointer ignores).
Effort: S
Evidence: `.gitignore:4,23,24` (`.env`, `.env.bak*`, `.env.*.bak`); `git check-ignore .env.pre-rotation-20261001T150319Z` → not ignored (also `.env.local`, `.env.hub`, `.env.console`, `.env.production`); `scripts/git-hooks/pre-commit:1-51` contains no path/content check beyond the SRE-identity block.
Fix sketch: `.gitignore`: replace the three narrow lines with `.env*` + `!.env.example`. `pre-commit`: refuse any staged path matching `^\.env($|\.)` except `.env.example`, and run `scripts/hooks/secret_shape_patterns.py` over the staged diff (`git diff --cached -U0`), fail-closed. Add a test under `tests/` that stages a fake `.env.pre-x` and asserts the hook exits 1.

#### [E-2] `tg_out.enqueue()` is an unleased, identity-spoofing back door into pen (iv)
Impact: HIGH — the doctrine says the console is "fail-closed OUT" of the hub's operator channel (floors L173/186), but any process holding the substrate DSN (console, any lane, any launchd job) can `INSERT INTO tg_out (channel_key='operator-orch', …)` and the hub-token gateway will deliver it as @wingmennorchbot. The row is stamped `app.current_agent_id='cc-orchestrator'` regardless of who called.
Effort: M
Evidence: `nervous_system/tg_out.py:181-195` (no `orch_lease` import anywhere in the file; L185 hardcodes hub identity); only `deny_all_tg_out` RLS for `public` (`migrations/014_bot_channels_ingest.sql:76`) — service role bypasses; enqueue callers: `bug_report_channel_poll.py:164`, `responder_runner.py:82`, `front_desk_runner.py:200`.
Fix sketch: in `enqueue()`, when `channel_key` resolves to a hub-token channel (`bot_channels.token_env_key='WINGMEN_BOT_TOKEN'`), call `orch_lease.check()` and raise on non-holder; set `app.current_agent_id` from `scripts/lib/agent_identity` not a literal. Belt: BEFORE INSERT trigger on `tg_out` that rejects hub-channel rows whose `current_setting('app.current_agent_id')` ≠ `orch_lease.holder` (migration, applied via psycopg pattern).

#### [E-3] Two launchd jobs on the Mini send on the hub bot token with no lease check and no `operator_messages` log
Impact: HIGH — direct pen-(iv) violation from the console host, and a "forbidden surface" (R2: every operator surface logs). Both are loaded right now (`launchctl list`: `dev.wingmen.health-check`, `dev.wingmen.daily-backup`).
Effort: S
Evidence: `scripts/health_check.sh:128-130` (`curl …/bot$BOT_TOKEN/sendMessage -d chat_id=$CHAT_ID`, CHAT_ID = `MUSA_TELEGRAM_ID` L9); `scripts/daily_backup.sh:79-81` (same shape). Neither references `orch_lease` (grep count 0).
Fix sketch: replace both curls with `scripts/tg_send.sh "<text>" fleet` (already leased + logged) — `health_check.sh:110` already does this for its group alert, so it is a 3-line change each. Add a repo test: `grep -rlE 'api.telegram.org/bot\$?\{?WINGMEN' scripts nervous_system` must be exactly `{tg_send.sh, tg_send_file.sh, tg_out.py}`.

#### [E-4] Action-taking watchdogs run ungated by `fleet_health_lease` (pen iii) — "never two concurrent holders" not held
Impact: HIGH — on SRE lease expiry the hub reclaims pen (iii), but `idle-with-work-watchdog` (nudges lanes) and `sre-lane-recycle` (checkpoint→/clear→reboot) keep firing from the Mini launchd regardless of holder; two bodies can nudge/recycle the same lane.
Effort: M
Evidence: `scripts/idle_with_work_watchdog.py:38,126` (real `lane_nudge.sh` delivery; 0 `fleet_health_lease` refs; plist `launchd/dev.wingmen.idle-with-work-watchdog.plist`, loaded). `scripts/sre_lane_recycle.py` (0 lease refs; `dev.wingmen.sre-lane-recycle` loaded). Also ungated with actions: `checkpoint_recycle_driver.py`, `lane_recycle_deadman.py`, `nervous_system/lane_watchdog.py:197`, `supervised_draft_deadman.py`. Contrast gated siblings: `lane_proactive_recycle_nudge.py`, `queue_age_watchdog.py`, `context_health_watchdog.py`.
Fix sketch: add the same `fleet_health_lease.check(...)` preamble those siblings use (fail-closed on known non-holder, fail-safe on missing row) in front of the ACTION branch of each; keep detection + paging ungated per doctrine. Add `tests/test_fleet_health_lease_coverage.py` asserting every `scripts/*{watchdog,recycle,deadman,nudge}*.py` that imports `lane_nudge`/`reset_lane` also imports `fleet_health_lease`.

#### [E-5] `supabase db push` has no refusing gate anywhere
Impact: HIGH (incident class already realized: decision 962 arm-loss). The CLAUDE.md "never" is a sentence; the only code is a stderr *warning* in a script CI calls on migration files, not on the push path.
Effort: S
Effort note: a PreToolUse Bash hook is the cheapest.
Evidence: `scripts/check_additive_migration.py:456-493` (`_warn_if_supabase_db_push_context`, warning only; caller `.github/workflows/ci.yml:91` only); `scripts/hooks/*` 0 hits for `db push`.
Fix sketch: new `scripts/hooks/supabase_cli_guard.py` (PreToolUse `Bash`, wired in `.claude/settings.json`) refusing `supabase db push|reset|link` unless `--local`/shadow context; plus `scripts/lib/ensure_lane_deny.py` adding `Bash(supabase db push*)` to every lane's `permissions.deny`.

#### [E-6] `lane_nudge.sh:190` hand INSERT omits `priority` → "P1" blocker lands at P2 (bus #43651 recurrence)
Impact: MED — this exact row is a REVERT-FAIL blocker addressed to orch-console that sits below the hub wake floor; the whole reason the bus_send rule exists.
Effort: S
Evidence: `scripts/lane_nudge.sh:190-191` column list `(from_agent,to_agent,message_type,subject,body,requires_response)`; `supabase/migrations/20260420_arch036_priority_column.sql:19` `DEFAULT 'P2'`.
Fix sketch: add `priority` = `'P1'` to the column list (or route via `bus_send.py --priority P1 --req`). Structural: migration dropping the `'P2'` default on `agent_messages.priority` (NOT NULL stays) so every omitted-priority insert fails loudly; then fix the ~95 daemon inserts listed above incrementally (all but one already pass it).

#### [E-7] `audit_log` "INSERT-only hash chain" is not enforced at the DB
Impact: MED — floors L271 claims append-only; service-role connections can UPDATE/DELETE and silently re-write grant provenance.
Effort: S
Evidence: no trigger/RULE/REVOKE on `public.audit_log` in `migrations/*.sql`, `migrations/infra/*.sql`, `supabase/migrations/*.sql` (only `cc_cai_audit_log` / `ecosystem_audit_log` RLS in `migrations/013_substrate_rls_grant_lockdown.sql:97-151`).
Fix sketch: migration adding `CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON public.audit_log FOR EACH ROW EXECUTE FUNCTION raise_append_only()`; `REVOKE UPDATE, DELETE ON public.audit_log FROM PUBLIC, anon, authenticated, <console_ro role>`.

#### [E-8] Live-checkout protection is page-only; one script still `git pull`s it
Impact: MED — `post-checkout` always exits 0 (`:17-19`), `pre-commit` only blocks the SRE identity (`:29-40`), nothing blocks `git checkout/reset/pull` in `/Users/sheikhmusa/wingmen/orchestrator` for console/lane identities. `scripts/setup_mirror_wsl.sh:75` runs `git -C $HOME/wingmen/orchestrator pull --ff-only` (L23) — a mutation of the live tree outside the sanctioned deploy path.
Effort: M
Evidence: `scripts/git-hooks/post-checkout:17-19,22-36`; `scripts/git-hooks/pre-commit:26-40`; `scripts/setup_mirror_wsl.sh:23,75`.
Fix sketch: PreToolUse Bash hook refusing `git (checkout|switch|reset|pull|merge|rebase)` when `cwd` or `-C` resolves to the live checkout and `ORCH_BODY_ROLE != hub-deploy`; make `pre-commit` block ALL non-`fable/substrate-safe-fixes` commits in the live checkout (not just SRE). Delete/retarget the `setup_mirror_wsl.sh` pull.

#### [E-9] Console deploy gate can be skipped with `launchctl kickstart`
Impact: MED — `deploy_console.sh` has 4 fail-closed gates, but a bare `launchctl kickstart -k gui/501/dev.wingmen.hosted-console` serves the live checkout as-is with no gate. Rule is prose.
Effort: S
Evidence: 0 hits for `kickstart` in `scripts/hooks`, `scripts/lib`, `scripts/git-hooks`; `scripts/deploy_console.sh` is the only sanctioned path.
Fix sketch: PreToolUse Bash hook refusing `launchctl (kickstart|bootout|bootstrap|load|unload) .*hosted-console` unless env `CONSOLE_DEPLOY_GATE_PASSED=<content-hash>` is set by `deploy_console.sh` itself.

#### [E-10] `require_verified_authorization` covers 4 call sites; other irreversible ops do not call it
Impact: MED — token flips, body resets, lane kills (`switch_lane_token.sh`, `reset_*.sh`, `switch_singleton_token.sh`) are irreversible/money-adjacent and act on an in-console YES.
Effort: M
Evidence: callers = `scripts/deploy_console.sh`, `scripts/irsyad_residency_purge.py`, `scripts/stage_client_file.py`, `nervous_system/console/app.py` only.
Fix sketch: enumerate irreversible scripts in a `scripts/lib/irreversible_ops.list`; add a test asserting each sources the gate; wire the gate into `switch_*_token.sh` and `reset_{orch,cai,nazim}.sh`.

#### [E-11] Dead-letter rules in CLAUDE.md: "Always update STATUS.md", "log build ops to build_log", "deliverables in work_outputs"
Impact: LOW-MED — unenforceable prose that trains bodies to ignore the Rules block. `STATUS.md:1-5` explicitly says it is no longer current state (last commit 2026-09-07). `work_outputs` has no writer in hooks/lib/nervous_system; `build_log` only in `launch_dangerous_cc.sh`.
Effort: S
Evidence: grep `STATUS.md|work_outputs` in `scripts/hooks scripts/lib scripts/git-hooks nervous_system` → 0 enforcers.
Fix sketch: either delete the three rules from CLAUDE.md (and floors L269) or give each a mechanical check (e.g. `merge_gate.py` refusing a PR labelled `deliverable` without a `work_outputs` row keyed by PR number).

#### [E-12] `nazim_send.sh` has no body check — thread-ownership is one-directional
Impact: LOW — hub body can post on `nazim-console` (doctrine: "the hub stays off that thread").
Effort: S
Evidence: `scripts/nazim_send.sh` 0 refs to `ORCH_BODY_ROLE`/`orch_lease`.
Fix sketch: refuse when `ORCH_BODY_ROLE=hub` (or when `orch_lease.py check` succeeds — i.e. the caller IS the hub).

#### [E-13] No lane-boot refusal when the repo lacks a seeded `CLAUDE.md`
Impact: LOW (incident bus #43474 already happened once).
Effort: S
Evidence: `scripts/seed_lane_claude_md.py` exists; `scripts/launch_dangerous_cc.sh`/`scripts/lanes.sh` have no `[ -f "$CALLER_DIR/CLAUDE.md" ]` check.
Fix sketch: in `launch_dangerous_cc.sh`, fail with "run scripts/seed_lane_claude_md.py first" when the lane dir has no `CLAUDE.md` and `fleet_lanes.first_boot` is true.

#### [E-14] `strategic_decisions` "never SELECT *" is unenforced
Impact: LOW (context-bloat rule, not safety).
Effort: S
Evidence: 0 refs in `scripts/hooks`; no restricted view.
Fix sketch: Bash/MCP PreToolUse hook regex `select\s+\*\s+from\s+strategic_decisions` → refuse with pointer to `boot_briefing`/`get_decision`.

### Checked and found ENFORCED (no action)
- Lane `tag=` requirement: `nervous_system/operator_log.py:242-247,958-963`.
- Console never drives irsyad execution: `scripts/hooks/console_irsyad_guard.py` wired in `.claude/settings.json`.
- Consult-before-assert: `scripts/hooks/consult_governance_floor_guard.py` wired.
- `restart_orch.sh` refuses nohup: `:6-17` + `tests/test_restart_script.py`.
- `tg_send.sh`/`tg_send_file.sh` lease gate: `scripts/tg_send.sh:21-25`.
- SRE boundaries: `fleet_health_boundaries.assert_*` at `context_health_watchdog.py:2021`, `a3_invariant_sink.py:35`, `checkpoint_recycle_driver.py:162,335`, `lane_proactive_recycle_nudge.py:239`, `lane_selfrecycle_detect.py:333`.
- cai advisory-only: no code hard-blocks on a cai grant; `trg_agent_messages_cai_gate` only blocks messages TO cai for cai-off projects (consistent).
- `core.hooksPath` configured (absolute, shared) in both the live checkout and this worktree.
- No real secrets in tracked files (all key-shaped hits are test fixtures / `scripts/scratch_shape_replay.py` fakes).

---

# 3. Launchd (Mac Mini) + gzb systemd jobs (auditor C)

## C — launchd (Mac Mini) + gzb systemd audit — 2026-10-28 (read-only)

Scope: `~/Library/LaunchAgents/` = 132 entries: 110 `*.plist` (108 parsed; 2 are Google keystone w/o Label), 21 `.bak/.disabled/.moved` files + 1 `retired-op27235/` dir. Repo `launchd/` = 60 tracked plists. 95 of 108 plists are loaded. gzb (`ssh gzb` → `gzbai`, user `gazzai`) reachable; 15 wingmen systemd services + 3 timers + 8 cron lines.

Parsing note: 17 installed plists contain raw `&&` inside `<string>` — launchd/plutil accept it, Python `plistlib` rejects it. 2 plists are genuinely malformed (`plutil -lint` fails): `dev.wingmen.remind-musa-20261015.plist` (line 11, unescaped `&` in a `bash -lc` string) and `dev.wingmen.tailscale-up.plist` (line 16, `2>&1`). tailscale-up is nonetheless loaded (launchd is lenient); remind-musa is not loaded.

### 1. Missing-script / interpreter / workdir jobs

| label | missing path | loaded? | last exit |
|---|---|---|---|
| `dev.wingmen.orchestrator` | `/Users/sheikhmusa/wingmen/orchestrator/wingmen_orch.py` (moved to `legacy/` in commit d6c2630, PR #91) | no (not in `launchctl list`) | — |
| `dev.wingmen.watchdog` | `/Users/sheikhmusa/wingmen/orchestrator/watchdog.py` (same legacy move) | no (explicitly `disabled`) | — |
| `com.cosem.powersync` | `/Users/sheikhmusa/wingmen/projects/cosem-port-lane/scripts/offline/local-stack.sh` | **yes, KeepAlive** | **126, runs=34322** — respawn loop every ThrottleInterval since the script vanished |
| `dev.wingmen.adcda-coverage-host` | `/Users/sheikhmusa/wingmen/projects/caai-lane/.venv/bin/python` (called from line 40 of `scheduler/host_coverage.sh`) | yes (weekly Mon 06:15) | 1, runs=9 |
| `dev.wingmen.remind-musa-20261015` | n/a — plist itself unparseable, never fired, never self-deleted | no | — |

All other ProgramArguments, `.venv` interpreters, WorkingDirectories and log directories exist. No installed plist references `orchestrator.wt-*`, `/Volumes`, or a MacBook path.

### 2. Loaded-but-failing (non-zero LastExitStatus)

| label | exit | runs | diagnosis (from stderr) |
|---|---|---|---|
| `com.cosem.powersync` | 126 | 34322 | script gone (above). KeepAlive hot-loop; `/tmp/cosem-powersync-agent.log` fills with "No such file" |
| `dev.wingmen.irsyad-pii-containment-monitor` | 1 | 1469 | hourly; log `logs/irsyad-pii-containment-monitor.out.log` last written **Aug 28** (psycopg2 "Network is unreachable" to `aws-1-ap-southeast-*.pooler.supabase.com`). ~1400 runs since produced nothing in the log yet exit 1 → exits before logging (likely env/DSN gate). A P0 PII containment monitor has been silently non-functional ~2 months. |
| `dev.wingmen.adcda-coverage-host` | 1 | 9 | missing caai-lane venv (above) |
| `dev.wingmen.memory-index-audit` | 1 | 61 | by design? prints "3 agent memory dir(s) need attention" + posts advisory; exit 1 = findings. Benign but noisy in `launchctl list`. |
| `dev.wingmen.a3-isolation-check` | 1 | 62 | script `return 2`/exit 1 on `gate_status: pending` (fail_count 159). Behavioural, not infra. |
| `dev.wingmen.asks-reconcile` | 1 | 20 | `asks_reconcile_daily.py:460 return 1` after a caught exception (log shows normal PARKED output, no traceback visible → swallowed). Worth a look, not infra-broken. |
| `dev.wingmen.politemall-notes` | 2 | 36 | `re-login failed (BROWSER_ERROR)` nightly since session expiry — credential/browser issue in wingmen-personal |
| `dev.wingmen.weekly-alert-relay` | 120 | 2 | currently running; last crash was a Python exception at shutdown (`logs/weekly-alert-relay.err`, Oct 22). Low. |
| `dev.wingmen.agent-wake-subscriber` | 1 | 31 | running now; 31 restarts since boot — realtime WS drops + KeepAlive restart. Expected pattern, but 31 is high. |
| `-15` exits (fleet-console, finance/fleet-health-bus-notify, front-desk, nazim-ingest, backlog-done-checker, coord-pane-publisher, wake-backstop-sweep, hosted-console, sensitive-access-monitor, shipforge.worker) | -15 | — | SIGTERM = manual kickstart/restart; all running now. Fine. |

Unloaded-but-present (plist on disk, not in `launchctl list`): `cc-orch` (hub moved to gzb — correct), `cc-orchestrator.scheduled` (disabled), `fix1a-grant-ping` (one-shot, done), `fleet-stall-watch` (disabled), `irsyad-ingest` (enabled in print-disabled but NOT loaded — irsyad ingest now runs on gzb `wingmen-ingest`; stale plist), `lane-watch` (disabled), `lane-watchdog` (disabled), `orchestrator` (legacy), `tripwire-72h` (disabled), `watchdog` (disabled), `wingmendev-bot` (not loaded).

### 3. Drift — repo `launchd/` vs installed

- **Tracked but not installed (2):** `launchd/dev.wingmen.glm-peak-pause-resume.plist` (PR #407), `launchd/dev.wingmen.sentry-new-error-watch.plist` (op#19859 "INERT until armed"; launchd still has it `enabled` in print-disabled but the file is gone → was installed once then removed).
- **Installed but not tracked (48):** a3-isolation-check, adcda-coverage-host, adcda-port-watch, arabic-ocr, cc-orch, cc-orchestrator.scheduled, coord-pane-publisher, daily-backup, disk-space-monitor, file-history-prune, fix1a-grant-ping, fleet-console, fleet-health, fleet-health-bus-notify, fleet-stall-watch, front-desk, gazzabyte-autopublish, health-check, irsyad-ingest, irsyad-pii-containment-monitor, lane-recycle-deadman, lane-selfrecycle-detect, lane-watch, lane-watchdog, log-rotate, media-cleanup, nazim-bus-notify, nazim-ingest, nazim-session, op11911-syed-trigger, orchestrator, politemall-notes, remind-musa-20261015, repo-context-watchdog, second-brain-nightly, sre-liveness, tailscale-up, tripwire-24h/48h/72h, video-retention, watchdog, wingmendev-bot, com.wingmen.sensitive-access-monitor, com.wingmen.shipforge.tunnel/worker, com.cosem.mirror/powersync. Several are core singletons (`nazim-session`, `fleet-console`, `fleet-health`, `front-desk`, `nazim-ingest`, `sre-liveness`) — their live config has no git history.
- **Content drift (2 of 58 common):**
  - `dev.wingmen.lane-proactive-recycle-nudge`: installed adds `export PROACTIVE_RECYCLE_NUDGE_ENABLED=1` (armed live, repo says unarmed).
  - `dev.wingmen.secret-hash-sweep`: repo `StartInterval=7200`, installed `14400` + `ProcessType=Background`.
- **Host-path portability:** 7 wrappers hard-code `cd /Users/sheikhmusa/wingmen/orchestrator` instead of `$HOME` (`scripts/run_adcda_fn_error_bus_watch.sh:9`, `run_glm_peak_pause_resume.sh:10`, `run_glm_overflow_switch.sh:11`, `run_ci_queue_watchdog.sh:8`, `run_deploy_provenance_watch.sh:7`, `run_sentry_new_error_watch.sh:8`, `run_cubeasht_monitor.sh:9`); also `scripts/audit_drain_sweep.sh` `ORCH_DIR="/Users/sheikhmusa/wingmen/orchestrator"` and `scripts/watchdog_tripwire_report.py` `load_dotenv("/Users/sheikhmusa/...")`. `scripts/lanes.sh`, `scripts/launch_dangerous_cc.sh`, `scripts/boot_*.sh` are clean (`$HOME` / `dirname $0`, claude/tmux resolved via fallback list). gzb checkout `/home/gazzai/wingmen/orchestrator` is on `fable/substrate-safe-fixes` @ 7bdcc56 (Oct 27) — matches live-checkout doctrine.

### 4. Overlap groups

| group | members (interval) | verdict |
|---|---|---|
| **Lane recycle/bloat** | `auto-recycle-on-bloat` (300s, Stage-0 detect-only, gauge-based, idle-gated) · `lane-selfrecycle-detect` (600s, Stage-0 detect-only, **pane**-based; docstring says it exists to fix two bugs in `sre_lane_recycle`) · `sre-lane-recycle` (300s, `--no-arm` → detect-only) · `lane-proactive-recycle-nudge` (300s, armed, busy-inclusive nudge) · `lane-recycle-deadman` (300s, loud backstop) | **Three detect-only detectors of the same condition** (auto-recycle-on-bloat, lane-selfrecycle-detect, sre-lane-recycle --no-arm) read the same lanes every 5–10 min and fire nothing. Recommend: keep ONE armed executor (`sre_lane_recycle`, fold in selfrecycle-detect's pane-read fix) + `lane-proactive-recycle-nudge` + `lane-recycle-deadman`; retire `auto-recycle-on-bloat` and `lane-selfrecycle-detect` plists. |
| **Lane stall/wedge** | `lane-wedge-watchdog` (60s, armed nudge) · `idle-with-work-watchdog` (900s) · `lane-watch` (disabled, 2026-06 era, "cheap watcher for cc-orchestrator") · `lane-watchdog` (disabled, CAI-284 unsent-input submitter) · `fleet-stall-watch` (disabled, zombie-lane detector) | lane-wedge-watchdog + idle-with-work are complementary (wedge vs idle-with-owned-work). The three disabled ones are dead code-paths with plists still on disk. Also gzb cron `lane_wedge_watchdog_tick.sh` every 7 min (coord self-keepalive, alert-only) overlaps the Mini's 60s wedge watchdog for gzb-hosted lanes — confirm host scoping. Retire lane-watch / lane-watchdog / fleet-stall-watch plists. |
| **Liveness/health** | `health_check.sh` (300s, HTTP pings → Telegram) · `fleet_health.py` (600s, heartbeat→offline, reap, reconcile, seq-drift) · `sre-liveness` (180s, SRE composer-wedge) · `singleton-liveness` (300s, singleton tmux death) · `hub-self-recovery-staleness-watchdog` (120s, reads gzb tick marker) · `channel-liveness-watchdog` (120s, TG poll health) · `watchdog` (disabled, legacy file missing) | Distinct signals each (process-dead vs wedge vs heartbeat vs poll). Not duplicates, but 6 timers at 2–10 min on one box. `health_check.sh` still greps BOT_TOKEN/CHAT_ID from `.env` directly (pre-tg_out pattern) — bypasses the lease-gated tg_send path. Retire `dev.wingmen.watchdog.plist`. |
| **Media** | `media-cleanup` (daily 04:30, `cleanup_media.py --apply --purge-archive-older-than 60`, retention for `logs/tg_media`) · `media-reaper` (600s, TG re-fetch of media bytes, channel-pinned) · `video-retention` (daily 03:30, wingmen-personal `video_pipeline.cli cleanup`) | Different stores/functions; names mislead. Keep; rename media-reaper → `tg-media-fetch` would end the confusion. Note gzb also runs `wingmen-media-reaper.timer` (10 min) — pinned per-host by channel list, by design. |
| **Disk** | `disk-space-monitor` (600s, page only) · `disk-autoremediate` (300s, `--apply`, tiered reclaim) · `agent-worktree-reaper` (6h) · `worktree-nodemodules-reaper` (30m) · `node-modules-content-dedup` (1h hardlink) · `preview-devserver-reaper` (30m) · `file-history-prune` (600s) | Monitor and autoremediate overlap on detection (both read DATA-volume free); autoremediate also pages. The 4 reapers target disjoint dirs (`.claude/worktrees`, `*.wt-*/node_modules`, cross-tree dedup, dev-server `.next`) — defensible but could be one `disk_hygiene` plist with subcommands. Merge disk-space-monitor into disk-autoremediate (it already pages on floor breach). |
| **Queue/SLA** | `queue-age-watchdog` (300s, coord_dispatch_queue unclaimed) · `ci-queue-watchdog` (300s, GitHub Actions self-hosted runner stall) · `priority-sla-watchdog` (90s, agent_messages SLA re-nudge) | Three different queues. Keep. 90s on priority-sla is aggressive (see §5). |
| **Orch bodies** | `cc-orch` (unloaded; hub now gzb `wingmen-orch-hub.service`) · `cc-orchestrator.scheduled` (disabled, `claude -p` inbox sweep) · `orchestrator` (unloaded, legacy `wingmen_orch.py` missing) · `nazim-session` (KeepAlive, live console body) | Only nazim-session is live and correct. The other three are dead plists — remove. |
| **Tripwires** | `tripwire-24h` / `-48h` (loaded, StartCalendarInterval 2026-05-27/28 — past) · `tripwire-72h` (disabled) | One-shot CAI-RESP-168 reports from May; same script parameterised by `--window`. All three can go. |
| **bus-notify** | `cai-bus-notify` / `finance-bus-notify` / `fleet-health-bus-notify` / `nazim-bus-notify` — each a 3-line `boot_*.sh` → `python -m nervous_system.<x>_bus_notify` | Four separate modules + four KeepAlive processes + four plists, differing only in target agent + nudge mechanism (nudge_cai.sh vs lane_nudge.sh vs nazim tmux). Also the realtime `agent-wake-subscriber` already fires on every directed row. Merge into one `bus_notify --agent X` module (one plist per target or one process fanning out) or fold into agent_wake. |
| **Ingest** | `nazim-ingest` (Mini, 12 channels pinned) · `irsyad-ingest` (Mini plist, not loaded) · gzb `wingmen-ingest.service` (hub channels) | irsyad-ingest Mini plist is stale — irsyad coord lives on gzb now (`wingmen-irsyad-coord.service`). Remove plist. |
| **Cross-host pairs (both hosts run one)** | agent-wake-subscriber, wake-backstop-sweep, coord-pane-publisher, media-reaper | By design (host-scoped by `FLEET_HOST_ID` / channel pin). OK. |

### 5. Litter + one-offs + aggressive timers

**21 litter files + 1 dir in `~/Library/LaunchAgents/` (not loaded, confusing):**
`context-health-watchdog.plist.bak-prearm-20260801` (Aug 1) · `fleet-console.plist.pre-rotation-bak` (Oct 1) · `irsyad-shadow.plist.disabled-obsolete-shadow-op14580` (Jul 25) · `lane-proactive-recycle-nudge.plist.bak-fleethealth-disarm-20261012T0125Z` (Oct 12) · `lane-wedge-watchdog.plist.bak-{alertonly-1445,drawdown-0535,excludefix-1618,prearm-2316,rearm-1509,removeexclude-1627}` (6 files, Aug 12–Oct 25) · `mizan-bot.plist.bak.20260620-claudeauth` + `mizan-bot.plist.moved-to-gzb-20260914` (Jun 20) · `op23364-musa-trigger.plist.disabled-op23681` (Sep 30) · `orchestrator.plist.bak-preMeteredScrub-20260926T121015Z` (Apr 16 mtime) · `priority-sla-watchdog.plist.bak-{hk-unpause-0038,prearm-2316,predrawdown-op11899,quiesce-20261007,repause-0416,sla-disarm-0857}` (6 files, Aug 12–Oct 7) · `queue-age-watchdog.plist.bak-prearm-1018` (Sep 17) · `retired-op27235/dev.wingmen.opus-reprobe.plist` (Aug 19).
Also `launchctl print-disabled` still carries `dev.wingmen.tg-bridge` and `dev.wingmen.cai-bridge` (deleted Sep 5, PR #85) and `dev.wingmen.sentry-new-error-watch` (no plist).

**Expired one-offs still installed:**
- `dev.wingmen.op11911-syed-trigger` — **loaded, runs every 300s**, docstring: "TEMPORARY: bootout after the Syed reset ~10:00 UTC 2026-08-12". Still polling pool_usage 2.5 months later (log: `Syed pct_7d=43% threshold=100%`; threshold raised to 100 so it can never fire). Pure waste.
- `dev.wingmen.tripwire-24h` / `-48h` loaded with May 2026 calendar dates (will never fire again); `-72h` disabled.
- `dev.wingmen.fix1a-grant-ping` (unloaded, CAI-774 one-shot, done).
- `dev.wingmen.remind-musa-20261015` (Oct 15, malformed → never fired; the reminder was never delivered).
- `dev.wingmen.op23364-musa-trigger.plist.disabled-op23681` (litter).
- gzb cron: `irsyad_a3_pgrst202_bapa_watch.py` hourly, comment says "REMOVE after 24h (console #45911, hc#89)" — log is 40KB, still running, every tick "clean: 0".

**Aggressive / costly timers:**
- ≤120s: `lane-wedge-watchdog` 60s · `live-checkout-branch-watchdog` 60s · `pooler-breaker-snapshot` 60s · `priority-sla-watchdog` 90s · `channel-liveness-watchdog` 120s · `gazzabyte-autopublish` 120s · `hub-self-recovery-staleness-watchdog` 120s. All hit Supabase each tick (7 jobs ≈ 5 DB round-trips/min baseline from timers alone). `pooler-authfail-tripwire` (900s) has written 400KB to both `.log` and `.err.log` (every tick logs to stderr too).
- **LLM on a timer:** `cc-quality-audit-drain` + `cc-storefront-audit-drain` (both 43200s) spawn an **opus-4-8** Claude Code session via `launch_dangerous_cc.sh` when `decision_audits` has open rows (pre-filter + heartbeat on empty tick; fail-OPEN → spawns on DB error). `cc-orchestrator.scheduled` (`claude -p`) is disabled. KeepAlive LLM bodies: `nazim-session` (console Claude), gzb `wingmen-orch-hub` / `wingmen-irsyad-coord` / `wingmen-irsyad-tabung-jumaat`. `audit_drain_sweep.sh` header says the bespoke storefront wrapper was to be retired once generic proven — both still live.

### 6. gzb (VPS hub) findings

Reach: `~/.ssh/config` `Host gzb` → 100.77.251.8 (tailnet), user `gazzai`, key `~/.ssh/gzb_fleet`. `ssh gzb hostname` → `gzbai`. Repo refs: `ssh gzb` (3), `GZB_SSH=` in scripts; runbook mentions `ssh wingmen-core -> sudo gzb-vpn.sh up -> sudo -u wingmen ssh gzb` (older path).

System units (`/etc/systemd/system`, all ExecStart/WorkingDirectory paths **exist**, 0 failed wingmen units, 1 err-level journal line in 24h):
- active/running: `wingmen-orch-hub` (→ `/home/gazzai/orch_supervisor.sh`, untracked, restarts=1) · `wingmen-irsyad-coord` (→ `coord_supervisor.sh`, untracked) · `wingmen-irsyad-tabung-jumaat` (→ `tabung_supervisor.sh`, untracked) · `wingmen-ingest` · `wingmen-tg-out` · `wingmen-agent-wake-subscriber` (restarts=1) · `wingmen-wake-backstop-sweep` (restarts=2) · `wingmen-body-activity-oracle-vps` · `coordinator-pane-publisher` · `hub-ctx-publish` · `irsyad-exec-broker` (`/opt/irsyad-exec-broker/`, the /opt copy) · 2× GitHub Actions runners.
- oneshot/`exited`: `wingmen-fetch-secrets` (→ `/dev/shm/wingmen-secrets/.env`, present).
- timer-driven (inactive between ticks, normal): `wingmen-hub-self-recovery` (2m) · `wingmen-media-reaper` (10m) · `wingmen-orch-lease-renew` (4m). Note `wingmen-orch-lease-renew` sources `. .env` from the checkout while every other unit sources `/dev/shm/wingmen-secrets/.env` — inconsistent secret source.
- `systemctl --failed`: 9 failed transient `run-p*.service` + `paperclip-canary-onboard.service` from `/opt/paperclip-canary/run-paperclip-vps.sh` — not wingmen, but litter in `--failed`.
- Litter: `wingmen-wake-backstop-sweep.service.bak-predryrun-arm`, `ihsanos-runner.service.bak-20261004T115339Z`, `actions.runner...bak-20261004T115340Z` in `/etc/systemd/system/`.
- cron (`gazzai`): 8 lines, all script paths exist — autoscaler_tick (20m), irsyad_confirm_executor_tick (3m), irsyad_worker_reaper_tick (5m), gated_inbound_digest (daily), **irsyad_a3_pgrst202_bapa_watch (hourly, "REMOVE after 24h")**, irsyad_exec_reconcile_tick (15m), weekly_audit_chain_check (Mon), lane_wedge_watchdog_tick (7m). Five untracked `/home/gazzai/*.sh` supervisors/ticks carry hub logic with no git history (boot_orch.sh:123 acknowledges `orch_supervisor.sh` is untracked).
- No user-level (`--user`) units.

### 7. Ranked findings

#### [L-1] `com.cosem.powersync` KeepAlive hot-loop on a deleted script (34,322 respawns)
Impact: continuous launchd churn + `/tmp/cosem-powersync-agent.log` growth; masks real failures in `launchctl list`.
Effort: S
Evidence: `launchctl print gui/501/com.cosem.powersync` → `runs = 34322, last exit code = 126`; `/Users/sheikhmusa/wingmen/projects/cosem-port-lane/scripts/offline/local-stack.sh` does not exist.
Fix sketch: bootout + delete `~/Library/LaunchAgents/com.cosem.powersync.plist` (and review `com.cosem.mirror`, same project).

#### [L-2] `dev.wingmen.irsyad-pii-containment-monitor` has exited 1 on every hourly run since ~Aug 28 (1469 runs) without logging
Impact: the CAI-RESP-1060 "deep-field-zero" P0 containment assertion for 890 minors has not actually run for ~2 months; nobody paged because the dead-man is the monitor itself.
Effort: S (diagnose) / M (add dead-man)
Evidence: `launchctl print` runs=1469 exit=1; `logs/irsyad-pii-containment-monitor.out.log` mtime Aug 28 08:42 (last content: psycopg2 "Network is unreachable"); script `return 1` at line 710.
Fix sketch: run it by hand once (as the irsyad-coord lane, not console — key separation) to get the real error; add a `monitor_heartbeat` stamp + a `fleet_health.py` check that pages when the stamp is >2h old.

#### [L-3] 48 installed plists have no tracked source; 2 tracked plists drifted live (armed / interval)
Impact: machine-loss or re-provision cannot rebuild the Mini's job set; `lane-proactive-recycle-nudge` is ARMED live but the repo says unarmed — exactly the "process by promise" drift CLAUDE.md forbids.
Effort: M
Evidence: §3 lists; diff shows `export PROACTIVE_RECYCLE_NUDGE_ENABLED=1` only in installed copy; `secret-hash-sweep` 7200 vs 14400.
Fix sketch: `git add` the 48 live plists into `launchd/` (scrub nothing secret — they only hold paths); commit the two drifts; add a `scripts/launchd_drift_check.py` to `fleet_health.py` step list that diffs `launchd/*.plist` vs installed and pages on divergence.

#### [L-4] Expired one-off jobs still running or installed (op11911 every 5 min since Aug 12; tripwires from May; a3 bapa cron "remove after 24h")
Impact: wasted DB polls, confusing inventory, and `remind-musa-20261015` silently never delivered (malformed XML).
Effort: S
Evidence: `scripts/op11911_syed_trigger.py` docstring "bootout after ... 2026-08-12"; log shows `threshold=100%` (can never fire); `tripwire-24h/48h` StartCalendarInterval Year 2026 Month 5; gzb crontab line `17 * * * * ... irsyad_a3_pgrst202_bapa_watch.py # REMOVE after 24h`; `plutil -lint dev.wingmen.remind-musa-20261015.plist` fails.
Fix sketch: bootout+rm op11911, tripwire-24h/48h/72h, fix1a-grant-ping, remind-musa, orchestrator, watchdog, cc-orch, cc-orchestrator.scheduled, lane-watch, lane-watchdog, fleet-stall-watch, irsyad-ingest, wingmendev-bot; remove the bapa cron line on gzb. Add an `ExpiresAt` convention: one-shot plists carry a comment date and `fleet_health.py` pages when past.

#### [L-5] Three detect-only recycle detectors poll the same lanes every 5–10 min; four near-identical bus-notify daemons
Impact: ~6 extra DB polls/5 min, three codepaths to keep in sync (selfrecycle-detect exists to patch bugs in sre_lane_recycle rather than fixing it), 4 KeepAlive processes for one function.
Effort: M
Evidence: docstrings of `scripts/auto_recycle_on_bloat.py` ("STAGE 0 FIRES NOTHING"), `scripts/lane_selfrecycle_detect.py` ("fixes two root-cause bugs in sre_lane_recycle.py"), `sre-lane-recycle` plist `--no-arm`; `scripts/boot_{cai,finance,fleet_health,nazim}_bus_notify.sh` each 3 lines → `nervous_system.<x>_bus_notify`.
Fix sketch: port selfrecycle-detect's pane-based read into `sre_lane_recycle.py`, retire the two detect-only plists; collapse the four `*_bus_notify` modules into `nervous_system/bus_notify.py --agent <id>` (or fold into `agent_wake_subscriber`, which already sees every directed row).

#### [L-6] 21 `.bak/.disabled/.moved` plists + `retired-op27235/` dir in `~/Library/LaunchAgents/`; stale `print-disabled` entries for deleted bridges
Impact: operator/agent confusion when eyeballing the dir (132 entries for ~95 live jobs); six historical variants of lane-wedge-watchdog and priority-sla-watchdog each.
Effort: S
Evidence: §5 list with mtimes; `launchctl print-disabled` lists `dev.wingmen.tg-bridge`, `dev.wingmen.cai-bridge` (deleted PR #85).
Fix sketch: move all `.bak*`/`.disabled*`/`.moved*` to `~/wingmen/orchestrator/launchd/_retired/` (or delete — the repo should be the history), `launchctl enable` cleanup is cosmetic. Same for `/etc/systemd/system/*.bak-*` on gzb.

#### [L-7] Hub-side supervisors and tick scripts on gzb are untracked (`/home/gazzai/{orch,coord,tabung}_supervisor.sh`, 5 cron ticks)
Impact: the hub's boot path and the irsyad autoscaler/executor/reaper loops cannot be reviewed, diffed or re-provisioned from git; `boot_orch.sh:123` acknowledges the drift.
Effort: M
Evidence: `systemctl cat wingmen-orch-hub` → `ExecStart=/bin/bash -lc '/home/gazzai/orch_supervisor.sh'`; crontab references `/home/gazzai/*_tick.sh`; none under `/home/gazzai/wingmen/orchestrator/`.
Fix sketch: copy into `scripts/gzb/` + `systemd/` in the repo, point units/cron at the checkout paths, add to the same drift check as L-3. Also unify `wingmen-orch-lease-renew` to source `/dev/shm/wingmen-secrets/.env` like its peers.

#### [L-8] Nine wrappers hard-code `/Users/sheikhmusa/wingmen/orchestrator`
Impact: those jobs break verbatim on gzb (`/home/gazzai/...`) or any re-homed Mini; inconsistent with the `$HOME` convention the other 30 wrappers use.
Effort: S
Evidence: `scripts/run_{adcda_fn_error_bus_watch,glm_peak_pause_resume,glm_overflow_switch,ci_queue_watchdog,deploy_provenance_watch,sentry_new_error_watch,cubeasht_monitor}.sh` `cd /Users/sheikhmusa/wingmen/orchestrator`; `scripts/audit_drain_sweep.sh` `ORCH_DIR=` literal; `scripts/watchdog_tripwire_report.py` `load_dotenv("/Users/...")`.
Fix sketch: `cd "$(dirname "$0")/.."` or `$HOME/wingmen/orchestrator`; one sed pass.

#### [L-9] Seven timers at ≤120s plus two 12-hourly opus Claude spawns; `health_check.sh` bypasses the lease-gated Telegram path
Impact: baseline DB load from timers alone (~5 round-trips/min) on a box that also hosts the console; audit-drain fail-OPEN spawns an opus session on a DB error; `health_check.sh` greps `WINGMEN_BOT_TOKEN`/`MUSA_TELEGRAM_ID` from `.env` and posts directly — ORCH-TOPOLOGY-001 pen (iv) is enforced in `tg_send`, not here.
Effort: S–M
Evidence: §5 interval list; `scripts/audit_drain_sweep.sh:80` "N or ERR -> fail-open (spawn)"; `scripts/health_check.sh` lines 4–6.
Fix sketch: raise `pooler-breaker-snapshot`/`live-checkout-branch-watchdog` to 300s unless a documented SLA needs 60s; flip audit-drain to fail-closed on `ERR`; route `health_check.sh` alerts through `scripts/tg_send.sh` (lease-gated) or retire it in favour of `fleet_health.py`.

#### [L-10] Two malformed plists on disk; 17 more only parse under launchd's lenient XML reader
Impact: any tooling that reads plists with `plistlib` (drift check, inventory) will choke; `remind-musa` already silently failed to load.
Effort: S
Evidence: `plutil -lint` fails on `dev.wingmen.remind-musa-20261015.plist` (line 11) and `dev.wingmen.tailscale-up.plist` (line 16 `2>&1`); `plistlib` rejects 19/110.
Fix sketch: escape `&` as `&` in the 19 files (or move shell one-liners into `scripts/*.sh` wrappers, the pattern the rest already use); add `plutil -lint launchd/*.plist` to CI.

---

# 4. Sleeping failure modes in Python (auditor D)

## D-python — sleeping failure modes in the orchestrator Python code

Read-only audit, worktree `/Users/sheikhmusa/wingmen/orchestrator.wt-fable-3` @ `7f10ed1` (2026-10-28). Nothing run except grep/sed/pyflakes; all paths relative to the worktree. Scope: `nervous_system/` (84 modules), `scripts/` (143), `scripts/lib/` (51). `wingmen_orch.py` no longer exists at repo root — it is `legacy/wingmen_orch.py`; the launchd plist pointing at the old path is kept UNLOADED (plist comment), so it and everything only it imports (`council_executor`, `swallowed_except_harness`, `error_tracker`) are out of the live path.

Ranking: Impact = how badly the fleet is fooled ("gate passed" when it didn't / "paged" when nobody was / wrong identity or store). Effort S < 1h, M < 1 day, L > 1 day. Line numbers were spot-checked against source for every P-item.

---

### Top 25

#### [P-1] Musa-key FREEZE refusal (exit 42) is swallowed by the lane launcher — lane boots on the Musa account anyway
Impact: high · Effort: S
Evidence: `scripts/launch_dangerous_cc.sh:164` — `_LANE_DEFAULT_TOKFILE="$(... -m scripts.lib.lane_token_resolver --session "$_LANE_SESSION" 2>/dev/null || true)"`; vs `scripts/lib/lane_token_resolver.py:352-358` — `REFUSE(42) ... "a DISTINCT non-zero code (42) that a launcher can test to ABORT the boot rather than fall through to the .env Musa account"`; `:348-350` — `except Exception: # Fail-open at the boundary too ... path = None`; `:231-235` — `_musa_freeze_active`: `except Exception: return False # absent / unreadable => NOT active`.
Why it sleeps: `2>/dev/null || true` discards both the exit code and the stderr reason; empty output → `[ -n ... ]` false → falls through to `.env CLAUDE_CODE_OAUTH_TOKEN` (Musa) — the exact outcome the freeze exists to prevent — with no log line; an unreadable flag file is a second fail-open on the same control.
Fix sketch: capture `rc=$?`, abort on 42, drop `|| true`, keep stderr; in the resolver distinguish `FileNotFoundError` (inactive) from other `OSError` (fail closed).

#### [P-2] Watchdogs latch "paged"/"alerted"/"escalated" even when the send FAILED — page-once logic then never retries
Impact: high · Effort: S (per site)
Evidence: `scripts/priority_sla_watchdog.py:2053-2062` — `ok = send_page(page_text, dry); pages_this_run += 1; rec["paged"] = True ... audit(conn, "[sla-watchdog] PAGED operator re msg #{mid}", ...)` (audit written regardless of `ok`; `already_paged_on_bus()` `:784` then matches it); `scripts/context_health_watchdog.py:1626-1629` and `:874-878` — `subprocess.run([... "nazim_send.sh", text], timeout=30, cwd=...)` returncode never read, callers latch `backstop_paged=True` (`:1751`), `rec["paged"]=True` (`:1898`), `gauge_unreachable_paged=True` (`:2001`); `nervous_system/lane_wedge_watchdog.py:947-961` `_page` returns None, callers stamp `entry["alerted"]=now` (`:1762-1764`, `:1590-1593`, `:1735-1737`, `:1522-1536`); `nervous_system/singleton_liveness.py:406-408` — `_page(...); state[agent]["paged_dead"] = True` (`_page` `:265-266` logs "WARN page failed" only); `nervous_system/lane_watchdog.py:187,307-309` — `_escalation_count += 1` before the insert, `held_escalated=True` though `escalate()` swallows bus failures.
Why it sleeps: every safety page in the fleet is "best-effort + latch"; `nazim_send.sh` fails silently off the hub host, so a watchdog on the wrong host records a clean "PAGED" audit trail while the operator hears nothing.
Fix sketch: make every `_page`/`send_page` return bool; stamp `paged`/`alerted`/write the PAGED audit row ONLY on True; on False write "PAGE FAILED" and leave the record eligible.

#### [P-3] `fleet_health_lease.gate()` / `orch_lease.check()` ALLOW on DB error, missing row, or missing `.env` — reachable by any non-holder with no env
Impact: high · Effort: M
Evidence: `scripts/lib/fleet_health_lease.py:183-189` — `except Exception as exc: ... print("unreadable ... allowing"); return True, "lease-unreadable-failsafe"`; `:106` `if row is None: return True, "lease-missing-failsafe"`; `:109` `if holder is None: return True, "holder-null-failsafe"`; `:83-89` `_agent_id()` defaults to `SRE_AGENT_ID`; `:111` `host_ok = holder_host is None or holder_host == host`. Same shape `scripts/lib/orch_lease.py:263-272` (`lease-unreadable-failsafe`, `lease-missing-failsafe`), `:77-78` `load_dotenv(os.path.join(ROOT, ".env"))` silently no-ops when absent, `:253` only `console` role is refused (empty role passes), `:81` `_dsn()` may be None → `connect(None)` raises → ALLOW. Gated actions: destructive red `/clear` (`scripts/context_health_watchdog.py:1425`), SLA nudge/page (`priority_sla_watchdog.py:1913`), `reset_lane` (`lane_wedge_watchdog.py:1626`), `tg_send.sh:21`.
Why it sleeps: "fail-safe for the holder" is indistinguishable from "fail-open for anyone unconfigured": a process in a worktree without `.env` (this one), or any DB blip, lets TWO bodies self-heal/reset/send at once — the exact invariant CAI-RESP-501 says is enforced in code.
Fix sketch: split policy — fail-open only for *paging*, fail-CLOSED for destructive/pen actions (`/clear`, `reset_lane`, `tg_send`); refuse when `_dsn()` is None or `.env` not found; no default acting identity; require `holder_host` set before `holder-current`; optional cached last-good lease file for the genuine holder.

#### [P-4] `fleet_health.live_tmux_sessions()` returns an EMPTY set on any tmux error → live lanes reaped as offline
Impact: high · Effort: S
Evidence: `scripts/fleet_health.py:96-101` — `out = subprocess.run(["tmux", "ls"], ...).stdout` / `except Exception: return set()`; consumed by the observed-activity gate `:104-125,391` → `admin_mark_offline` `:395`. Bare `"tmux"` on launchd PATH (which `priority_sla_watchdog.py:433` itself notes lacks `/usr/local/bin`).
Why it sleeps: tmux missing from PATH or a hung server is reported as "no live sessions", and the reaper marks healthy lanes offline and archives their mail — the fleet thinks it cleaned ghosts.
Fix sketch: absolute tmux path; on failure raise/skip the reaper step rather than return `set()`. Same bare-`tmux`→empty-list pattern in `nervous_system/lane_watchdog.py:118,216` (watches nothing silently).

#### [P-5] `nervous_system/swallowed_except_harness.py` guards NOTHING live — all 68 `record_swallowed()` call sites are in `legacy/wingmen_orch.py`
Impact: high · Effort: M
Evidence: `grep -rln 'record_swallowed(' --include=*.py` → `legacy/wingmen_orch.py` (68) + the harness (1); `check_swallowed_escalation` is referenced outside tests only by `nervous_system/error_tracker.py:24,28` (a re-export, never called); `~/Library/LaunchAgents/dev.wingmen.orchestrator.plist` → `/Users/sheikhmusa/wingmen/orchestrator/wingmen_orch.py` (file gone; plist "Kept UNLOADED"). Harness imports `supabase.AsyncClient` + `telegram.Bot` (legacy stack).
Why it sleeps: the one mechanism meant to escalate repeated silent excepts covers a retired process; the ~470 `except: pass/continue/return` sites in the live psycopg daemons (Appendix A) have no counter and no escalation, while the module's presence implies coverage.
Fix sketch: delete it + `error_tracker.py`, or rewrite on psycopg/`bus_send` and wire `record_swallowed(label, exc)` into the except blocks listed in P-2, P-6..P-12.

#### [P-6] `tg_out.deliver_one` error path has no `rollback()` — a post-send DB error re-sends the same Telegram message every 120s forever
Impact: high · Effort: S
Evidence: `nervous_system/tg_out.py:300-307` — `except Exception as e: status = ...; cur.execute("UPDATE tg_out SET status=%s ...")` (no `conn.rollback()`); `:321-323` — `UPDATE tg_out SET status='queued' WHERE status='sending' AND created_at < now() - interval '120 seconds'`.
Why it sleeps: if `INSERT INTO operator_messages` (`:295-299`) fails AFTER the Telegram POST succeeded, the UPDATE runs in an aborted txn and raises out of `drain_once`; row stays `sending`, `attempts` unchanged, re-queued and re-sent indefinitely; the other 19 claimed rows are stuck.
Fix sketch: `conn.rollback()` first; commit `status='sent'` in its own txn before the audit insert.

#### [P-7] `tg_out` retry "backoff" keys on `created_at` — a 10 s Telegram blip burns all 5 attempts; alerts go `dead`
Impact: high · Effort: S
Evidence: `nervous_system/tg_out.py:315-318` — `... WHERE status='failed' AND created_at < now() - make_interval(secs => %s)`; `:301-302` `status = "dead" if attempts + 1 >= MAX_ATTEMPTS else "failed"`; no 4xx/5xx/429 distinction, `retry_after` ignored.
Why it sleeps: any row older than 30 s is re-queued on every 2 s drain → 5 attempts ≈ 10 s; operator pages during a transient outage die while the daemon looks healthy.
Fix sketch: `next_attempt_at` with exponential backoff, honour 429 `retry_after`, mark 400/403 dead immediately.

#### [P-8] `bus_send.send()` raises `SystemExit` on refusal — in-process daemon callers catch `Exception` only; a refused page can take down `ingest`
Impact: high · Effort: S
Evidence: `scripts/bus_send.py:265` `raise SystemExit(f"bus_send: REFUSED — {message}")`, `:591` `raise SystemExit("no DATABASE_URL ...")`, also `:150,202,214,655,667,697,707,712`; in-process callers `nervous_system/ingest.py:538-539,569-570,617-620` (`from scripts import bus_send; bus_send.send(...)`) under `except Exception` (`:1397`, `:1534`); `nervous_system/tg_out.py:494-499`; `nervous_system/operator_log.py:688-699` (`except BaseException: pass`).
Why it sleeps: `SystemExit` is not an `Exception`; in an asyncio task it propagates out of the loop → ingest exits (launchd restart loop); in tg_out the "dropped" note after a committed DELETE is lost.
Fix sketch: `send()` raises `BusSendRefused(Exception)`; only CLI `main()` converts to exit code.

#### [P-9] `ingest` can be poisoned by ONE bad update — a data error on the `operator_messages` INSERT is logged as "DB unreachable" and the offset is never acked
Impact: high · Effort: M
Evidence: `nervous_system/ingest.py:1289-1299` (INSERT) → `:1526-1533` (offset not acked on `psycopg.Error`, logged as substrate unreachable).
Why it sleeps: NUL byte / `tag_shape_chk` / RLS denial is a `psycopg.Error` like a connection drop; `getUpdates` returns the same batch forever, the whole bot token is deaf, and health says "DB down" while the DB is fine.
Fix sketch: split `OperationalError` from `DataError/IntegrityError`; quarantine row, advance `update_id`, page once.

#### [P-10] `lane_wedge_watchdog --loop`: a permanent DB fault becomes an empty, "clean" scan forever
Impact: high · Effort: S
Evidence: `nervous_system/lane_wedge_watchdog.py:1657-1659` — `except Exception as e: log(f"db/gather failed: {e}"); observations = []`; loop `:2046-2055` then `_clear_crash_state()` + `time.sleep(interval)`; `_page` itself goes through the same DB. Sibling: `scripts/priority_sla_watchdog.py:1899-1904` — `except Exception as e: log(f"db-connect-failed: {e}"); return 1` (each launchd tick exits 1, no streak, no page).
Why it sleeps: bad DSN / rotated password → zero observations → zero wedges → zero pages; the watchdog's own heartbeat keeps ticking.
Fix sketch: consecutive-failure streak file; after N, page via `nazim_send.sh` (non-DB path) and exit non-zero.

#### [P-11] `agent_wake` reverse-ownership (pane hijack) guard fails OPEN on a DB error
Impact: high · Effort: S
Evidence: `nervous_system/agent_wake.py:393-396` — `except Exception as e: ... "reverse-ownership read failed ..."; return []` → `:406-408` `for row in src(session) or []: ... return False`.
Why it sleeps: the #40426 guard against typing a wake into another agent's live pane disappears exactly when the substrate is flaky; a wake lands in the wrong lane with no record.
Fix sketch: refuse any candidate not from the agent's own row when the lookup fails; page on repeats.

#### [P-12] `tg_out.enqueue` with no chat_id and `MUSA_TELEGRAM_ID` unset falls to `allowed_chat_ids[0]` — operator alerts could land in a client/group chat
Impact: high · Effort: S
Evidence: `nervous_system/tg_out.py:213-217` — `op = os.environ.get("MUSA_TELEGRAM_ID")` / `if op and int(op) in (allowed or []): ...` / `elif allowed: default_chat = allowed[0]`.
Why it sleeps: silent identity fallback on a people-facing surface; nothing logs that the fallback branch fired.
Fix sketch: raise when no explicit chat_id and no operator id.

#### [P-13] `tg_out` durable-log row is written UNREDACTED (bypasses `operator_log.log()` vault check + personal-routing)
Impact: high · Effort: S
Evidence: `nervous_system/tg_out.py:225` `text = redact(text)` (local to `_send_text`) vs `:295-298` `INSERT INTO operator_messages (...) VALUES ('outbound','telegram',%s,%s,%s,true)", (str(target), tag, text or ...)` with the pre-redaction `text`.
Why it sleeps: Telegram gets the redacted copy, the substrate keeps secret/private-family text; the vault guard never ran so nothing fires.
Fix sketch: route the audit insert through `operator_log.log()`.

#### [P-14] `hub_self_recovery` commits an audit row saying "nudged" BEFORE the nudge runs; the shell driver's exit code is never written back
Impact: med (high once `HSR_MODE=act`) · Effort: S
Evidence: `nervous_system/hub_self_recovery.py:350-363` — `elif mode == "act": action = "nudged"; result["act"] = True ... log_event(cur, mode=mode, action=action, ...); conn.commit()`; actual nudge in `scripts/hub_self_recovery.sh:88` (`lane_nudge.sh`), rc unrecorded; `:367-375` failed observation page → `conn.rollback()` with no log line; `:376-384` `finally: stamp_tick(...)` on a possibly-aborted txn masks the original error.
Why it sleeps: `hub_self_recovery_log` is the evidence the hub was revived; a failed nudge is indistinguishable from a successful one.
Fix sketch: log `nudge-requested`, then `--record-result rc=$?` from the driver; rollback before `stamp_tick`.

#### [P-15] `ingest` sets the "already nudged" marker BEFORE the nudge; bus-notify daemons advance the high-water mark even when the tmux nudge failed/debounced
Impact: med · Effort: S
Evidence: `nervous_system/ingest.py:984-989` — `ch._last_forced_nudge_id = newest_id` then `if nudge_session(...): ... else: _log_line(... "NOT delivered")`; `nervous_system/nazim_bus_notify.py:151-155` — three `subprocess.run([TMUX, "send-keys", ...], capture_output=True)` no returncode, `return True`, `:234-235` `last_max` advanced; `nervous_system/cai_bus_notify.py:99-106` (+ finance/fleet_health twins) `else: _log("... within debounce — coalescing")` / `last_max = mx`; `nervous_system/lane_wedge_watchdog.py:796-801` `nudge_hub` ignores `tm("send-keys", ...)` rcs → `return True` (also suppresses the singleton-DEAD backstop `:1486`); `nervous_system/lane_watchdog.py:221-226` `nudge_orch_escalations` always True.
Why it sleeps: "can't find pane"/debounce is recorded as delivered; the row is durable but nobody is rung; on boot the mark resets to current max so rows that arrived while down are never nudged.
Fix sketch: advance markers only on rc==0 / True; carry a `pending` flag through the debounce window.

#### [P-16] `operator_log` vault-leak P1 page swallowed with `except BaseException: pass`; a crashed vault scan writes raw text with no page
Impact: med · Effort: S
Evidence: `nervous_system/operator_log.py:700-710` — `except BaseException: ... pass` around `bus_send.send(... "P1", req=True)`; `:744-746` — `except Exception as exc: leaked_keys, skipped = [], [("*", f"guard_error:...")]`; `:806-809` `maybe_track_ask(...)` / `except Exception: pass` (asks-ledger row lost silently).
Why it sleeps: the only signal that a secret hit the durable log is itself best-effort and dropped (also eats `KeyboardInterrupt`); guard crash degrades to "write raw, note a skip".
Fix sketch: catch `(Exception, SystemExit)` + stderr/fallback file; on `guard_error` apply pattern `redact()` and page.

#### [P-17] `operator_log` import silently OVERWRITES `os.environ["DATABASE_URL"]` from `<repo>/.env`
Impact: med · Effort: S
Evidence: `nervous_system/operator_log.py:32-38` — `_file_db_url = _dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env")).get("DATABASE_URL"); if _file_db_url and not _under_pytest: os.environ["DATABASE_URL"] = _file_db_url` / `except Exception: pass`.
Why it sleeps: an import side effect repoints every later `psycopg.connect(os.environ["DATABASE_URL"])` in the importing process to whichever `.env` sits beside the imported copy; a worktree without `.env` is a no-op, so identical code behaves differently per checkout. `ingest` gets file-first DSN only by accident of this import; `tg_out` (no import) keeps a stale DSN after rotation.
Fix sketch: callers ask `scripts/lib/substrate_dsn.dsn_from_env_file()` explicitly; no global mutation.

#### [P-18] Nearly every daemon `psycopg.connect()` lacks `connect_timeout`/`statement_timeout`; `ingest` runs DB + media + tmux work ON the asyncio loop
Impact: med · Effort: S
Evidence: connects/with-timeout — `nervous_system/ingest.py` 4/0, `tg_out.py` 4/0, `agent_wake.py` 6/0, `wake_backstop_sweep.py` 16/0, `scripts/fleet_health.py` 1/0 (`:355`), `scripts/exec_broker_daemon.py` 1/0, `operator_log.py` 8/1, `scripts/bus_send.py` (psycopg2 `:98,122,164,368,461,737`) 0, `scripts/context_health_watchdog.py:375,1008,1578`, `scripts/live_checkout_branch_watchdog.py:186`, `nervous_system/singleton_liveness.py:224`, `scripts/lib/fleet_health_lease.py:205,239,280`. `nervous_system/ingest.py:1480-1490` — `with psycopg.connect(_dsn()) as conn: for upd in ...: process_update(conn, ...)` (only `getUpdates` is in `asyncio.to_thread`, `:1479`).
Why it sleeps: a pooler stall hangs the loop; for ingest ALL bots stop polling while heartbeats look recent until TTL; also subprocess without timeout `agent_wake.py:216,244,251,262,293,559`, `nazim_bus_notify.py:128,151-155`.
Fix sketch: one shared `connect()` helper (`connect_timeout=10, options='-c statement_timeout=15000'`); `process_update` in `asyncio.to_thread`; `timeout=5` on tmux calls.

#### [P-19] `ingest` revoked-token (401)/409 path retries every 5 s forever; `tg_out` DB-down loop logs every 2 s; neither pages in-process
Impact: med · Effort: S
Evidence: `nervous_system/ingest.py:1534-1557` — `except Exception as e: _log_line(...)` → best-effort `ingest_poll_health` upsert under `except Exception: pass` → `return last_db_alert, ERROR_BACKOFF`; `nervous_system/tg_out.py:556-562` — `except psycopg.Error: _log_line("WATCHDOG: substrate DB unreachable")`; `time.sleep(DRAIN_INTERVAL)`; any other exception kills the daemon (launchd restart loop).
Why it sleeps: paging depends entirely on the external liveness watchdog reading `consec_errors`; if the DB is also down the health write is swallowed and nothing anywhere records the dead bot.
Fix sketch: 401/404 terminal → page once via bus_send (non-SystemExit) + exponential backoff to 10 min.

#### [P-20] `ingest` reset-button audit row and gated-stranger `handled_at` stamp are `except Exception: pass` with no rollback
Impact: med · Effort: S
Evidence: `nervous_system/ingest.py:1143-1152` (`INSERT INTO operator_messages ... 'ingest-button'` / `except Exception: pass`); `:1344-1352` (`UPDATE operator_messages SET handled_at=now()` / `except Exception: pass`).
Why it sleeps: a destructive reset can run with no audit row; the next statement on the same conn fails `InFailedSqlTransaction` and is mislabelled "DB unreachable"; the un-stamped stranger row triggers `reassure_if_unhandled` → "Got your message" to the operator (2026-09-16 bug again).
Fix sketch: `conn.rollback()` + log in both.

#### [P-21] `fleet_health_lease._agent_id()` defaults the acting identity to the SRE; `orch_lease._holder_id()` / `operator_log._agent_id()` default to `cc-orchestrator`
Impact: med · Effort: S
Evidence: `scripts/lib/fleet_health_lease.py:83-89` — `return (override or os.environ.get("FLEET_HEALTH_LEASE_AS") or SRE_AGENT_ID).strip()`; `scripts/lib/orch_lease.py:112` — `os.environ.get("ORCH_AGENT_ID", "cc-orchestrator")`; `nervous_system/operator_log.py:63` same; `scripts/gzb_hub_heartbeat.py:43` same; `scripts/gated_inbound_digest.py:213` `--from-agent` default `cc-orchestrator`.
Why it sleeps: any script invoked without the env var claims to be `cc-fleet-health`/the hub; on the SRE's host it passes `holder-current`; `take`/`renew`/audit stamps record "hub" when it wasn't.
Fix sketch: no default identity for lease/audit paths — refuse when unset.

#### [P-22] `tg_out` dead-row cleanup DELETEs + commits BEFORE posting the "dropped" note; chunked/text+file retries double-send
Impact: med · Effort: M
Evidence: `nervous_system/tg_out.py:456-459` (`DELETE FROM tg_out WHERE id=%s; conn.commit()`) → `:494-499` `bus_send.send(...)` (can `SystemExit`, P-8); stale batch `:403-404`; chunk loop `:226-234,289-291` re-sends whole text on partial failure; `:321-323` re-queue keyed on `created_at` so the CLI `send` (`:536`) can re-queue a row the daemon is mid-sending.
Why it sleeps: the only record a message to the operator was dropped is written after the evidence is gone; duplicates look like "working".
Fix sketch: note first (or same txn), then delete; `chunks_sent` + `claimed_at` columns.

#### [P-23] `singleton_liveness.hub_alive_evidence` treats an unreadable hub lease as ALIVE; `commitment_sweeper` then routes to a possibly-dead hub
Impact: med · Effort: S
Evidence: `nervous_system/singleton_liveness.py:328-333` — `if fresh is None: log("... indeterminate/unreadable -> fail-safe ALIVE ..."); return True`; `:281-284` naive/aware `TypeError` → None → ALIVE; consumer `nervous_system/commitment_sweeper.py:123`; `:428-441` dead-man page for probe failure written to the DB that is the fault (`except Exception: pass`).
Why it sleeps: "unknown" is collapsed into "alive" at the routing layer, so commitments pile up on a dead hub with no page.
Fix sketch: return None for unknown; sweeper routes to console when None; `nazim_send.sh` fallback for the dead-man page.

#### [P-24] Watchdog state/log paths tied to `~/wingmen/orchestrator` (not `__file__`), with silent write failures
Impact: med · Effort: S
Evidence: `scripts/priority_sla_watchdog.py:48-50` — `ORCH = Path(os.path.expanduser("~/wingmen/orchestrator")); STATE_FILE = ORCH / "logs" / ...; LOG_FILE = ...` (log write `except: pass` `:266`); `nervous_system/lane_watchdog.py:33-38` same + `sys.path.insert(0, ORCH)` (imports the LIVE checkout's `scripts.lib` from a worktree), `STATE_FILE.write_text` `:337` no `mkdir`; `scripts/context_health_watchdog.py:841-845` `_save_exec_state` `except OSError: pass` (loses `reset_at`/`checkpoint_at` dedup → repeated `/clear`); `scripts/live_checkout_branch_watchdog.py:32-33` `EXPECTED_BRANCH = "fable/substrate-safe-fixes"` hard-coded; `scripts/cc_session_costs_backfill.py:24`, `scripts/watchdog_tripwire_report.py:23` `load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")`; stale hub hosts `lane_wedge_watchdog.py:257` `HUB_SSH="Musa@mac-studio"`, `priority_sla_watchdog.py:215,551` `STUDIO_SSH`.
Why it sleeps: a missing `logs/` dir loses the anti-replay state every tick; a worktree run executes the live checkout's scripts; cross-host nudges to a decommissioned host fail "successfully".
Fix sketch: `Path(__file__).resolve().parents[1]`; `mkdir(parents=True)`; refuse to arm red when state can't be saved; read hub host from the lease row.

#### [P-25] `console/auth.py` breakglass + access audit lines are `except Exception: pass`
Impact: low · Effort: S
Evidence: `nervous_system/console/auth.py:219-224` — `fh.write(f"{ts}\t{peer_ip}\tBREAKGLASS\tused\n")` / `except Exception: pass`; `:238-243` same for the access log.
Why it sleeps: a break-glass use is the one event that must be recorded; a full disk or missing dir makes it invisible while access still succeeds.
Fix sketch: on audit-write failure emit stderr + best-effort bus page.

---

### Checked and found clean

- **Verified-authorization gate** `scripts/lib/require_verified_authorization.py:129-178` — fail-closed on every branch (no `after`, no `MUSA_TELEGRAM_ID`, no DSN, misconfigured phrase, DB error → `AuthResult(False, ...)`).
- **Ingest deny-by-default gate** `nervous_system/ingest.py:652-660` `gate_allows` is pure (no try); `chat_id is None` gated (`:1335`); callback requires `presser == MUSA_TELEGRAM_ID` (`:1097`). No path turns "gate errored" into "allowed".
- **PreToolUse hooks** `scripts/hooks/console_irsyad_guard.py:164-172`, `consult_governance_floor_guard.py:150-160`, `glm_image_read_guard.py:188-208`, `secrets_transcript_guard.py` return 2 on internal crash for the guarded body. `secrets_output_scanner.py:536-537` returns 0 on crash but is PostToolUse (documented).
- **Arm defaults**: `CTX_WD_ARM` → off (`context_health_watchdog.py:1952`), `LANE_WEDGE_ARM` unset → detect-only (`lane_wedge_watchdog.py:1446`; note `1`/`true` map to the full ESCALATE ladder incl. `reset_lane`), `HSR_MODE` → observe, `AUTO_WAKE_ENABLED` → "0". `priority_sla_watchdog`, `fleet_health`, `lane_watchdog` have NO arm switch and act by default, gated only by the fail-open lease (P-3).
- **`shell=True`**: only `nervous_system/council_executor.py:325,386,436` — cmd string verbatim from `cto_council_sessions.spec_json` (DB-sourced arbitrary shell), rollback always logs `✓` regardless of rc (`:389`, `:283` → `rolled_back`), but the module is imported ONLY by `legacy/wingmen_orch.py` (unloaded) → dead code (Appendix D). `nervous_system/console/panes.py` explicitly avoids `shell=True`. No `os.system`.
- **Naive datetimes in TTL math**: none in lease/TTL code (`orch_lease.py`, `fleet_health_lease.py`, `require_verified_authorization.py:55-69` normalise to UTC; relevant columns are `timestamptz`). Only naive sites: `scripts/pooler_authfail_tripwire.py:41,70` (`utcnow()` log text), `scripts/gazzabyte_board_autopublish.py:52` (stamp string).
- **HTTP timeouts**: all Telegram `urlopen` calls pass one (`tg_out.py:230` 30 s, `:253` 120 s; `_tg_chunked_send.py:153` 30 s; `lane_watch.py:60` 15 s; ingest 35/30/60 s). `httpx.post` without timeout only in dev tooling `scripts/qwen_capability_testgen.py:332`, `scripts/ab_qwen_opus.py:231`.
- **pyflakes** over `nervous_system scripts`: zero `undefined name` / `referenced before assignment`.
- **`wake_backstop_sweep.py`** 16 swallows all fail SAFE (unprovable → "alive" → never quiesce `:517,806`; or toward paging `:595,612`).
- **`hub_bus_currency_monitor.py:191,302`** swallow only the optional `alert_format` import.
- **No Python default aims a WRITE at a Supabase project ref**: hard-coded refs (`scripts/apply_migration.py:185,230-231`, `ci_bootstrap_schema.py:119-121`, `onboard_client_channel.py:70`, `pooler_authfail_tripwire.py:33`, `scripts/lib/exec_broker/gate_validation.py:66-67`, `hooks/secrets_transcript_guard.py:532-534`) are allow/deny lists. DSNs resolve to `None` (not a wrong store) and then fail at connect — the failure mode is P-3's fail-open, not a mis-aimed write.

---

### Appendix A — raw table: remaining swallowed excepts / ignored results (path:line | pattern)

Counts of `except ...: pass|continue|return None/{}/[]|log.debug` per file (473 total; top): `nervous_system/console/app.py` 22 · `scripts/context_health_watchdog.py` 17 · `nervous_system/wake_backstop_sweep.py` 16 · `scripts/priority_sla_watchdog.py` 14 · `nervous_system/lane_wedge_watchdog.py` 14 · `scripts/disk_autoremediate.py` 12 · `nervous_system/agent_wake.py` 11 · `scripts/preview_devserver_reaper.py` 10 · `scripts/hooks/secrets_output_scanner.py` 9 · `nervous_system/ingest.py` 8 · `scripts/worktree_nodemodules_reaper.py` 8 · `scripts/sre_lane_recycle.py` 7 · `scripts/lib/pgtest_reaper.py` 7 · `nervous_system/console/panes.py` 7 · `scripts/_tg_chunked_send.py` 6 · `nervous_system/cc_session_costs_auto_writer.py` 6 · `scripts/bus_send.py` 5 · `scripts/lib/lane_token_resolver.py` 5 · `scripts/checkpoint_recycle_driver.py` 5 · `nervous_system/console/db.py` 5.

| path:line | pattern |
|---|---|
| `scripts/pooler_breaker_snapshot.py:93` | bare `except: cmd="?"` (only bare except in scope) |
| `scripts/lib/orch_lease.py:361,364,383,428,431` | `/proc` reads for hub auth fp/model → `return None` |
| `scripts/lib/orch_lease.py:486` | hub heartbeat write `except Exception as e: print(...)` — renew succeeds, hb row stale |
| `scripts/lib/lane_token_resolver.py:167,178,193,222` | pointer/keyfile/fp-map reads → `None`/`{}` (falls to default token) |
| `scripts/_tg_chunked_send.py:72,82,97` | dedupe lookup → `None` (send proceeds) |
| `scripts/_tg_chunked_send.py:210,224` | reason/msgid sidecar write `pass` |
| `scripts/_tg_chunked_send.py:252` | weekday-guard crash → fail OPEN + page (documented) |
| `nervous_system/agent_wake.py:186-191` | pause lookup fails → "failing OPEN (waking normally)" |
| `nervous_system/agent_wake.py:567-571` | state file corrupt → `{}` (5-wakes/5-min cap reset) |
| `nervous_system/agent_wake.py:585-593` | `record_pending` `except: pass` (retry marker lost) |
| `nervous_system/agent_wake.py:559-560` | bare `"tmux"` + non-exact `-t` for busy check (two tmux servers → "not busy") |
| `nervous_system/ingest.py:726,738` vs `agent_wake.py:282` vs `nazim_bus_notify.py:39` | three different tmux binary preferences |
| `nervous_system/ingest.py:1555-1556` | poll-health write `pass` |
| `nervous_system/ingest.py:1196-1204 → 1402-1422` | dedupe+log committed before route/nudge (crash → nudge lost; 600 s stale-drain backstop) |
| `nervous_system/operator_log.py:1003` | JSON parse fallback (benign) |
| `nervous_system/singleton_liveness.py:138,147` | tmux/DB unreadable → "uncovered" (class logged) |
| `nervous_system/singleton_liveness.py:233,241` | state file `{}`/`pass` |
| `nervous_system/lane_wedge_watchdog.py:1540-1542` | `reset_lane` FAILED only logged; `escalated_at` stamped → "already escalated — holding" |
| `nervous_system/lane_wedge_watchdog.py:1994-2008,2054,2076-2081` | crash fingerprint saved before page; page `subprocess.run` unchecked under `except: pass` |
| `nervous_system/lane_watchdog.py:100-102` | `unread_bus_work` → 0 on DB error ("nothing to nudge") |
| `nervous_system/lane_watchdog.py` (whole) | no lease gate at all before send-keys / `admin_mark_offline` |
| `nervous_system/commitment_sweeper.py:346-350,384` | prints `FIRED` before the single batch commit |
| `scripts/live_checkout_branch_watchdog.py:126-131,186` | read-miss `return 0` no streak; DB connect failure crashes, no page |
| `scripts/priority_sla_watchdog.py:483-521` | `_tmux_countonly` ignores send-keys rc, returns True — zero callers (dead) |
| `nervous_system/cc_session_costs_auto_writer.py:127,169,204,228,267,299,323` | cost parse → `{}`/`None`/`continue` (silent under-report) |
| `nervous_system/hub_self_recovery.py:367-375` | failed observation page → rollback, no log |

### Appendix B — identity / store defaults (path:line | default)

| path:line | default |
|---|---|
| `scripts/lib/orch_lease.py:112` | `ORCH_AGENT_ID` → `cc-orchestrator` |
| `nervous_system/operator_log.py:63` | `ORCH_AGENT_ID` → `cc-orchestrator` (hub/console) |
| `nervous_system/operator_log.py:53` | `ORCH_BODY_ROLE` unset → `lane` (deliberate) |
| `scripts/gzb_hub_heartbeat.py:43` | `ORCH_AGENT_ID` → `cc-orchestrator` |
| `scripts/lib/fleet_health_lease.py:83-89` | acting id → `SRE_AGENT_ID` |
| `scripts/gated_inbound_digest.py:213` | `--from-agent` → `cc-orchestrator` |
| `scripts/lane_operator_reconcile.py:80` | → `unknown-lane` |
| `scripts/mamadah_reconcile.py:103` | → `cc-mamadah` |
| `scripts/hooks/secrets_output_scanner.py:492,598` | → `unknown-agent` |
| `nervous_system/tg_out.py:213-217` | chat → `allowed_chat_ids[0]` when `MUSA_TELEGRAM_ID` unset |
| `nervous_system/ingest.py:94-97`, `tg_out.py:69-72`, `agent_wake.py:40`, `orch_lease.py:81`, `fleet_health_lease.py:70-71`, `gzb_hub_heartbeat.py:52` | DSN → `None`, unchecked before `connect` |
| `nervous_system/lane_wedge_watchdog.py:257` | `HUB_SSH` → `Musa@mac-studio` (stale host) |
| `nervous_system/*_bus_notify.py:33-37` | tmux session → `nazim`/`finance`/`fleet-health` |
| `scripts/adcda_fn_error_bus_watch.py:50` | `ADCDA_PROJECT` → `cosem-adcda-cb6d9` |
| `scripts/sentry_new_error_watch.py:384`, `irsyad_daily_issue_digest.py:76` | Sentry org/project → `ihsanos` |

### Appendix C — `load_dotenv` spellings (20 distinct, 0 assert existence, 0 `override=True`)

12× `load_dotenv(_ORCH_DIR / ".env")` · 11× `os.path.join(os.path.dirname(__file__)...)` · 9× `str(ORCH / ".env")` · 8× bare `load_dotenv()` (CWD-relative: `scripts/exec_broker_daemon.py:152`, `scripts/asks_close.py:49`, `scripts/backlog_swipe.py:75`, `nervous_system/coordinator_pane_publisher.py:170`, `scripts/backfill_option_b_historical_bugs.py:22`) · 7× `os.path.join(ROOT, ".env")` · 6× `ORCH / ".env"` · 4× each `os.path.join(ORCH, ".env")`, `_ORCH / ".env"`, `ROOT / ".env"` · 3× each `os.path.join(here, ".env")`, `os.path.join(_REPO_ROOT, ".env")`, `env_path`, `Path(__file__)...` · 2× each `pathlib.Path(__file__)...`, `os.path.join(ORCH_DIR, ".env")`, `ORCH_DIR / ".env"`, hard-coded `/Users/sheikhmusa/wingmen/orchestrator/.env` (`scripts/cc_session_costs_backfill.py:24`, `scripts/watchdog_tripwire_report.py:23`), `".env"` (`scripts/verify_governance_hygiene_batch.py:314`). `exec_broker_daemon` partially mitigates with `_REQUIRED_ENV_VARS` + `dsn_from_env_file` (`:148-160`).

### Appendix D — dead code (modules imported by nothing live and referenced by no plist/shell)

Confirmed by this audit (grep across `*.py *.sh *.plist *.json *.md` + `~/Library/LaunchAgents`):

| module | only referenced by | note |
|---|---|---|
| `nervous_system/swallowed_except_harness.py` | `legacy/wingmen_orch.py`, `error_tracker.py` (re-export), tests | P-5; legacy supabase/telegram stack |
| `nervous_system/error_tracker.py` | `legacy/wingmen_orch.py` + 15 `nervous_system/*` modules that are themselves only imported by legacy (`brain_sync`, `bug_reports_poll`, `strategic_decisions_poll`, `queue_stall_detector`, `agent_messages_realtime`, `pipeline_clock`, `morning_brief`, `orch_self_audit`, `deploy_verifier`, `paused_jobs_retry_policy`, `agent_messages_poll`, `agent_watchdog`, `paused_job_escalation`) | legacy-only chain — needs a per-module plist check before deletion |
| `nervous_system/council_executor.py` | `legacy/wingmen_orch.py` | carries the only `shell=True` + DB-sourced command strings in scope |
| `scripts/priority_sla_watchdog.py::_tmux_countonly` | nothing | dead function with ignored send-keys rc |
| `~/Library/LaunchAgents/dev.wingmen.orchestrator.plist` | → `/Users/sheikhmusa/wingmen/orchestrator/wingmen_orch.py` (file absent) | unloaded by comment; delete or point at legacy/ |

(A full per-module referenced-by table for all 278 modules was delegated and had not returned within the time box; the rows above are the ones verified directly.)

### Appendix E — duplicated helpers (collapse candidates)

| shape | implementations (path:line) |
|---|---|
| DSN resolution (`DATABASE_URL or SUPABASE_DB_URL`) | `scripts/lib/orch_lease.py:81`, `scripts/lib/fleet_health_lease.py:70`, `nervous_system/ingest.py:94`, `nervous_system/tg_out.py:69`, `nervous_system/agent_wake.py:40`, `scripts/gzb_hub_heartbeat.py:52`, `scripts/_tg_chunked_send.py:74-83` (manual `.env` line parse), `scripts/bus_send.py::dburl` (`:591`), `scripts/lib/substrate_dsn.dsn_from_env_file` (the one that reads file-first), `nervous_system/operator_log.py:32-38` (global env mutation) — ≥10 → 1 |
| psycopg connect wrappers | psycopg3 in `nervous_system/*` and `scripts/lib/*lease.py`; psycopg2 in `scripts/bus_send.py:98,122,164,368,461,737` and `nervous_system/singleton_liveness.py:140,224` — two drivers, no shared `connect()` with timeouts |
| `.env` loading | 20 spellings (Appendix C) → 1 `load_orch_env()` |
| Telegram send | `nervous_system/tg_out.py:226-234` (urlopen), `scripts/_tg_chunked_send.py:150-173` (urlopen + retry_after), `scripts/lane_watch.py:57-63` (urlopen), `scripts/tg_send.sh` + `scripts/nazim_send.sh` (curl), `nervous_system/operator_notify.py:28-32`, legacy `telegram.Bot` in harness — 6 → 1 gateway (tg_out already exists as the sanctioned one) |
| tmux binary resolution | `ingest.py:726,738`, `agent_wake.py:282`, `nazim_bus_notify.py:39`, bare `"tmux"` in `fleet_health.py:98`, `lane_watchdog.py:118,216`, `priority_sla_watchdog.py:418,421,500` — 4 strategies → 1 `fleet_tmux_bin()` |
| bus notify daemons | `nervous_system/cai_bus_notify.py`, `finance_bus_notify.py`, `fleet_health_bus_notify.py`, `nazim_bus_notify.py` — near-identical loops with the same high-water-mark bug (P-15) → 1 parameterised daemon |
| host identity | `scripts/lib/fleet_host_id.py` (canonical) vs `socket.gethostname()` in `nervous_system/ingest.py:1552` (poll-health row host) |

---

# 5. Shell, git, worktree/.venv and runtime traps (auditor E)

## E — Shell / git / worktree / runtime-environment audit (read-only, 2026-10-28)

Worktree audited: `/Users/sheikhmusa/wingmen/orchestrator.wt-fable-3` @ 7f10ed1. Live checkout read-only: `/Users/sheikhmusa/wingmen/orchestrator` (branch `fable/substrate-safe-fixes`, HEAD 4bad6c6 after ff-pull at 03:19 +08).

Headline: **no script, launchd job or cron runs a mutating git op against the live checkout** (crontab is empty; all ~25 advances in the last 10h are hand-typed `git pull --ff-only`). The real holes are environmental: Claude-Code hooks are wired to a literal `$CLAUDE_PROJECT_DIR/.venv/bin/python3` that 9 of 13 sibling worktrees do not have (guards fail OPEN there), the live checkout carries **12 uncommitted tracked-file edits + 242 untracked files** that launchd daemons and `deploy_console.sh` execute/ship (deployed != GitHub), and the git hooks that are supposed to protect the live checkout are resolved from the live checkout's path, so every worktree runs whatever hook version happens to sit there.

---

#### [S-1] Claude Code hooks fail OPEN in any worktree without `.venv` (9/13 sibling worktrees today)
Impact: high  Effort: S
Evidence:
- `.claude/settings.json:8,18,30,41,51,61` — every hook command is the literal `"$CLAUDE_PROJECT_DIR"/.venv/bin/python3 "$CLAUDE_PROJECT_DIR"/scripts/hooks/<guard>.py` (console_irsyad_guard, consult_governance_floor_guard, lane_prod_key_guard, glm_image_read_guard, session_start_reconstitute, precompact_digest).
- Worktree survey (this session): `.venv` present only in `orchestrator`, `wt-console-revamp`, `wt-fable-1/2/3/4`, `wt-slowask-deferred`(dangling: `.venv/bin/python3` missing) — absent in `wt-asksboard, wt-bariq-tg, wt-busstamp, wt-glm-accept-leak, wt-glm-proj, wt-glm-std, wt-memlint`. The `wt-fable-*` ones are hand-made symlinks to `/Users/sheikhmusa/wingmen/orchestrator/.venv` (ad hoc; no script or doc creates them — grep for `ln -s .*\.venv` across scripts/docs/CLAUDE.md/memory: 0 hits).
- Claude Code hook contract: a command that cannot exec returns 127 → treated as a *non-blocking* error, not a deny (only exit 2 blocks). So `console_irsyad_guard.py` / `lane_prod_key_guard.py` ("fail-closed", `scripts/hooks/lane_prod_key_guard.py:11`) never run there. All four guards are stdlib-only (import grep), so the venv is not even needed — the literal path is the only thing that breaks.
- No boot-time verification: `scripts/launch_dangerous_cc.sh:44` sets `VENV_PY="$ORCH_DIR/.venv/bin/python3"` for the LAUNCHER's repo (live checkout), never checks `$CALLER_DIR/.venv`; `scripts/lanes.sh:129-131` just `tmux new-session -c "$dir" "$LAUNCHER"`; CLAUDE.md:50 `git worktree add` guidance says nothing about `.venv`/`.env`; `scripts/session_start_reconstitute.py` itself runs via the same missing interpreter so it cannot warn.
- Contrast: `.claude/settings.local.json:15` (untracked, present in only 6/14 trees) hard-codes the LIVE `.venv` absolute path for `secrets_transcript_guard.py` — that one works everywhere but is invisible to the other 8 trees.
Fix sketch: replace the interpreter in all six hook entries with a tracked `scripts/hooks/run_hook.sh <guard.py>` that picks `"$CLAUDE_PROJECT_DIR/.venv/bin/python3"` → `$HOME/wingmen/orchestrator/.venv/bin/python3` → `python3`, and `exit 2` with a stderr line if none is executable (deny, not skip). Add to `lanes.sh up`/`launch_dangerous_cc.sh` a pre-flight `[ -x "$CALLER_DIR/.venv/bin/python3" ] || ln -s "$ORCH_DIR/.venv" "$CALLER_DIR/.venv"` plus the same for `.claude/settings.local.json`. Add a `tests/test_hooks_interpreter_present.py` that asserts the hook command resolves in a bare worktree.

#### [S-2] Live checkout runs with 12 uncommitted tracked edits + 242 untracked files; launchd + `deploy_console.sh` ship that tree, not GitHub
Impact: high  Effort: M
Evidence:
- `git -C ../orchestrator status --short`: `M scripts/tg_send.sh`, `M scripts/nazim_send.sh`, `M scripts/hooks/consult_governance_floor_guard.py`, `M scripts/onboard_client_channel.py`, `M tests/test_consult_governance_floor_guard.py`, `M state/*.json`, `M reports/console-deploy/...` (diff --stat: 12 files, +166/-33); 242 untracked. The `tg_send.sh`/`nazim_send.sh` hunks are a 2026-10-27 `--ask` arg-validation hot-patch that exists ONLY on disk (not on any branch) — the sanctioned operator voices are running code GitHub has never seen (`feedback_deployed_code_must_match_github`).
- `scripts/deploy_console.sh:30-31` — `cd "$HOME/wingmen/orchestrator"; source .env` then hashes and kickstarts whatever the WORKING TREE holds; there is no `git status --porcelain` clean check and no `git pull`. "Deploy path runs THROUGH the live checkout" = it deploys the working tree, including these untracked/modified files.
- Advancement is manual: `git -C ../orchestrator reflog -n 25` is 25× `pull --ff-only` by hand (17:44 → 03:19 +08); grep of scripts/launchd/docs finds NO script or runbook that advances the live checkout (`docs/runbooks`: 0 hits for `git pull|ff-only`). `--ff-only` is good, but a hand-typed pull will REFUSE the moment an incoming commit touches one of the 12 locally-modified files ("local changes would be overwritten") — at which point the next agent is one `git checkout -- .`/`stash` away from either losing the hot-patch or (worse) stashing onto the SHARED stash stack (3 stale entries already: `stash@{0..2}` dated 2026-04/07/08).
Fix sketch: (1) land the `tg_send.sh`/`nazim_send.sh`/`consult_governance_floor_guard.py` hot-patches as a PR today, then `git -C ~/wingmen/orchestrator checkout -- <those files>` after merge+pull; (2) add a tracked `scripts/advance_live_checkout.sh` = branch==`fable/substrate-safe-fixes` && `git status --porcelain` clean (tracked) && `git pull --ff-only origin fable/substrate-safe-fixes`, loud refusal otherwise; (3) make `deploy_console.sh` refuse when `git status --porcelain -- $GATED_PATHS` is non-empty (ship only committed console content; today the review hash can cover an uncommitted file).

#### [S-3] `core.hooksPath` is an absolute path INTO the live checkout — every worktree runs the live tree's hook files, and the live tree can be dirty/stale
Impact: med  Effort: S
Evidence:
- `git config --get core.hooksPath` (this worktree) = `/Users/sheikhmusa/wingmen/orchestrator/scripts/git-hooks`; identical in `../orchestrator` (shared `.git/config`, so answer is "yes in both", but because it is ABSOLUTE rather than relative `scripts/git-hooks`, a worktree on a branch that changes `pre-push` still runs the live checkout's copy).
- `scripts/git-hooks/post-checkout:12-15` acknowledges this; `pre-push:94` falls back from `$ROOT/.venv/bin/python3` to system `python3`, and `:110-111` **warns and passes** when pytest is absent — in the 9 venv-less worktrees gate 1 (test-collection) is a no-op. `pre-commit:29` only guards the live checkout; `pre-push` gate 2 (console review) blocks; `post-checkout` only pages (never blocks, `:17`). `--no-verify` bypass is advertised in the hooks themselves (`pre-commit:50`, `pre-push:107,146`); no doc forbids it.
- Hooks are not executable-verified by anything: `tests/test_pre_push_hook.py:42` only asserts the file exists.
Fix sketch: `git config core.hooksPath scripts/git-hooks` (relative) so each worktree runs its own tracked copy; make `pre-push` gate 1 fail-closed when `$ROOT/.venv` is missing (print the symlink one-liner), or resolve the live venv explicitly instead of `command -v python3`.

#### [S-4] Three installed launchd jobs target files that no longer exist; 64 installed plists are not tracked in `launchd/`; 5 tracked ones differ from what is installed
Impact: med  Effort: M
Evidence:
- `~/Library/LaunchAgents/dev.wingmen.orchestrator.plist` → `/Users/sheikhmusa/wingmen/orchestrator/wingmen_orch.py` (moved to `legacy/` in d6c2630, PR #91) and `dev.wingmen.watchdog.plist` → `.../watchdog.py` — both files absent; neither label appears in `launchctl list` (not loaded), yet `scripts/restart_orch.sh:24` still targets `dev.wingmen.orchestrator` and CLAUDE.md says "Restart orchestrator only via scripts/restart_orch.sh" — the runbook'd restart path kickstarts a dead label. CLAUDE.md "Key Files: wingmen_orch.py — main entry point" is stale too.
- `comm` of installed vs repo `launchd/`: 64 installed `dev.wingmen.*` not in repo (many are `.bak-*` leftovers alongside live labels such as `fleet-console`, `fleet-health`, `nazim-session`, `agent-wake-subscriber`, `lane-watchdog`); 5 tracked plists differ byte-wise from the installed copy (`asks-daily-digest, context-health-watchdog, lane-proactive-recycle-nudge, queue-age-watchdog, secret-hash-sweep`).
- `dev.wingmen.agent-wake-subscriber` shows last exit status 1 in `launchctl list` (pid 63441 running — KeepAlive restart loop or a prior crash; not investigated further).
Fix sketch: a `scripts/launchd_drift_check.py` (fleet-health sweep) that diffs installed vs tracked, flags ProgramArguments paths that do not exist, and lists `.bak-*` leftovers; retire `dev.wingmen.orchestrator`/`watchdog` plists or point `restart_orch.sh` at the real label; fix CLAUDE.md Key Files.

#### [S-5] Bot tokens on the curl command line (visible in `ps`) in four senders
Impact: med  Effort: S
Evidence: `scripts/hk_send.sh:25` `curl -sS -X POST "https://api.telegram.org/bot${TOK}/sendMessage"`; `scripts/tg_send_file.sh:56` `curl ... "https://api.telegram.org/bot${TOK}/sendDocument"`; `scripts/health_check.sh:128` and `scripts/daily_backup.sh:80` `.../bot$BOT_TOKEN/sendMessage`. The two primary voices are clean: `tg_send.sh:89-96` and `nazim_send.sh:83-91` pass token/chat/text via env into `_tg_chunked_send.py`; `nervous_system/tg_out.py:229` uses urllib in-process. `vps_provision.sh:46` also embeds `GH_TOKEN` in the clone URL (persists in the VPS `.git/config` remote and `ps` during clone) when `gh auth` is unavailable.
Fix sketch: route the four through `_tg_chunked_send.py`/`tg_safe_upload.sh` with env-passed token, or `curl --config <(printf 'url = ...')`; in `vps_provision.sh` clone with `-c http.extraHeader` from a credential helper or `gh repo clone` only, and `git remote set-url` to the token-free URL after.

#### [S-6] Pen-(iv) gate fail-safe + role-scrub combine to allow `tg_send` from non-console processes on the Mini during a substrate outage
Impact: med  Effort: S
Evidence: `scripts/lib/orch_lease.py:253` the console refusal keys ONLY on `ORCH_BODY_ROLE=console` in the caller's env; `:262-268` any DB read failure → `return True, "lease-unreadable-failsafe"`. `scripts/launch_dangerous_cc.sh:188-193` deliberately scrubs `ORCH_BODY_ROLE`/`ORCH_AGENT_ID` from every lane env, so a lane on the Mini has role "" → treated as hub → only the host compare (`decide`, `:147-170`) refuses it, and that compare is skipped on the fail-safe branch. Today's belt is the DSN in the live `.env` (`tg_send.sh:11,21` hard-codes `ORCH_DIR=$HOME/wingmen/orchestrator`), so this only opens during a pooler/DB outage — exactly when the hub is also blind.
Fix sketch: in `check()`, before the fail-safe, refuse when `fleet_host_id()` != the last-known holder host cached on disk (`state/orch_lease_last_holder.json`, written on every successful check), or require `CC_BASE_AGENT_ID` empty AND role==hub for the fail-safe path.

#### [S-7] `spawn_uiux_review.sh` is an un-censused raw `send-keys` payload submit; `lane_watchdog.verified_enter` still sends a bare Enter
Impact: med  Effort: S
Evidence: `scripts/spawn_uiux_review.sh:75` `tmux send-keys -t "$SESSION" -l "$MSG"; sleep 1; tmux send-keys -t "$SESSION" Enter` — full prompt text typed into a lane pane (no C-u clear, no verified-submit, not routed through `lane_nudge.sh`), after a fixed `sleep 20` (`:66`) race on boot; not listed in `docs/send-keys-automation-census-2026-07-03.md`. `nervous_system/lane_watchdog.py:166` `tmux("send-keys","-t",sess,"Enter")` bare Enter (trust-prompt path; `dev.wingmen.lane-watchdog` is NOT loaded today per `launchctl list`, consistent with census row 1 "killed"). Remaining real callers all go C-u → `-l` count-line → Enter (`ingest.py:756-765`, `nazim_bus_notify.py:151-155`, `priority_sla_watchdog.py:513-517`, `lane_wedge_watchdog.py:795-799`, `backlog_swipe.py:54-56`, `lane_nudge.sh:505-519`, `nudge_cai.sh`) or type only a ghost sentinel/BSpace (`composer_capture.sh:479`, `irsyad_shadow_watch.py:220`, `reset_*.sh`, `switch_lane_token.sh:395`); `boot_nazim.sh:250` / `boot_fleet_health.sh:294` kick their OWN session at boot. `agent_wake.py:500-528` routes through `lane_nudge.sh` (census item 2 risk addressed).
Fix sketch: make `spawn_uiux_review.sh` post the brief as a bus row and nudge via `lane_nudge.sh` (count-only); add it and `lane_watchdog.verified_enter` to the census; add a test that greps `send-keys .* -l` callers against an allowlist.

#### [S-8] Lanes/boots source the live `.env` and the live `.venv` (not their worktree's) — acceptable, but 8 scripts bare-`load_dotenv()` from cwd and 61 load `<repo>/.env` relative to `__file__`, which is empty in 13/13 worktrees
Impact: low-med  Effort: S
Evidence: no sibling worktree has `.env` (survey above). `scripts/lib/orch_lease.py:74` `load_dotenv(ROOT/.env)` (ROOT = its own repo) → in a worktree `DATABASE_URL` is None; `scripts/bus_send.py:98,122,164,368` `psycopg2.connect(dburl(os.environ.get("DATABASE_URL")))` → clean exception (fail-closed, but with an unhelpful traceback). Bare cwd-relative `load_dotenv()` at `scripts/backfill_option_b_historical_bugs.py:22`, `scripts/exec_broker_daemon.py:152`, `scripts/asks_close.py:49`, `scripts/backlog_swipe.py:75`, `nervous_system/coordinator_pane_publisher.py:170` (+3 docstring mentions). 9 scripts hard-code `/Users/sheikhmusa/wingmen/orchestrator/.env` (e.g. `scripts/scheduled_cc_sweep.sh:62,157`). Lane launches are safe only because `launch_dangerous_cc.sh:113` sources `$ORCH_DIR/.env` where `ORCH_DIR` = the launcher's repo (live), and `lanes.sh:168` hard-codes the live venv.
Fix sketch: one `scripts/lib/env_resolve.py` that loads `<repo>/.env` then falls back to `~/wingmen/orchestrator/.env` (or `$ORCH_ENV_FILE`) and logs which it used; replace the 8 bare calls; `bus_send.py`/`orch_lease.py` print "no DATABASE_URL — is .env present in <root>?" instead of a raw psycopg error.

#### [S-9] Hard-coded Mac-Mini paths and tmux names in scripts expected on other hosts
Impact: low  Effort: S
Evidence: 84 files under scripts/nervous_system/launchd/.claude contain `/Users/sheikhmusa`; top code hits `scripts/scratch_toolpath_replay.py` (13), `nervous_system/autonomous_loop_detector.py` (9), `scripts/audit_drain_sweep.sh` (5), `scripts/scheduled_cc_sweep.sh:41` (`ORCH_DIR="/Users/sheikhmusa/wingmen/orchestrator"`), `scripts/restart_orch.sh:29,35`, 7× `scripts/run_*.sh` wrappers. Only `scripts/cc_session_costs_writer_run.py` is both hub-aware and hard-codes the path (`:13`, docstring). Stale `/Users/musa` in `scripts/audit_mac_mini.py:39`. Hard-coded tmux targets: `nudge_cai.sh` (=cai, sanctioned), `break_glass.sh` (4), `boot_*.sh` session names, `nervous_system/lane_watchdog.py:221-225` (`=orch:0.0`). `pkill`/`kill` surface is small and exact-match (`gzb-vpn.sh:60,62` `pkill -x openfortivpn`; `lanes.sh:64` `pgrep -f 'claude --dangerously-skip-permissions'` is read-only liveness, not a kill; `console_deploy_quality_gate_shadow.sh:50` `kill -9 "$pid"` of its own child).
Fix sketch: `ORCH_DIR="${ORCH_DIR:-$HOME/wingmen/orchestrator}"` in the 7 `run_*.sh` + `scheduled_cc_sweep.sh`; delete/relocate `scratch_toolpath_replay.py`; fix `audit_mac_mini.py:39`.

#### [S-10] Shell hygiene is mostly good; residual items
Impact: low  Effort: S
Evidence: of 122 `scripts/*.sh`, only 2 top-level scripts lack `set -e` (`sendkeys_catcher.sh`, `cubeasht_collect_wsl.sh:5` documented "no set -e"); 27 `scripts/lib/*.sh` are sourced libs (correctly no `set -e`); 2 lack `pipefail`. Safety scripts: `tg_send.sh:10`, `nazim_send.sh:16`, `restart_orch.sh:4`, `lanes.sh:24`, `log_console_msg.sh:9`, `nudge_cai.sh:11`, `boot_orch.sh:11` = `-euo pipefail`; `deploy_console.sh:29`, `launch_dangerous_cc.sh:38`, `boot_cai.sh:16`, `boot_fleet_health.sh:24`, `boot_nazim.sh:10`, `switch_lane_token.sh:45`, `safe_merge.sh:10` = `-uo pipefail` (no `-e`, deliberate explicit-exit style). No `eval`, no `chmod 777`, no `set -x`, no `| tail; echo $?` pattern found (30 `| tail/head` tails, none followed by `$?`); every `rm -rf "$VAR"` is `[ -n ]`-guarded or `mktemp`-derived (`daily_backup.sh:261,303`). `curl | sh` only in provisioning (`setup_mirror_wsl.sh:115`, `vps_provision.sh:36`, plus `setup_mirror_wsl.sh:62` `curl ... | sudo -E bash -`). Long-lived processes outside launchd: `scripts/self_recycle.sh:284-331` `nohup bash -c '...' & disown` (bounded by `MAX_WAIT`, intentional), heartbeat `&` loops inside `boot_*.sh`/`launch_dangerous_cc.sh:941` (children of the tmux session), `gzb-vpn.sh:78` `setsid openfortivpn` with dead-man (`:100`), `mirror_activate.sh:41`/`mirror_deactivate.sh:22` `boot_mirror.sh &`. `restart_orch.sh:9-21` actively refuses nohup. Sleep-then-check races: `spawn_uiux_review.sh:66` (`sleep 20` → `has-session`), `deploy_console.sh:135` (`sleep 3` → curl `/api/version`, result only logged), `switch_lane_token.sh` (4), `boot_nazim.sh`/`boot_fleet_health.sh` (3 each).
Fix sketch: pin `curl | sh` installers to a checksum; replace `deploy_console.sh:135-137` with a poll-until-version-matches loop (fail if `/api/version` != `$SW` after N s); `spawn_uiux_review.sh` poll `has-session` + composer-ready instead of fixed 20 s.

---

### Appendix A — git mutating ops found (scripts / launchd / cron)

| Where | Op | Target | Guarded? | Notes |
|---|---|---|---|---|
| `scripts/setup_mirror_wsl.sh:75` | `git -C "$ORCH_DIR" pull --ff-only` | mirror WSL box, not Mini live | `--ff-only`, failure → warn | provisioning only |
| `scripts/vps_provision.sh:44-46` | `gh repo clone` / `git clone -b $REPO_BRANCH https://x-access-token:${GH_TOKEN}@…` | VPS fresh clone | n/a | token in URL (S-5) |
| `scripts/irsyad_spin_worker.py:133` | `git -C IHSANOS_REPO worktree add -B <branch> <wt> origin/main` | ihsanos repo worktrees | new dir only | not orchestrator |
| `scripts/agent_worktree_reaper.py:182,234` | `git worktree remove` (no --force), `git worktree prune` | `.claude/worktrees/agent-*` | clean-tree + liveness + 6h floor | reversible by design |
| `scripts/repo_hygiene_sweep.py:139` | `git push origin <branch>` | managed repos | branch list | push only |
| `nervous_system/repo_context_writer.py:163` | `git fetch origin main --quiet` | managed repos | read-only ref update | safe |
| `scripts/gazzabyte_board_autopublish.py:71-72` | string in a degrade message only (`git checkout origin/… -- BOARD`) | — | — | not executed |
| `scripts/deploy_console.sh:30` | **no git op**; `cd ~/wingmen/orchestrator` + hash + kickstart | live working tree | no clean-tree check | S-2 |
| `~/Library/LaunchAgents/*.plist` | none run git mutations (only comments mention it) | — | — | `live-checkout-branch-watchdog` detects branch drift |
| `crontab -l` | "no crontab for sheikhmusa" | — | — | — |
| Live checkout reflog | 25× hand-typed `pull --ff-only` (17:44→03:19 +08) | live checkout | `--ff-only` | undocumented, no clean-tree pre-check |

Protective controls present: `scripts/git-hooks/post-checkout` (pages, never blocks), `scripts/live_checkout_branch_watchdog.py:99` (pages on branch != `fable/substrate-safe-fixes` >120 s), `pre-commit` (blocks SRE commits in live), `pre-push` (blocks un-collectable tests / unreviewed console).

### Appendix B — `.venv` assumption inventory (265 lines; by file, count)

| File | n | Resolves to |
|---|---|---|
| `.claude/settings.json` | 6 | `$CLAUDE_PROJECT_DIR/.venv` (worktree — S-1) |
| `.claude/settings.local.json:15` | 1 | live absolute (untracked) |
| `scripts/git-hooks/post-checkout:52`, `pre-push:94` | 2 | `$ROOT/.venv` (worktree; pre-push falls back to system python) |
| `scripts/lane_nudge.sh` 7, `tg_send.sh` 6, `nazim_send.sh` 6, `boot_orch.sh` 6, `oeh_send.sh` 4, `finance_send.sh` 4, `reviewer_send.sh` 3, `mamadah_send.sh` 3, `lane_reply.sh` 3, `deploy_console.sh` 3, `coffeemedia_send.sh` 3, `caai_send.sh` 3, `boot_nazim.sh` 3, `boot_fleet_health.sh` 3, `tg_send_file.sh` 2, `switch_lane_token.sh` 2, `self_recycle.sh` 2, `scheduled_cc_sweep.sh` 2, `reviewer_send_file.sh` 2, `reset_nazim.sh` 2, `reset_fleet_health.sh` 2, `reset_cai.sh` 2, `nazim_say.sh` 2, `mamadah_send_photo.sh` 2, `irsyad_support_send*.sh` 4, `cosem_*_support_send.sh` 4, `console_deploy_quality_gate_shadow.sh` 2, `cai_send.sh` 2, `boot_quality.sh` 2, `boot_cai.sh` 2, `angullia_send.sh` 2, `spawn_uiux_review.sh` 1, `safe_merge.sh` 1, `secret_hash_sweep_run.sh` 1, `run_*.sh` 3 | ~110 | `$ORCH_DIR/.venv` where `ORCH_DIR=$HOME/wingmen/orchestrator` (live) or `dirname(script)/..` (own repo) |
| `scripts/launch_dangerous_cc.sh:44` | 1 | launcher's own repo (live for lanes) |
| `scripts/lanes.sh:168` | 1 | live absolute |
| `scripts/setup_mirror_wsl.sh` 4, `vps_provision.sh` 3 | 7 | creates `.venv` on the remote host (`vps_provision.sh:53`) |
| Python: `reconcile_responded_at.py` 4, `worktree_nodemodules_reaper.py` 3, `preview_devserver_reaper.py` 3, `node_modules_content_dedup.py` 3, `lib/fire_window.py` 3, `agent_worktree_reaper.py` 3, `nervous_system/tg_out.py` 3, `provider_policy_backfill_check.py` 2, `mark_shadowed_tg_media_rows.py` 2, `hooks/console_irsyad_guard.py` 2, `file_history_prune.py` 2, `disk_autoremediate.py` 2, `nervous_system/singleton_liveness.py` 2, `nervous_system/orch_self_audit.py` 2, `verify_fleet_token.py` 1, `session_start_reconstitute.py` 1, `scope_tg_media_collisions.py` 1, `lib/pin_fleet_host_id.sh` 2, `ci/check_lock_keys.sh` 2 | ~45 | mostly `sys.executable`/path strings for subprocess; untested here |

Creation/verification of a worktree `.venv`: **none** (no script, no doc; `vps_provision.sh:53` is the only `python -m venv` and targets the VPS clone).

### Appendix C — send-keys callers vs census (docs/send-keys-automation-census-2026-07-03.md)

Sanctioned/census-consistent: `ingest.py:756-765` (count-only), `nudge_cai.sh` (count-only, =cai), `lane_nudge.sh:505-519` (C-u→-l→Enter→verify), `nazim_bus_notify.py:151-155`, `fleet_health_bus_notify.py` (via nudge), `priority_sla_watchdog.py:513-517`, `lane_wedge_watchdog.py:795-799`, `backlog_swipe.py:54-56`, `agent_wake.py:500-528` (routes to lane_nudge), `context_health_watchdog.py:821,829` (CAI-500 /clear executor), `composer_capture.sh:479` + `irsyad_shadow_watch.py:220` (ghost sentinel probe), `reset_*.sh`/`switch_lane_token.sh:395` (BSpace wipes inside reset flows), `boot_nazim.sh:250`/`boot_fleet_health.sh:294` (own-session boot kick), `sendkeys_catcher.sh` (detector). Not in census / raw payload: `spawn_uiux_review.sh:75` (S-7); `lane_watchdog.py:166` bare Enter (daemon not loaded). Census rows 5-7 (deleted bridges) confirmed absent.

---

# 6. Dead/duplicate scripts, overlaps and top-10 simplifications (auditor F)

## F — Dead/duplicate scripts, overlapping watchdogs, top simplifications

Read-only audit of `/Users/sheikhmusa/wingmen/orchestrator.wt-fable-3` (branch `fable/fleet-code-audit-20261027`), 2026-10-28. Mechanical reference check: `scratchpad/refcheck2.py` (git grep on basename + module stem across all 1982 tracked files + the 110 installed `~/Library/LaunchAgents/*.plist`); raw output `scratchpad/refs.json`. No crontab exists (`crontab -l`: none). pytest `--collect-only` could not run (conftest fail-closes because `DATABASE_URL` is the prod substrate — itself correct); a static import scan of `tests/` substituted.

### Headline numbers

| Metric | Value |
|---|---|
| Tracked files / .py files | 1982 / 885 (of which `legacy/` holds `cto_bot.py` 3986L + `wingmen_orch.py` 2302L) |
| `scripts/` + `scripts/lib` + `scripts/hooks` + `nervous_system/` entries checked | 443 (96,055 lines) |
| Referenced by **nothing** | 4 |
| Referenced **only** by docs/reports/tests (never executed by code, plist, hook) | 58 (8,627 lines incl. the 4) |
| Installed `dev.wingmen.*` plists | 110 files, of which 22 are `.bak-*`/`.disabled-*`/`.pre-*` litter |
| Installed plists NOT tracked in repo `launchd/` | 44 (plus repo has 3 plist dirs: `launchd/` 60, `ops/launchd/` 28, `scripts/launchd/` 1 — overlapping, drifting) |
| Installed plists whose target file no longer exists in the live checkout | 2 (`dev.wingmen.orchestrator` -> `wingmen_orch.py`, `dev.wingmen.watchdog` -> `watchdog.py`; both moved to `legacy/`) |
| Per-file `_dsn()`/`_connect()` helper definitions | **80 definitions in 71 files**; `psycopg.connect(` appears 275x in 161 files; only 11 files use the sanctioned `scripts/lib/substrate_dsn.dsn_from_env_file`, 136 still read `os.environ["DATABASE_URL"]` directly (op#24342 "file-first" fix never propagated) |
| Scheduled watchdog/monitor/reaper/sweep scripts | ~60 distinct entrypoints across ~85 loaded launchd jobs |
| Telegram send scripts | 24 `scripts/*_send*.sh` (1,483 lines) + 21 files calling `api.telegram.org` directly |
| Migrations | 119 files; 3 number collisions (037, 043, 072); gaps at 002, 025–029, 081, 083, 122–124 |

---

### Top 10 simplifications

#### [X-1] One `scripts/lib/db.py` connect helper replacing 80 hand-rolled `_dsn()`/`_connect()` copies
**Impact:** High — the 2026-10 pooler circuit-breaker incident (op#24342) was caused by stale env-first DSNs in long-lived pollers; the fix (`dsn_from_env_file`) reached 11 of ~147 call sites. Every rotation re-exposes the other 136.
**Effort:** M (mechanical, test-covered per module; `scripts/lib/substrate_dsn.py` already exists as the core)
**Evidence:** `grep -n "^def _dsn\|^def _connect"` -> 80 defs in 71 files (list in Appendix C). `dsn_from_env_file` adopters: 11 files. Raw `os.environ...DATABASE_URL` readers: 136 files. `psycopg.connect(`: 275 occurrences / 161 files; `psycopg2.connect(`: 44 / 26 (two driver generations coexist).
**Fix sketch:** `scripts/lib/db.py` exposing `connect(role="substrate"|"ro"|"silo:<ref>", *, autocommit=False)` + `dsn(role)` that wraps `substrate_dsn.dsn_from_env_file`, applies the residency guard (`lane_db_residency_guard.py`), and sets `connect_timeout`/`application_name` once. Delete each module-local `_dsn`. Add a lint test (`tests/test_no_local_dsn.py`) asserting no new `def _dsn` outside `scripts/lib/`.

#### [X-2] Collapse the context-bloat/recycle sextet into one `lane_context_policy` tick
**Impact:** High — six launchd jobs read the same `cc_session_costs`/pane gauge for the same lanes with six different bars (70/80/80/85/88/90/95%) and three different "act" paths; proven double-nudge/race surface and a 5,500-line maintenance load.
**Effort:** L
**Evidence:** `scripts/context_health_watchdog.py` 2099L (`_HARD=0.80`, `_NUDGE_PCT=0.70`, `_NUDGE_BACKSTOP_PCT=95`, `_EXT_RECYCLE_PAGE_PCT=95`; iv=600s) · `scripts/auto_recycle_on_bloat.py` 518L (`FIRE_THRESHOLD=88`, `HARD_LINE=95`, `PANE_FIRE_K=650`; iv=300) · `scripts/lane_selfrecycle_detect.py` 462L (`BLOAT_PCT=90`; iv=600; docstring: "fixes two root-cause bugs in sre_lane_recycle.py") · `scripts/lane_proactive_recycle_nudge.py` 296L (`PROACTIVE_PCT=80`; iv=300) · `scripts/lane_recycle_deadman.py` 356L (`HARD_PCT=85`, `SUSTAIN_S=1800`; iv=300) · `scripts/sre_lane_recycle.py` 850L (`_CTX_HARD=0.80`; iv=300) · plus `scripts/checkpoint_recycle_driver.py` 408L (no plist; "Phase 2" driver). Each docstring describes itself as the layer the previous one missed ("busy lanes invisible to all proactive detectors, only the dead-man reaches it").
**Fix sketch:** One module `nervous_system/lane_context_policy.py` with a single gauge read per lane per tick and an explicit ladder table `[(70,"nudge"),(80,"nudge_busy_ok"),(85,"page_sustained"),(88,"recycle_if_idle_clean_handoff"),(95,"page_P1")]`, one state file, one dedupe. Keep `sre_liveness_watchdog.py` separate (it watches the watcher). Retire 5 plists.

#### [X-3] Collapse the `*_bus_notify.py` quartet into one `bus_notify.py --target` (or fold into `agent_wake_subscriber`)
**Impact:** Medium-High — 4 KeepAlive daemons + 4 boot wrappers + 4 plists doing identical poll-max(id)-debounce-nudge; three of them are already copy-paste of each other and the fourth (nazim) diverged by 269 lines of local fixes the others never got.
**Effort:** S
**Evidence:** `nervous_system/cai_bus_notify.py` 114L, `finance_bus_notify.py` 102L, `fleet_health_bus_notify.py` 104L, `nazim_bus_notify.py` 247L (`diff cai finance` = 82 changed lines, almost all docstring; the only functional delta is `TARGET` and the injector: `nudge_cai.sh` vs `lane_nudge.sh` vs tmux `nazim`). Wrappers `scripts/boot_{cai,finance,fleet_health,nazim}_bus_notify.sh` 10–12L each. All four also duplicate the realtime doorbell `nervous_system/agent_wake_subscriber.py` (124L) and the floor `wake_backstop_sweep.py` (1399L) — that is **six** "new bus row -> poke recipient" mechanisms.
**Fix sketch:** `nervous_system/bus_notify.py` with a `TARGETS = {agent_id: (tmux_session, injector, poll_s, debounce_s)}` table read from `fleet_lanes`/`protected_agents`, one daemon, one plist. Then evaluate deleting it entirely in favour of `agent_wake_subscriber` + `wake_backstop_sweep` (already covers "directed row rotting unread" for every recipient).

#### [X-4] One scheduler/registry for watchdogs (a `fleet-health` tick) instead of ~60 launchd jobs
**Impact:** High — 85 loaded `dev.wingmen.*` jobs, 44 of them untracked in repo `launchd/`, 22 `.bak` siblings, 3 competing repo plist dirs; every new op# fix adds a plist + a `run_*.sh`/`boot_*.sh`/`*_run.sh` wrapper (3 naming conventions) whose only job is `cd; source .env; exec python -m X`.
**Effort:** L (but incremental: move one job per PR)
**Evidence:** Installed plist map in Appendix D. Wrapper-only shells: `scripts/run_*.sh` (6), `scripts/boot_*_bus_notify.sh` (4), `scripts/*_run.sh` (4), `scripts/boot_singleton_liveness.sh`, `boot_wake_backstop_sweep.sh`, `boot_media_reaper.sh`, `boot_gzb_media_reaper.sh` ... ≈20 files of 9–40 lines each. `scripts/fleet_health.py` already exists as a 10-min sweep (iv=600) with numbered steps (step 6 `sequence_drift()`), i.e. the registry pattern is already half-built.
**Fix sketch:** `nervous_system/scheduler.py` with `JOBS = [Job(name, callable, every_s, lease="fleet-health"|None)]`; one KeepAlive plist; each former script becomes a `run()` function. Keep KeepAlive daemons (ingest, tg_out, console, wake subscriber) as separate plists. Delete `ops/launchd/` and `scripts/launchd/`; make `launchd/` + an `install_launchd.sh` the single source, and have `fleet_health.py` assert installed == repo.

#### [X-5] Delete the dead/legacy tree and the 2 ghost plists that point at it
**Impact:** Medium — 6,288 lines in `legacy/` plus ~1,900 lines of `nervous_system/` modules whose only runner is `legacy/wingmen_orch.py`/`legacy/watchdog.py`; installed `dev.wingmen.orchestrator.plist` and `dev.wingmen.watchdog.plist` target files that no longer exist (`/Users/sheikhmusa/wingmen/orchestrator/wingmen_orch.py`, `.../watchdog.py`), so a `launchctl load` would crash-loop. CLAUDE.md still names `wingmen_orch.py` as "main orchestrator entry point".
**Effort:** S
**Evidence:** `legacy/` = 37 entries incl. `cto_bot.py` 3986L, `wingmen_orch.py` 2302L. Modules referenced only from `legacy/` + tests/comments: `nervous_system/agent_watchdog.py` 512L (last 06-18), `long_caller_watchdog.py` 305L (05-26), `queue_stall_detector.py` 153L (05-07), `strategic_decisions_poll.py` 235L (04-16), `bug_reports_poll.py` 229L (05-08), `agent_messages_poll.py` 383L (05-17, only ref `AGENTS.md`), `operator_notify.py` 33L. `launchctl list` confirms neither ghost job is loaded today.
**Fix sketch:** `git rm -r legacy/` + the 7 modules + their tests (`tests/test_long_caller_watchdog*.py`, `test_queue_stall_detector.py`, `test_strategic_decisions_poll.py`); `launchctl bootout` + `rm` the 2 ghost plists; fix CLAUDE.md "Key Files".

#### [X-6] Delete finished one-off scripts and the 2026-10-06 orphans nobody actioned
**Impact:** Medium — 58 scripts (8.6k lines) are executed by nothing; the prior audit (`reports/fable-audit-substrate-20261006/_orphans.json`, `D-accretion-ci-secrets.md`) already listed most of them 3 weeks ago and the count did not go down. Several embed client/silo names and DSN logic, widening the secret-handling surface the `secret_hash_sweep` has to police.
**Effort:** S
**Evidence:** Appendix A (full list with last-touch). Clear "job done" one-offs: `scripts/lib/irsyad_purge_executed_20260703.py` (name says executed), `backfill_option_b_historical_bugs.py` (04-29), `cc_session_costs_backfill.py`, `apply_fleet_lane_autoscale_log.py`, `mark_shadowed_tg_media_rows.py`, `reclassify_client_asks.py`, `scope_tg_media_collisions.py`, `backfill_ask_delivered_20261027.py` (0 refs), `sweep_persisted_output_secrets.py` ("one-off remediation sweep"), `scratch_toolpath_replay.py`, `scratch_shape_replay.py`, `scripts/dev.wingmen.residency-sweep.plist` (0 refs, wrong `/Users/Musa` path), `render_office.py`/`render_refresh.py` (0 refs), `watchdog_tripwire_report.py` (+24/48/72h after a 2026-05-26 kickstart; 3 plists still installed). Also temporary-by-docstring but still scheduled: `pooler_authfail_tripwire.py` ("TEMPORARY ... REMOVE this + its launchd job"), `op11911_syed_trigger.py` (plist installed, iv=300), `remind-musa-20261015.plist` (self-deleting one-shot that did not delete).
**Fix sketch:** one PR `chore: remove executed one-offs`; move anything with historical value into `docs/runbooks/` as prose. Add `tests/test_no_orphan_scripts.py` running `refcheck2.py` logic in CI with an allowlist, so the orphan count can only go down.

#### [X-7] One parameterised Telegram sender replacing 24 `*_send*.sh` + 21 direct `api.telegram.org` callers
**Impact:** Medium — the no-dupe retry contract (`scripts/lib/tg_group_send.py`, Nazim 38090/38094) exists in exactly one path; the other 20 callers each re-implement `curl`/`urlopen` with their own timeout, chunking, and leak-guard wiring, so a fix lands in one file at a time.
**Effort:** M
**Evidence:** `ls scripts/*_send*.sh` = 24 files / 1,483 lines (`angullia_send.sh` vs `coffeemedia_send.sh` differ by 45 lines, mostly header prose + token var name; `oeh_send_photo.sh` vs `nazim_send_photo.sh` same). Direct `api.telegram.org` callers: 21 (Appendix C). Four Python send layers: `nervous_system/tg_out.py` 566L (gateway), `scripts/_tg_chunked_send.py` 317L, `scripts/lib/tg_group_send.py` 257L, `nervous_system/operator_notify.py` 33L (legacy shim) + `alert_format.py` 59L.
**Fix sketch:** `scripts/tg.sh <channel_key> [--file|--photo] <text>` that resolves bot token + chat id from the `bot_channels` registry (already the SSOT per TG2.0), enforces the gates (`client_send_leak_guard.sh`, `console_irsyad_client_send_gate.sh`) once, and calls `tg_group_send.py` for the wire. Keep `tg_send.sh`/`nazim_send.sh` as 3-line aliases (doctrine references them by name).

#### [X-8] Split the three 2,000-line watchdogs along their already-visible seams
**Impact:** Medium — `priority_sla_watchdog.py` (2426L, 66 top-level defs), `context_health_watchdog.py` (2099L, 55), `lane_wedge_watchdog.py` (2082L, 54) each carry detection + lease gating + tmux probing + paging + state file + CLI in one module; tests for them are 1,965L. Every op# fix lands as a new top-level function and a new env knob (`context_health_watchdog.py` alone has >15 `CTX_WD_*` env vars).
**Effort:** M
**Evidence:** `wc -l` + `grep -c '^def '` above; `nervous_system/console/app.py` 3696L/65 defs and `ingest.py` 1628L are the next two.
**Fix sketch:** extract shared `nervous_system/lane_probe.py` (tmux capture, composer-ghost detection — currently `scripts/lib/composer_capture.sh` 711L of bash is reimplemented in Python inside wedge/idle/sla), `nervous_system/paging.py` (dedupe + cooldown + bus row), and keep each watchdog to its predicate.

#### [X-9] Single constants module for project refs / agent ids / channel keys
**Impact:** Medium — LAYER-VOCAB-001 says data refs must be exact, yet the refs are string literals scattered across code, so a silo repoint is a fleet-wide grep.
**Effort:** S
**Evidence:** `tscuymavysscrvoberrr` 21 occurrences / 16 files; `ceayjeamtmcyzzvqflus` 14 / 12; `goumlynecruxrlmzlntp` 10 / 10; `'cc-orchestrator'` 66 / 30 files; `'orch-console'` 112 / 53 files; `MUSA_TELEGRAM_ID` 60 / 24 (code only, excl. tests/legacy). `docs/data-store-registry.md` is the documented SSOT and `scripts/gen_data_store_registry.py` exists but is referenced only by docs.
**Fix sketch:** `scripts/lib/fleet_consts.py` (`SUBSTRATE_REF`, `SILOS = {...}`, `HUB_ID`, `CONSOLE_ID`, channel keys) generated from/validated against `docs/data-store-registry.md` and the `bot_channels`/`fleet_lanes` tables by `gen_data_store_registry.py`; a lint test forbids the raw literals outside that module and migrations.

#### [X-10] Migrations: fix numbering collisions and make `apply_migration.py` the only entry
**Impact:** Low-Medium — three duplicate prefixes (`037_admin_mark_offline.sql`/`037_pool_pace.sql`, `043_agents_single_owner_repo_scope_guard.sql`/`043_pane_context_pct.sql`, `072_oeh_bot_channel.sql`/`072_operator_asks_tracking.sql`) mean "migration 043" is ambiguous in bus rows and gate requests (same rejection class CLAUDE.md applies to unpinned migrations). Three bespoke appliers coexist with the generic one.
**Effort:** S
**Evidence:** `ls migrations | grep -oE '^[0-9]+' | uniq -d`; gaps 002, 025–029, 081, 083, 122–124 (the last documented in commit `d1941bc migration 122 -> 125`). Appliers: `scripts/apply_migration.py` 848L (generic, gated) + `apply_orch_lease.py` 27L, `apply_fleet_health_lease.py` 48L, `apply_fleet_lane_autoscale_log.py` 58L (the last two reference-free except docs). `migrations/drafts/` and `migrations/infra/` subdirs are outside the numbering. DB-side applied-table check skipped (no read-only helper identified in-budget).
**Fix sketch:** rename the second file of each collision pair to a free number with a redirect note in the header; delete the three bespoke appliers; have `apply_migration.py --lint` fail on duplicate prefixes (one `sorted/uniq -d` check).

---

### Overlap groups (same condition detected by several mechanisms)

| Group / condition | Mechanisms (file, lines, cadence) | Overlap / race |
|---|---|---|
| **Lane context bloat -> nudge/recycle** | `context_health_watchdog.py` 2099L/600s · `auto_recycle_on_bloat.py` 518L/300s · `lane_selfrecycle_detect.py` 462L/600s · `lane_proactive_recycle_nudge.py` 296L/300s · `lane_recycle_deadman.py` 356L/300s · `sre_lane_recycle.py` 850L/300s · `checkpoint_recycle_driver.py` 408L (manual) · `self_recycle.sh` 333L | 7 readers of one gauge; bars 70/80/80/85/88/90/95%; three can nudge the same lane in the same 5-min window (proactive@80, context@70-nudge, deadman page@85). |
| **Lane idle/wedged with unread work -> nudge** | `nervous_system/lane_wedge_watchdog.py` 2082L/60s · `nervous_system/lane_watchdog.py` 399L/300s (`IDLE_NUDGE_THROTTLE=600`, raw `send-keys Enter` at L166) · `scripts/idle_with_work_watchdog.py` 237L/900s · `scripts/priority_sla_watchdog.py` 2426L/90s (re-nudges target on SLA breach) · `nervous_system/wake_backstop_sweep.py` 1399L (KeepAlive) · `scripts/hub_bus_currency_monitor.py` 362L/1800s (hub only) · `scripts/fleet_stall_watch.py` 149L/1200s (zombie: heartbeat but no output) | Five independent nudgers of the same pane; `lane_watchdog.py` still uses bare `send-keys Enter`, the exact pattern `lane_nudge.sh` (533L) was written to forbid (CAI-RESP-284). |
| **New bus row -> wake recipient** | `agent_wake_subscriber.py` 124L (realtime WS) · `wake_backstop_sweep.py` 1399L · `cai_bus_notify.py` · `finance_bus_notify.py` · `fleet_health_bus_notify.py` · `nazim_bus_notify.py` · (legacy) `agent_messages_realtime.py` 367L, `agent_messages_poll.py` 383L | 6 live paths; the four notifiers are per-recipient forks of one loop. |
| **Body dead / stalled** | `nervous_system/singleton_liveness.py` 448L/300s (process+tmux death of protected singletons) · `scripts/sre_liveness_watchdog.py` 355L/180s (SRE wedged) · `scripts/hub_self_recovery_staleness_watchdog.py` 161L/120s (hub timer dead) · `nervous_system/agent_watchdog.py` 512L (heartbeat stale; legacy-only runner) · `scripts/channel_liveness_watchdog.py` 217L/120s (TG poll wedged) · `scripts/health_check.sh` /300s (disk check "DEAD since 07-02" per `disk_space_monitor.sh` header) | Reasonable split by target, but 4 state files + 4 page dedupe implementations. |
| **Disk pressure** | `disk_space_monitor.sh` 121L/600s · `boot_disk_autoremediate.sh` /300s · `worktree_nodemodules_reaper.py` 332L/1800s · `agent_worktree_reaper.py` 312L/360s · `preview_devserver_reaper.py` 468L/1800s · `node_modules_content_dedup` /3600s · `file-history-prune` /600s · `log-rotate` /21600s · `media-cleanup` (daily) + `media_reaper.py` (download retry, different job despite name) · `health_check.sh` (wrong volume) | 8 jobs, 3 tiers of thresholds, each with its own `df` parse. |
| **Client message unanswered** | `gazzabyte_request_nodrop_watch.py` 192L/600s · `client_channel_nodrop_watch.py` 352L/600s (docstring: "generalizes the SAME pattern") · `supervised_draft_deadman.py` 124L/600s · `irsyad_shadow_watch.py` 334L (plist `.disabled-obsolete`) | gazzabyte-specific watch is now a strict subset of the generic one -> delete it. |
| **Queue / dispatch ageing** | `queue_age_watchdog.py` 284L/300s · `priority_sla_watchdog.py` (bus SLA) · `queue_stall_detector.py` 153L (legacy jobs table) · `programme_stall_guard.py` 263L/3600s + daily digest · `commitment_sweeper.py` 401L/24s | Different tables, but 3 separate page/dedupe stacks. |
| **Telegram outbound** | `tg_out.py` 566L (gateway + daily dead-replay) · `tg_send.sh` 127L · `nazim_send.sh` 131L · `tg_send_file.sh` 76L · `_tg_chunked_send.py` 317L · `lib/tg_group_send.py` 257L · `operator_notify.py` 33L · `alert_format.py` 59L · 20 client `*_send*.sh` · 21 direct `api.telegram.org` callers | Chunking implemented in `_tg_chunked_send.py` and `tg_out.py`; no-dupe retry only in `tg_group_send.py`. |
| **DB connect** | 80 `_dsn`/`_connect` defs; `scripts/lib/substrate_dsn.py` (file-first) used by 11 | see X-1 |
| **Audit-board drain** | `audit_drain_sweep.sh` 136L ("GENERIC per-auditor") · `cc_storefront_audit_drain_sweep.sh` 111L (the pre-generalisation copy, still scheduled /43200s) · `scheduled_cc_sweep.sh` 182L | storefront copy is the template the generic one replaced; both plists still loaded. |
| **Plist sources** | `launchd/` 60 · `ops/launchd/` 28 · `scripts/launchd/` 1 · `scripts/dev.wingmen.residency-sweep.plist` · 110 installed (44 untracked, 22 .bak) | No single source of truth for what runs. |

---

### Dead-script summary

- **(a) referenced by nothing (4):** `scripts/backfill_ask_delivered_20261027.py` (55L), `scripts/dev.wingmen.residency-sweep.plist` (15L, hard-codes `/Users/Musa/...`), `scripts/render_office.py` (117L), `scripts/render_refresh.py` (62L).
- **(b) referenced only by docs/reports/tests — never executed by code, plist, hook, CLAUDE.md-as-command (58, 8,627L):** full list Appendix A. Note: ~12 of these are legitimately operator-invoked CLIs (`restart_orch.sh` is named in CLAUDE.md Rules; `seed_lane_claude_md.py`, `memory_recall.py`, `onboard_client_channel.py`, `coord_golive.py`, `migration_rls_grant_lint.py`, `arm_glm_image_read_guard.sh`, `break_glass*.sh`, `vps_provision.sh`, `lib/pgtest_reaper.py` via conftest) — keep those; the remaining ~46 are candidates.
- **(c) one-off op#/fix/apply/backfill/scratch scripts (16 by name pattern):** Appendix B. 11 of 16 are not referenced by any executor. Three "TEMPORARY" ones still have loaded launchd jobs (`pooler_authfail_tripwire`, `op11911_syed_trigger`, `tripwire-24h/48h/72h`).
- **Legacy-only runners (7 modules, ~1,850L):** `nervous_system/{agent_watchdog,long_caller_watchdog,queue_stall_detector,strategic_decisions_poll,bug_reports_poll,agent_messages_poll,operator_notify}.py` — their only non-test, non-doc importer is `legacy/wingmen_orch.py` or `legacy/watchdog.py`, whose installed plists point at paths that no longer exist.
- **Tests for deleted code:** static scan of 474 test files found only 1 missing path reference (`scripts/some_unrelated_tool.sh` in `tests/test_pre_push_new_branch_base.py` — a deliberate fixture). Tests that still exercise legacy-only modules: `tests/test_long_caller_watchdog*.py`, `test_queue_stall_detector.py`, `test_strategic_decisions_poll.py`, `test_agent_messages_realtime*.py` (would go with X-5).
- **Prompt/template sprawl:** none — `prompts/` has 1 file (`irsyad_worker_prompt.md`, used by `deploy/irsyad_worker_supervisor.sh`), `templates/` has 1 (`lane_claude_template.md`, used by `seed_lane_claude_md.py` + CLAUDE.md). CLAUDE.md "Key Files" still describes `prompts/` as "session prompt templates" (stale).

---

### Appendix A — scripts referenced only by docs/reports/tests (58)

```
last        lines  path                                              example ref
2026-04-16   88    scripts/restart_orch.sh                           CLAUDE.md (operator CLI - keep)
2026-04-16  151    scripts/run_phase0_drills.py                      reports/backend-review/...
2026-04-18  762    scripts/audit_mac_mini.py                         docs/status-history.md
2026-04-20  350    scripts/verify_governance_hygiene_batch.py        docs/status-history.md
2026-04-29  104    scripts/backfill_option_b_historical_bugs.py      docs/status-history.md
2026-05-09   89    scripts/measure_supabase_branch_coldstart.py      docs/superpowers/runbooks/ci-live-db-setup.md
2026-05-11  124    scripts/record_session_cost.py                    reports/.../_orphans.json
2026-05-28   98    scripts/cc_session_costs_backfill.py              docs/superpowers/plans/...
2026-06-18  100    scripts/token_usage.py                            reports/.../D-accretion-ci-secrets.md
2026-07-03   42    scripts/setup_tailscale_system_daemon.sh          reports/...
2026-07-03   79    scripts/watch_fleet.py                            reports/.../_orphans.json
2026-07-03  137    scripts/lib/irsyad_purge_executed_20260703.py     reports/...
2026-07-08   47    scripts/break_glass.sh                            reports/...  (keep: DR)
2026-07-11   70    scripts/breakglass_studio_orch.sh                 reports/...  (keep?: DR)
2026-07-22   78    scripts/quality_gate_check.sh                     reports/...
2026-07-22   99    nervous_system/front_door.py                      reports/.../B-velocity-inert.md
2026-07-25   99    scripts/lane_drill_seed.py                        docs/status-history.md
2026-07-26   41    scripts/cai_send.sh                               reports/.../_orphans.json
2026-07-26  123    scripts/check_send_paths_report_failure.py        reports/.../_orphans.json
2026-07-26   50    scripts/nazim_model.sh                            reports/...
2026-08-05   48    scripts/finance_send.sh                           reports/.../_orphans.json
2026-08-05   69    scripts/vps_provision.sh                          reports/...  (keep: provisioning)
2026-08-05  120    scripts/weekly_substrate_audit_gate.py            reports/.../_orphans.json
2026-08-06  322    nervous_system/qwen_lane.py                       reports/...
2026-08-11   55    nervous_system/wake_self_drive.py                 reports/.../_orphans.json
2026-08-16   79    scripts/spawn_uiux_review.sh                      docs/ihsan-gate-manifest.yaml
2026-08-17  142    scripts/lib/a3_rls_load_bearing.py                reports/.../_orphans.json
2026-08-20  388    scripts/ab_qwen_opus.py                           reports/...
2026-08-20  540    scripts/harvest_sft_dataset.py                    data/sft_testgen_dataset.README.md
2026-09-12   58    scripts/apply_fleet_lane_autoscale_log.py         reports/...
2026-09-16  311    scripts/lib/pii_safe_file_inspector.py            reports/.../_orphans.json
2026-09-21  168    scripts/hub_ctx_publish.py                        reports/.../_orphans.json
2026-09-24   79    scripts/cc_cost_snapshot.py                       reports/...
2026-09-26  148    scripts/seed_lane_claude_md.py                    CLAUDE.md (operator CLI - keep)
2026-09-27  256    scripts/gated_inbound_digest.py                   reports/.../_orphans.json
2026-10-01  162    scripts/mark_shadowed_tg_media_rows.py            reports/...
2026-10-01  161    scripts/reclassify_client_asks.py                 reports/.../_orphans.json
2026-10-01   81    scripts/render_pdf_remote.sh                      reports/...
2026-10-02   51    scripts/coffeemedia_send.sh                       reports/...
2026-10-02   58    scripts/scratch_toolpath_replay.py                reports/.../_orphans.json
2026-10-04  237    scripts/sweep_persisted_output_secrets.py         reports/.../_orphans.json
2026-10-06   19    scripts/lib/vault_get_proof_cli.py                reports/...
2026-10-07   61    scripts/dev_group_edit.sh                         reports/...
2026-10-07   33    scripts/hk_send.sh                                reports/...
2026-10-07   68    scripts/mamadah_send_photo.sh                     reports/...
2026-10-07   44    scripts/nazim_send_file.sh                        reports/.../_orphans.json (keep: operator CLI)
2026-10-07   72    scripts/hooks/ddl_detect_vectors.json             tests/ (fixture - keep)
2026-10-12  312    scripts/instance_id_dup_detector.py               tests/ only
2026-10-13  107    scripts/arm_glm_image_read_guard.sh               tests/ only (operator CLI - keep)
2026-10-19  392    scripts/onboard_client_channel.py                 reports/... (operator CLI - keep)
2026-10-21  186    scripts/coord_golive.py                           reports/... (operator CLI - keep)
2026-10-22  201    scripts/migration_rls_grant_lint.py               docs/playbooks/03-... (keep)
2026-10-22   57    scripts/migration_rls_grant_lint_vectors.json     tests/ (fixture - keep)
2026-10-24   81    scripts/gen_data_store_registry.py                docs/DATA-PROVENANCE.md (keep; see X-9)
2026-10-24  140    scripts/provider_policy_backfill_check.py         tests/ only
2026-10-24   47    scripts/lib/durable_inbox_reconciler.py           tests/ only
2026-10-27  166    scripts/memory_recall.py                          tests/ only (operator CLI - keep)
2026-10-27  128    scripts/lib/pgtest_reaper.py                      tests/conftest.py (keep)
```

### Appendix B — one-off-pattern scripts (name match), last touch

```
2026-04-29  104  scripts/backfill_option_b_historical_bugs.py     docs-only
2026-07-05   27  scripts/apply_orch_lease.py                      referenced by code/docs (lease bootstrap)
2026-07-22   48  scripts/apply_fleet_health_lease.py              CLAUDE.md names it (keep or fold into apply_migration)
2026-08-27  119  scripts/op11911_syed_trigger.py                  ONLY ref = installed plist (iv=300, still loaded)
2026-09-12   58  scripts/apply_fleet_lane_autoscale_log.py        docs-only
2026-09-26  148  scripts/seed_lane_claude_md.py                   CLAUDE.md (keep)
2026-09-28  848  scripts/apply_migration.py                       live tool (keep)
2026-10-01   91  scripts/scope_tg_media_collisions.py             one-off
2026-10-01  146  scripts/backfill_client_asks_ledger.py           one-off
2026-10-01  161  scripts/reclassify_client_asks.py                docs-only
2026-10-01  162  scripts/mark_shadowed_tg_media_rows.py           docs-only
2026-10-02   58  scripts/scratch_toolpath_replay.py               docs-only
2026-10-02   82  scripts/scratch_shape_replay.py                  calls api.telegram.org directly
2026-10-21  135  scripts/opus_reprobe_storefront.py               one-off probe
2026-10-27   55  scripts/backfill_ask_delivered_20261027.py       0 refs
2026-10-28  187  scripts/seed_build_programmes.py                 new; check after use
```
Also temporary-by-docstring with live schedules: `scripts/pooler_authfail_tripwire.py` + `_run.sh` (iv=900), `scripts/watchdog_tripwire_report.py` (3 plists), `~/Library/LaunchAgents/dev.wingmen.remind-musa-20261015.plist`, `dev.wingmen.fix1a-grant-ping.plist`.

### Appendix C — duplicated helpers / direct callers

**`_dsn`/`_connect` definitions (80):** nervous_system: body_activity_oracle, bug_report_channel_poll, cai_bus_notify, commitment_sweeper, console/db (`resolve_dsn`), console/governance, console/hosted_view, finance_bus_notify, fleet_health_bus_notify, front_desk_runner, hub_self_recovery (x2), ingest, lane_wedge_watchdog, media_reaper, nazim_bus_notify, protected_agents, qwen_lane, responder_runner, singleton_liveness (x2), tg_out, vault, weekly_alert_relay. scripts: address_variant_miss_watchdog, asks_daily_digest, asks_open, asks_reconcile_daily, asks_triage, backfill_client_asks_ledger, backlog_done_checker, backup_console_memory (x2), cisco_reconcile_watch, client_channel_nodrop_watch, console_assign, context_health_watchdog, cubeasht_monitor, gated_inbound_digest, gazzabyte_request_nodrop_watch, gzb_hub_heartbeat, hub_bus_currency_monitor, hub_self_recovery_staleness_watchdog, idle_with_work_watchdog, instance_id_dup_detector, irsyad_autoscaler, irsyad_confirm_executor, irsyad_media_mirror, irsyad_shadow_watch (x2), irsyad_spin_worker, irsyad_worker_reaper, lane_drill_seed, lane_operator_reconcile, lib/fleet_health_lease, lib/orch_lease, lib/reset_audit_row, lib/substrate_dsn (x2, the intended SSOT), lib/unreleased_client_drafts, mamadah_reconcile, mark_shadowed_tg_media_rows, monthly_burn_snapshot, onboard_client_channel, op11911_syed_trigger, opus_reprobe_storefront, pooler_breaker_snapshot, preview_devserver_reaper (x2), priority_sla_watchdog (x2), programme_stall_guard, queue_age_watchdog (x2), reclassify_client_asks, reconcile_responded_at (x2), scope_tg_media_collisions, sre_liveness_watchdog, supervised_draft_deadman; ops/audit-chain-weekly/weekly_audit_chain_check.

**Direct `api.telegram.org` callers (21):** nervous_system/ingest.py, nervous_system/tg_out.py, scripts/_tg_chunked_send.py, angullia_send_photo.sh, check_send_paths_report_failure.py, cosem_tdu_support_send_photo.sh, daily_backup.sh, dev_group_edit.sh, health_check.sh, hk_send.sh, irsyad_support_send_file.sh, lane_watch.py, lib/tg_group_send.py, mamadah_send_photo.sh, nazim_say.sh, nazim_send_file.sh, nazim_send_photo.sh, oeh_send_photo.sh, reviewer_send_file.sh, scratch_shape_replay.py, tg_send_file.sh.

**`*_send*.sh` family (24, 1,483L):** angullia_send, angullia_send_photo, caai_send, cai_send, coffeemedia_send, cosem_exams_support_send, cosem_tdu_support_send, cosem_tdu_support_send_photo, dev_group_send, finance_send, hk_send, irsyad_support_send, irsyad_support_send_file, mamadah_send, mamadah_send_photo, nazim_send, nazim_send_file, nazim_send_photo, oeh_send, oeh_send_photo, reviewer_send, reviewer_send_file, tg_send, tg_send_file.

### Appendix D — installed launchd jobs (Mac Mini), grouped

- **KeepAlive daemons (18):** agent-wake-subscriber, arabic-ocr, backlog-done-checker, cai-bus-notify, cc-orch, console-ngrok, coord-pane-publisher, finance-bus-notify, fleet-console, fleet-health-bus-notify, front-desk, hosted-console, nazim-bus-notify, nazim-ingest, nazim-session, wake-backstop-sweep, weekly-alert-relay, (orchestrator/watchdog — targets missing, not loaded).
- **Interval ≤120s (7):** lane-wedge-watchdog 60, live-checkout-branch-watchdog 60, pooler-breaker-snapshot 60, priority-sla-watchdog 90, channel-liveness-watchdog 120, hub-self-recovery-staleness-watchdog 120, gazzabyte-autopublish 120; plus commitment-sweeper 24s and agent-worktree-reaper 6s (likely plist typos for minutes — `iv=6`/`iv=24` seconds).
- **Interval 180–600s (31):** sre-liveness 180; adcda-fn-error-bus-watch, auto-recycle-on-bloat, cc-session-costs-writer, ci-queue-watchdog, cubeasht-monitor, ddl-coverage-watchdog-substrate, ddl-coverage-watchdog-ywrpt, disk-autoremediate, glm-overflow-switch, health-check, lane-proactive-recycle-nudge, lane-recycle-deadman, lane-watch, lane-watchdog, op11911-syed-trigger, queue-age-watchdog, singleton-liveness, sre-lane-recycle, bug-report-channel-poll 300; client-nodrop-watch, context-health-watchdog, disk-space-monitor, file-history-prune, fleet-health, gazzabyte-nodrop-watch, lane-selfrecycle-detect, media-reaper, supervised-draft-deadman 600.
- **Interval ≥900s (16):** address-variant-miss-watchdog, cisco-reconcile-watch, idle-with-work-watchdog, pooler-authfail-tripwire, repo-context-watchdog 900; fleet-stall-watch 1200; deploy-provenance-watch, hub-bus-currency-monitor, preview-devserver-reaper, weekly-limit-monitor, worktree-nodemodules-reaper 1800; irsyad-pii-containment-monitor, node-modules-content-dedup, programme-stall-check 3600; secret-hash-sweep 14400; log-rotate 21600; adcda-port-watch 10800; cc-quality-audit-drain, cc-storefront-audit-drain 43200; tg-out-dead-replay 86400.
- **Calendar (15):** a3-isolation-check, adcda-coverage-host, asks-daily-digest, asks-reconcile, cc-finance-monthly-burn, cubeasht-daily-digest, daily-backup, fix1a-grant-ping, media-cleanup, memory-index-audit, nazim-memory-backup, politemall-notes, programme-stall-digest, reconcile-responded-at, remind-musa-20261015, second-brain-nightly, tripwire-24h/48h/72h, video-retention.
- **Repo `launchd/` but not installed (2):** glm-peak-pause-resume, sentry-new-error-watch (loaded per `launchctl list` though — installed from elsewhere).
- **Installed but untracked in repo `launchd/` (44):** listed in the shell output; 11 of them exist only in `ops/launchd/`.

### Appendix E — method notes / limits

- Reference check is textual: a script named in another script's *comment* counts as "code" reference (over-counts liveness), and a module stem that is also a common word may over-match. The 58-list was spot-checked; false positives are flagged inline as "keep".
- `git log -1 --format=%cs` is last commit touching the path in this worktree's history.
- DB-side checks (applied-migrations table, `fleet_lanes`/`bot_channels` registry vs code constants) skipped — no read-only helper used, per the no-DB-write rule and time budget.
- Not inspected: `nervous_system/console/` (3696L `app.py` + 1490L `db.py`) beyond size; `ops/` directory beyond plists.

---

# 7. Core daemons, bus and leases (auditor G)

## G-core — core daemons + agent bus correctness audit (read-only, 2026-10-27 ~19:25Z)

Scope: `nervous_system/ingest.py`, `tg_out.py`, `operator_log.py`, `tg_send.sh`, `nazim_send.sh`, `bus_send.py`, `my_inbox.py`, `agent_wake*.py`, `wake_backstop_sweep.py`, `front_desk*/front_door/triage`, `hub_self_recovery.py`, `singleton_liveness.py`, `orch_lease.py`, `fleet_health_lease.py`, `legacy/wingmen_orch.py`, `commitment_sweeper.py`, `archive.py`. DB facts via SELECT-only on the substrate.

### Inbound trace (one Telegram message)
`_run_token_cycle` getUpdates (ingest.py:1471-1479, offset = max(poll_offset)+1) → per update `process_update` (:1160): **dedupe** `INSERT ingest_dedup(channel_key, update_id) ON CONFLICT DO NOTHING RETURNING` (:1197-1205; loser returns False) → **durable log** `INSERT operator_messages … RETURNING id` + `UPDATE ingest_dedup.operator_msg_id` (:1290-1305) → `conn.commit()` (:1334) → **gate** deny-by-default (:1337; gated rows get `handled_at=now()` :1345-1349) → asks ledger (:1357-1377) → stage-file (:1385-1398) → **route** busy-aware `nudge_session` count-only send-keys (:1401-1424) → after the whole batch, **offset ack** `UPDATE bot_channels SET poll_offset` (:1491-1500). Offset is persisted AFTER the durable insert — correct. psycopg.Error anywhere → offset held, Telegram redelivers, dedupe absorbs (:1526-1532). `handled_at` is only stamped by the gate (:1345) and by `operator_log.mark_handled([ids])` scoped by body (operator_log.py:929-949) — no stamp-without-answer path found beyond the intended gated case.

### Lease rows (live, DB now = 19:21:31Z)
- `orch_lease` orch-hub: holder `cc-orchestrator@gzbai`, renewed 19:17:08Z (4m23s old), ttl 900 — fresh. taken_over_from `cc-orchestrator@wingmen-core` (planned cutover).
- `fleet_health_lease` fleet-health: holder `cc-fleet-health@Sheikhs-Mini`, renewed 19:18:05Z (3m26s), ttl 900 — fresh.

---

#### [C-1] Ingest post-commit steps are not replay-safe: a crash between commit and offset-ack silently drops the asks-ledger row and the nudge
Impact: high  Effort: M
Evidence: nervous_system/ingest.py:1334 `conn.commit()` then :1357-1377 `maybe_track_ask` / :1380-1383 `maybe_track_client_ask` / :1401-1409 `nudge_session(target, ch.key, unread_count(conn, ch))`; `unread_count` (:1427-1434) is an unwrapped DB call; on redelivery :1203-1205 `if cur.fetchone() is None: … return False` skips every post-commit step.
Failure scenario: DB blip or daemon restart after the log commit but before the batch's `UPDATE bot_channels SET poll_offset` (:1496) → Telegram redelivers → dedupe says "already processed" → no `operator_asks` row is ever opened for that message and no nudge fires; only Option B / `drain_stale_deferred` (600s) covers the nudge, nothing covers the ask row.
Fix sketch: record a stage on `ingest_dedup` (e.g. `routed_at`) and let a replay resume from the unfinished stage; or move 3b/3c inside the logging transaction.

#### [C-2] `tg_send.sh` fails OPEN when the lease gate cannot be evaluated, and a DB outage silently loses the durable outbound log (exit 0)
Impact: high  Effort: S
Evidence: scripts/lib/orch_lease.py:265-268 `except Exception … return True, "lease-unreadable-failsafe"` (any non-console body, incl. a lane with `ORCH_BODY_ROLE` unset); scripts/tg_send.sh:21-26 refuses only on non-zero exit; tg_send.sh:103 `OPLOGID="$(… operator_log outbound … 2>/dev/null)" || true`; same shape nazim_send.sh:107-116.
Failure scenario: substrate down → a lane on the Mini can speak on the hub's channel; and every reply sent during the outage has no `operator_messages` row, so the "log is the source of truth" invariant (Option B) has a hole nobody is paged about.
Fix sketch: for `lane` role fail closed when the lease is unreadable; on operator_log failure, spool the row to a local file and exit non-zero (or page) instead of `|| true`.

#### [C-3] `tg_out` retry backoff is keyed on `created_at`, not last attempt — 5 attempts burn in ~10s; no 429/`retry_after`; concurrent drainers can double-send
Impact: high  Effort: S
Evidence: nervous_system/tg_out.py:316-318 `UPDATE tg_out SET status='queued' WHERE status='failed' AND created_at < now() - 30s`; :65-66 `DRAIN_INTERVAL = 2`, `MAX_ATTEMPTS = 5`; :221-234 `_send_text` raw `urlopen` (HTTPError on 429, no retry_after); :301-306 → `dead`; :321-323 `sending` re-queue also keyed on `created_at`; :533-536 CLI `send` runs `drain_once` concurrently with the daemon. Dead replay runs once per 24h (`dev.wingmen.tg-out-dead-replay` StartInterval 86400). Contrast scripts/_tg_chunked_send.py:127-194 which honours `retry_after`.
Failure scenario: a 60s Telegram 429/5xx window → any row older than 30s is retried every 2s, dies after ~10s, waits up to 24h for one replay; a file send >120s on a row older than 120s is re-claimed by a second drainer → operator receives it twice.
Fix sketch: add `last_attempt_at`, back off on it (exponential), honour 429 `retry_after` via the chunked-send helper, key the `sending` crash-recovery on `last_attempt_at`.

#### [C-4] Archive never runs; `agent_messages`/`operator_messages`/`ingest_dedup` grow unbounded; no partial index for the inbox query
Impact: high  Effort: M
Evidence: nervous_system/archive.py:4 "called from wingmen_orch.py" → only caller is legacy/wingmen_orch.py:2144; legacy/README.md:8-10 "no launchd job on either host"; `launchctl list` has no `dev.wingmen.orchestrator` (plist RunAtLoad=false, "Kept UNLOADED"); archive.py covers only jobs/decisions anyway. Live: `agent_messages` 73,457 rows / 136 MB, +5,968 in 7d; `operator_messages` 34,743 / 27 MB; `ingest_dedup` 13,289 (no prune path in repo). `pg_indexes`: no `(to_agent) WHERE read_at IS NULL` index; scripts/lib/my_inbox.py:62-66 relies on `idx_agent_messages_claim` or `idx_agent_messages_open_by_priority`. CLAUDE.md:20 still names `wingmen_orch.py` as the main entry.
Failure scenario: ~300k rows/yr; inbox and sweep queries degrade; `fleet_health.py:415` marks dead-letters read but never moves them.
Fix sketch: launchd/systemd job archiving `agent_messages` with `read_at < now()-30d` to an archive table, prune `ingest_dedup` > 7d, add `CREATE INDEX … ON agent_messages (to_agent, created_at) WHERE read_at IS NULL`; fix CLAUDE.md:20.

#### [C-5] `orch_lease` is renewed by a host timer independent of the hub session: fresh lease ≠ live hub, which blocks DR `take` and misroutes commitments
Impact: med  Effort: S
Evidence: nervous_system/singleton_liveness.py:312-316 "lease renewed every ~3 min while the hub [session was dead]"; scripts/lib/orch_lease.py:455-462 renew is host-match only; :520-523 `take` refuses while fresh unless `--force`; nervous_system/commitment_sweeper.py:117-122 `_live_agents` adds `cc-orchestrator` on lease freshness alone.
Failure scenario: hub claude session dead on gzbai, systemd timer keeps renewing → pens stay with a dead body, `take` refuses, due commitments fire into the dead hub instead of re-routing to console.
Fix sketch: renew only when `tmux has-session -t =orch` AND recent pane/heartbeat activity on gzbai; otherwise let it expire so the reclaim path works.

#### [C-6] `read_at` is writable with arbitrary values and is stamped by a deliverer in one path; 285 rows have `read_at < created_at`
Impact: med  Effort: S
Evidence: nervous_system/nazim_bus_notify.py:200-209 `_autoack_suppressed` → `UPDATE agent_messages SET read_at=now() WHERE to_agent='orch-console' AND read_at IS NULL AND (<pattern>)` — a notifier marking the reader's column before the agent sees the row (pattern-scoped, gated on cc-scholar health :170-190). scripts/fleet_health.py:415 dead-letter archive stamps read (by design). No DB constraint: 285 rows `read_at < created_at` (137 at ~13h earlier, 43 at ~1 year earlier; shared `.300000` timestamps → hand-typed stamps by lanes, Aug + Oct). my_inbox.py never stamps (:62-66) — lanes stamp by ad-hoc SQL; bus_send.py:788 stamps the parent on `--reply-to` (reader-side, fine); qwen_lane.py:276 reader-side.
Failure scenario: wake_backstop_sweep.py:170-178 and SLA logic treat `read_at IS NOT NULL` as "agent processed it"; a bogus or deliverer-side stamp silently closes a doorbell on an unprocessed row.
Fix sketch: `CHECK (read_at >= created_at)` or a BEFORE UPDATE trigger clamping to `now()`; expose one `mark_read(ids)` helper in my_inbox.py and make it the only sanctioned stamp.

#### [C-7] Token-group fallback gates AND auto-handles mail for a chat whose channel shares the token but is neither enabled nor pinned
Impact: med  Effort: S
Evidence: nervous_system/ingest.py:1484-1490 `process_update(conn, matched or group[0], upd)`; :1337-1352 gated row gets `handled_at=now()` → never appears in `unprocessed()`. Registry (read-only): `brandit-coffeemedia` shares `WINGMENDEVBOT_TOKEN` with angullia/coffeemedia/coffeemedia-hamhuskies-test, is `enabled=false` and is NOT in scripts/boot_nazim_ingest.sh `INGEST_CHANNELS`; `ingest_poll_health` shows it polled on Sheikhs-Mini until 11:44Z today, then dropped.
Failure scenario: a client writes to that chat → logged under the group's first key (`angullia`) with `handled_at` stamped → invisible to every reconciler; no page.
Fix sketch: when an unmatched chat_id matches ANY `bot_channels` row for that token (even disabled/unpinned), do not stamp `handled_at`; page once per (channel, host) like the pinned-drift check (:521-549).

#### [C-8] Lease `check()` compares DB-written `renewed_at` against the LOCAL clock; CAS uses DB `now()` — plus a brief two-holder window on `holder-stale-self`
Impact: med  Effort: S
Evidence: scripts/lib/orch_lease.py:148-149 `_is_expired(now > renewed_at + ttl)`, :273 `datetime.now(timezone.utc)`; take/renew use SQL `now()` (:457, :522). scripts/lib/fleet_health_lease.py:95-96, :190 same; :116-119 `holder-stale-self → True` lets an expired holder keep acting while the hub's `reclaim` CAS (:246-253) succeeds on the same expiry; `check` is point-in-time and the action is not re-gated.
Failure scenario: host clock skew of a few minutes makes the gate and the CAS disagree about expiry; during an SRE stall both SRE (stale-self) and hub (reclaim) run the watchdog for one cycle.
Fix sketch: evaluate expiry in SQL (`renewed_at + make_interval(secs=>ttl_seconds) < now()`) in `_fetch_lease`; on `holder-stale-self` require a successful `renew` before acting.

#### [C-9] `singleton_liveness` uses prefix-matching `tmux has-session -t <name>` → false ALIVE
Impact: med  Effort: S
Evidence: nervous_system/singleton_liveness.py:173 `[_tmux_bin(), "has-session", "-t", session]` (no `=`); ingest.py:727 and agent_wake.py:294 use `-t =name`.
Failure scenario: `cai` dead but a `cai-old`/`cai2` session exists → `classify_dead` returns alive → no page (the exact 30h-undetected class this monitor was built for).
Fix sketch: `-t f"={session}"`.

#### [C-10] `commitment_sweeper` has no dead-man for itself, bypasses `bus_send.py`, and re-escalates a fired-undischarged row every 30 min forever
Impact: low  Effort: M
Evidence: nervous_system/commitment_sweeper.py:294-390 `_sweep_due` has no try/except; `sweep()` returns 2/3 (:277-291) — launchd only logs the exit code; :128-140 hand-written `INSERT INTO agent_messages` (priority explicit, so the P2-default trap is avoided, but no identity/undeliverable guard); :355-380 + `ESCALATE_AFTER` (:67) re-pages orch-console P1+rr with no ceiling. Mutations: `held_commitments.status/fired_at` (CAS `AND status='pending'` :348-350) and hydrate inserts/updates/cancels (:246-265); whole pass commits once (:383-386) → idempotent and atomic. Live proxy healthy: 0 pending overdue >15m; last fire 18:09Z today.
Failure scenario: DSN/import error → sweeper exits non-zero every 5 min, nothing fires, nobody paged (the residency_sweep failure mode its own docstring cites).
Fix sketch: fleet_health rung `held_commitments WHERE status='pending' AND due_at < now()-15m` → page; cap escalations at N; route `_notify` through `bus_send.send()`.

#### [C-11] Doorbell fan-out: four independent nudge paths, only one of them capped
Impact: low  Effort: S
Evidence: realtime (nervous_system/agent_messages_realtime.py:171-192 → `agent_wake.wake_agent`), backstop sweep (wake_backstop_sweep.py:63-64, 60s/90s grace), `*-bus-notify` daemons (nazim/cai/fleet-health/finance — `launchctl list`; nazim_bus_notify.py does its own send-keys), ingest nudge (ingest.py:735-769), priority_sla_watchdog. Debounce/5-per-5min/per-row ceiling live only in agent_wake.py:503-536 + :660-700 via `lane_nudge.sh`. Hub floor correct and shared: agent_wake.py:90-93 (P0/P1 AND rr), bus_send.py:268-277 warns, commitment_sweeper `_notify` defaults P1+rr (:128-129). Registry/session mismatch: agent_wake.py:336-380 resolves from `agent_status.tmux_session` with exact has-session; mismatch → "no live session" (:694-695) → sweep quiesces after cap (`skipped_at`) → surfaces only via the 30-min stuck page.
Failure scenario: bus-notify nudge + wake land seconds apart on a console whose footer isn't "busy"; conversely a renamed lane session gets nothing until the stuck page.
Fix sketch: route the bus-notify daemons' send-keys through `lane_nudge.sh` with `LANE_NUDGE_ROW_ID`; page on first "no live session" for a `desired_state='up'` lane.

#### [C-12] `tg_out` chunks at exactly 4096 code points; Telegram counts UTF-16 units
Impact: low  Effort: S
Evidence: nervous_system/tg_out.py:63 `CHUNK = 4096`, :79-93 `chunk_text`; sibling scripts/_tg_chunked_send.py:32 `LIMIT = 4000`.
Failure scenario: a 4096-char chunk containing astral emoji → "message is too long" → row fails → dies (C-3) → text never delivered.
Fix sketch: `CHUNK = 4000`.

#### [C-13] `edited_message` is ingested as a brand-new inbound row (second ack, second unhandled row)
Impact: low  Effort: S
Evidence: nervous_system/ingest.py:1180 `upd.get("message") or upd.get("edited_message")`; :1475 `allowed_updates` includes `edited_message`; no `edit_of`/`tg_message_id` stored for inbound (:1290-1298).
Failure scenario: operator fixes a typo → two unhandled rows, two "Got your message" acks, two asks-ledger candidates.
Fix sketch: store inbound `message_id`; on edit, update the original row's text (or mark `edit_of`) and skip the ack.

### Checked and OK (no finding)
- Dedupe key `(channel_key, telegram_update_id)` PK; callback buttons claim the update_id and commit BEFORE running the script (ingest.py:1118-1130) — redelivery can't double-run a reset.
- `operator_log.unprocessed()`/`mark_handled()` are body-scoped, id-exact, at-least-once (operator_log.py:884-949); hub stamp excludes `cai-channel` (:917-926).
- `bus_send.py` identity resolution is fail-closed and `--priority` is required (bus_send.py:42-47, :600); hub-floor warning :268-277.
- `hub_self_recovery.py` is observe-only, gated on `orch_lease.holder_host == self` (:104-110, :314-317), kill-switch fails closed (:113-125), acts only on PENDING work (:127-142); settings row live: enabled, tick 19:21:09Z, "no pending work". No restart/kill path on a wrong host found. `singleton_liveness` is page-only, hub excluded, lease-fresh prevents a false-DEAD page (:105-126).
- The gzbai HTTP 409 rows in `ingest_poll_health` (`oeh`, `coffeemedia*`) are stale (last updated 10-17..10-19) — historical dual-poller, not live.
- `tg_out` queue: 2,589 rows all `sent`, 0 queued/failed/dead right now.
- `operator_messages` has partial indexes on `(direction='inbound' AND handled_at IS NULL)`; `operator_asks` and `tg_out` hot paths indexed.

---

## NOVEL IDEAS (Musa op#34981-82, from the vantage point of a lane that just read the whole control plane)

### N1. Policy-as-code: compile CLAUDE.md into a rule registry with enforcer coverage CI
**Idea.** Every "never / always / only via" sentence in CLAUDE.md, `docs/governance-floors.md` and the lane template becomes a row in `fleet-policy.yaml` (`id`, `text`, `enforcer: scripts/hooks/x.py | git-hook | lease | trigger | PROSE-ONLY`, `test`). A pytest asserts every rule has an enforcer and every enforcer has a test; a pre-commit hook refuses a new "never" sentence in CLAUDE.md without a policy row. The prose stays for humans; the yaml is the SSOT the fleet is actually held to.
**Why feasible for us.** The enforcers already exist as separate scripts (hooks, leases, git-hooks, boundaries); today's audit is the seed inventory (section 2 of this report lists each rule with ENFORCED / PARTIAL / PROSE-ONLY). It is a day of cataloguing plus one test.
**First step.** `fleet-policy.yaml` with the ~25 rules from section 2, `tests/test_policy_coverage.py` that fails on any PROSE-ONLY row without an `accepted_gap_until` date.
**Unlocks.** Ends the relapse class permanently (a rule that can't be enforced becomes a visible, expiring debt item instead of a hope); new lanes/bodies inherit the full rule set at boot; the registry is the spine of the substrate-as-product story — "bring your own CLAUDE.md, we enforce it".

### N2. Fleet replay testbed: re-run the last N days of real traffic against a candidate branch
**Idea.** The substrate already durably logs everything that drives the fleet: `operator_messages`, `agent_messages`, `fleet_lanes` transitions, lease rows, build_log. Build a replay harness that loads a time window into a scratch Postgres (Docker, synthetic-scrubbed by the existing `secret_redact`/`synthetic_filter`), then runs `ingest.py`, the watchdogs and the lease gates from a candidate branch in dry-run against it and diffs the decisions (who got paged, what got routed, which gate refused) against what production did.
**Why feasible.** All inputs are already rows; dry-run flags already exist on most daemons; the nervous_system modules are mostly pure functions over DB state. The hard part (data) is solved; this is a harness and a differ.
**First step.** `scripts/replay.py --from 24h --module nervous_system.ingest --branch <sha>` producing a decision diff for one module; run it against PR #545 retroactively.
**Unlocks.** Makes the top-10 simplifications in section 7 (collapsing 80 modules and 70 launchd jobs) actually safe to do; turns "verify before assert" into a tool rather than a memory rule; a regression on the control plane is caught before merge instead of by the operator's phone at 03:00.

### N3. `fleetctl` with a transactional change ledger and auto-expiring disarms
**Idea.** One CLI for every runtime mutation that today is done by hand: enable/disable/pause a launchd job, change a threshold, switch a lane token, take/release a lease. Every call writes a `fleet_changes` row (who, why, op#, before/after, `expires_at`). A disarm/pause MUST carry an expiry; the fleet-health tick re-arms on expiry and pages if the reason is still live. `launchd/` in the repo becomes generated output, and installed plists that differ from the repo are a finding.
**Why feasible.** The audit found ~40 `.plist.bak-*` / `.disabled-*` files in `~/Library/LaunchAgents` — each is a hand edit with the reason encoded in the filename and no re-arm date. The repo already tracks plists and has `fleet_health.py` as the periodic tick to hang re-arming on. Two-day build.
**First step.** `fleetctl job pause <label> --reason --until`, writing the ledger row and the plist edit atomically; migrate the existing disarm files into ledger rows with explicit expiry.
**Unlocks.** No watchdog silently stays disarmed for weeks; a complete, queryable history of "why is prod configured like this"; the same ledger is the audit artifact clients (and CAI-1034-style floors) want to see.

### N4. Scheduled chaos drills: measure that the safety net actually catches
**Idea.** A weekly, operator-visible drill window where an injector deliberately produces one fault at a time in a sandboxed way (expire a copy of the `fleet_health_lease` row in a drill schema, park a synthetic P1+requires_response bus row addressed to a drill agent, wedge a dedicated drill tmux lane, block one launchd job) and asserts that the detector pages within its SLA and the reclaim/recycle path fires. Results land as `drill_results` rows and a line in the morning brief: "6/6 safety nets fired, median detection 41s".
**Why feasible.** The fleet has at least a dozen watchdogs, leases and dead-man paths whose only evidence of working is the incident that created them. The report's "sleeping failure modes" section shows several that would currently pass silently. Injection needs no new infrastructure: scratch rows, a drill lane, and `launchctl` in a drill namespace.
**First step.** One drill: `scripts/drill_lease_reclaim.py` that expires a drill-scoped lease row and asserts `fleet_health_lease.py reclaim` runs within TTL; wire to the morning brief.
**Unlocks.** MTTD becomes a number, not a belief; every "enforced in code" claim gets a weekly proof; a dashboard line that makes the fleet's reliability legible to the operator and sellable to clients.

### N5. Turn the substrate outward: a per-client fleet surface on the TG2.0 bot model
**Idea.** Clients (Gazzabyte/Shuq and Wan, cosem) already interact through Telegram and already have per-channel isolation in `bot_channels`. Give each client their own asks ledger, daily digest, and status board driven by the exact same `operator_asks`/`asks_daily_digest.py`/console code, scoped by channel. The client sees "what I asked, what shipped, what is waiting on me" without any human relaying it. Package that as the paid "fleet-as-a-service" tier on top of the product work the lanes already do.
**Why feasible.** The asks ledger, the triage states, the digest and the per-channel isolation all exist for Musa today; this is a tenancy axis on existing tables (TENANT-RESIDENCY-001 already defines where client rows live) and one bot per client. The console revamp (PR #545) just made the board presentable.
**First step.** Scope `operator_asks` by `channel`/tenant, mint one client bot for Gazzabyte, send them the digest for a week read-only, collect feedback.
**Unlocks.** Recurring revenue not tied to build hours; shorter feedback loops with clients (they triage their own asks); a reference customer for the substrate-as-product roadmap in `docs/substrate-as-product-roadmap.md`.

---

## Method and limits
- Seven read-only subagents (Claude Fable 5.1) ran in parallel for ~15–22 min each, each with a fixed dimension and a hard no-write rule; the lane de-duplicated and re-ranked. Line numbers were spot-checked by each auditor against `7f10ed1`; the lane did not independently re-verify every line.
- Substrate access was SELECT-only (lease rows, index list, row counts, launchd health). No client silo, the ihsanos multi-tenant DB or ADCDA prod was touched. No raw client data or operator message bodies appear in this report (CAI-1034).
- Reference checks for "dead" scripts are textual (grep on basename/module stem over tracked files + installed plists) and over-count liveness; the 58-list was spot-checked, operator CLIs marked "keep".
- Known limits: the per-module referenced-by table for all 278 modules did not finish inside the time box (auditor D, Appendix D is partial — auditor F's refcheck covers it); `pytest --collect-only` could not run because conftest fail-closes on the prod `DATABASE_URL` (itself correct); the hooks auditor's section (§1) is included only if it returned before the deadline.
- Reproduction scripts: the dead-script reference checker lives in the lane's scratchpad (`refcheck2.py`, `refs.json`) and is not committed; re-running the audit should port it to `tests/test_no_orphan_scripts.py` (X-6).
