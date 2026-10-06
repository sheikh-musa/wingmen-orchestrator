# Fable substrate audit 2026-10-06 — Fork D: code accretion, CI health, launchd parity, secrets hygiene

Auditor: orch-console fork D (Fable 5.1). Repo `/Users/sheikhmusa/wingmen/orchestrator` @ `cf853b5` on `fable/substrate-safe-fixes`. READ-ONLY: no DB writes, no bus posts, no git actions, no launchctl/tmux. Secret-shaped strings were only counted (`grep -l`/`-c`), never printed. Raw data: `_orphans.json`, `_orphans_lenient.json`, `_ci_runs.tsv`, `_launchd_loaded.txt` in this directory.

Labels: **verified** = measured directly on this host/repo; **inferred** = derived from a measurement with a stated caveat.

---

## 1. Orphans (code nothing references)

Method: every file in `scripts/`, `scripts/lib`, `scripts/hooks`, `nervous_system/*.py` (380 files, 77,963 lines) cross-referenced against all tracked text files, `launchd/*.plist`, the 87 installed `~/Library/LaunchAgents/*wingmen*.plist`, `.claude/settings.json`, the user crontab, and the fleet-health / cai boot scripts. Two passes:

| Pass | Match rule | Orphans | Lines | % files | % lines |
|---|---|---|---|---|---|
| strict | exact basename appears somewhere | 28 | 3,463 | 7.4% | 4.4% |
| lenient | module stem as a whole word (catches `import x`, `*_run.sh` wrappers) | 22 | 2,133 | 5.8% | 2.7% |

**verified**: the 09-30 audit's "~28% / 7.8k lines" is no longer true by this measure — either accretion was cut in the Phase-0 sweep or the earlier method counted differently; the lenient figure is the defensible one. Six files the strict pass flagged are live (rescued by tests + `_run.sh` wrappers or launchd): `checkpoint_recycle_driver.py`, `agent_worktree_reaper.py`, `idle_with_work_watchdog.py`, `address_variant_miss_watchdog.py`, `register_quality.py`, `break_glass.sh`.

Lenient orphans, grouped (lines, last commit):

| Group | Files | Lines | Verdict |
|---|---|---|---|
| Dead experiments | `ab_qwen_opus.py` (388, 08-20), `nervous_system/qwen_lane.py` (322, 08-06) | 710 | delete |
| One-shot ops scripts left behind | `lib/irsyad_purge_executed_20260703.py` (137, 07-03), `op23364_musa_trigger.py` (137, **untracked**), `model_move_firer_20261002.sh` (61, **untracked**), `mark_shadowed_tg_media_rows.py` (162, 10-01) | 497 | delete (archive the two untracked ones first — they ran against live) |
| Superseded infra | `vps_provision.sh` (69), `setup_tailscale_system_daemon.sh` (42), `deploy_ihsanos_prod.sh` (39, 07-03, pre-Vercel path), `breakglass_studio_orch.sh` (70), `quality_gate_check.sh` (78), `token_usage.py` (100, 06-18), `cc_cost_snapshot.py` (79) | 477 | delete or fold into docs |
| Agent-invoked CLIs with no static caller (referenced only from agent memory / other repos' CLAUDE.md) | `coffeemedia_send.sh`, `oeh_send_photo.sh`, `mamadah_send_photo.sh`, `hk_send.sh`, `dev_group_edit.sh`, `nazim_model.sh`, `render_pdf_remote.sh`, `apply_fleet_lane_autoscale_log.py`, `lib/vault_get_proof_cli.py` | 449 | keep, but register them (see move 2) — "orphan to the repo" means a lane can silently break them |

Deletable now: **~1,700 lines / 13 files** (first three groups). The 09-30 figure of 7.8k is not reproducible today.

**Hygiene finding (verified):** `.claude/worktrees/agent-a530703a010986051/` is a full stale copy of the repo inside the repo (its own `tests/`, `launchd/`), left by a subagent worktree; it pollutes every grep and was the source of several false "references" above.

## 2. Duplicate mechanisms

| Family | Members (verified) | Canonical | Concrete divergence (verified) |
|---|---|---|---|
| Telegram senders | 27 `scripts/*send*` (per-client `<x>_send.sh`, `_photo.sh`, `_file.sh`, plus `tg_send.sh`, `nazim_send.sh`, `reviewer_send.sh`, `bus_send.py`) | `tg_group_send.py` / `_tg_chunked_send.py` (22 of 24 shell senders route through it; **10 still curl `api.telegram.org` directly**) | Only **4 of 24** log the outbound to `operator_messages`/a durable log; only 7 redact; `--ask` (ask-ledger) exists in 4. A reply sent via `irsyad_support_send.sh` or `reviewer_send.sh` is invisible to the "every ask on the ledger" rule and to the redaction pass. |
| Watchdogs / sweeps / tripwires | 32 scripts matching `watchdog|tripwire|monitor|deadman|sweep|liveness`; 77 loaded `wingmen` launchd jobs | none — each has its own loop, DSN, paging path | Three independent recycle/wedge detectors (`lane_wedge_watchdog`, `lane_recycle_deadman`, `lane_selfrecycle_detect`, `lane-proactive-recycle-nudge`) page separately; `priority-sla-watchdog`'s installed plist differs from the tracked one (§4). |
| DSN access | 135 `.py` files read `SUPABASE_DB_URL`/`ORCH_DB_DSN`/`DATABASE_URL`; **58 define a local `_dsn()`/`_conn()`-style helper; 115 call `psycopg.connect(` directly; only 10 import `scripts/lib/substrate_dsn.py`** | `substrate_dsn.py` exists (09-30 fix #5) but adoption is 10/135 | The 09-30 P0 ("stale env DSN preferred over `.env`, no 28P01 handling") is fixed in the accessor and unfixed in 125 callers. |
| Nudge / wake paths | 9: `boot_*_bus_notify.sh` ×4 (one per singleton), `boot_agent_wake_subscriber.sh`, `boot_wake_backstop_sweep.sh`, `lane_nudge.sh`, `lane_proactive_recycle_nudge.py`, `nudge_cai.sh` | `agent-wake-subscriber` + `wake-backstop-sweep` | `dev.wingmen.agent-wake-subscriber` is running with last exit **1**; per-singleton `*-bus-notify` daemons duplicate its job for 4 bodies. |
| Singleton / protected-body lists | 4 `.py` definitions (`lib/lane_token_resolver.py`, `lib/lane_winddown.py`, `nervous_system/lane_wedge_watchdog.py`, `nervous_system/console/panes.py`) + 15 readers of the `protected_agents` table; `body_registry.json` **absent** (09-30 said it existed dormant) | `protected_agents` (DB) | Four hard-coded lists can disagree with the table; the 09-30 "one registry" move has not landed. |

## 3. CI / test health (verified via `gh` on `sheikh-musa/wingmen-orchestrator`)

| Metric | Value |
|---|---|
| Workflow | one: `ci.yml`, single job "Lint & Test" (syntax check → migration additive linter → ephemeral Postgres 17 schema bootstrap → pytest) |
| Latest green run 37467187016 (2026-10-06) | **4,319 passed, 405 skipped, 61 deselected, 1 xfailed**, 27 warnings, 7:05 test wall-time |
| Last 30 runs | 27 success / 3 failure; median run 9.6 min (min 9.1, max 20.0); newest-10 avg 9.6 min vs oldest-10 avg 11.9 min → improving |
| `tests/ci_known_failing.txt` | 61 deselected node-ids, **flat at 61 since introduced 2026-10-06** (5 commits touch it, all today); header states shrink-only contract; 37 comment lines of justification |
| In-tree markers | 44 `pytest.mark.skip`, 1 `xfail`, 2 files with flaky/rerun markers; 275 test files, 3,296 test functions |
| Branch protection | `main`: **not protected** (API 404 "Branch not protected"). `fable/substrate-safe-fixes` (the deployed trunk): required check `Lint & Test` only; no required reviews. |
| Required vs enforced | the one required check is enforced on the trunk; `main` has none — a merge to `main` can bypass CI entirely |

**inferred:** 405 skips + 61 deselects = 466 tests (~9.7%) that do not run in CI; the deselect list is honest and dated, the 405 skips are not inventoried anywhere.

## 4. Launchd parity (verified on the Mini)

| Check | Count | Items |
|---|---|---|
| Loaded `wingmen` jobs | 77 | — |
| Installed plists in `~/Library/LaunchAgents` | 87 | — |
| Tracked plists in `launchd/` | 45 (+2 untracked files in the dir: `pooler-breaker-snapshot`, `op23364-musa-trigger`) | — |
| **Loaded but not tracked in repo** | **47** | incl. `nazim-session`, `nazim-ingest`, `fleet-health`, `fleet-console`, `hosted-console`, `log-rotate`, `daily-backup`, `irsyad-pii-containment-monitor`, `queue-age-watchdog`, `lane-wedge-watchdog`, `sre-lane-recycle`, `tripwire-24h/48h`, three `com.wingmen.shipforge.*`/`sensitive-access-monitor` |
| Tracked but not loaded | 15 | `agent-worktree-reaper`, `asks-daily-digest`, `cc-quality-audit-drain`, `cc-storefront-audit-drain`, `client-nodrop-watch`, `console-ngrok`, `context-health-watchdog`, `disk-autoremediate`, `hosted-console`, `lane-wedge-watchdog`, `queue-age-watchdog`, `secret-hash-sweep`, `singleton-liveness`, `sre-lane-recycle`, `op23364-musa-trigger` (several of these ARE loaded under an installed plist that differs in name/label from the tracked one — i.e. the repo copy is not what runs) |
| Installed but not loaded (dead plists on disk) | 23 | incl. legacy `dev.wingmen.orchestrator` (points at missing `wingmen_orch.py`), `dev.wingmen.watchdog` (missing `watchdog.py`), `cc-orch`, `cc-orchestrator.scheduled`, `lane-watch`, `lane-watchdog`, `fleet-stall-watch`, `tripwire-72h`, `wingmendev-bot`, `irsyad-ingest`, `fix1a-grant-ping` |
| Tracked AND installed but **content differs** | 3 | `auto-recycle-on-bloat`, `lane-proactive-recycle-nudge`, `priority-sla-watchdog` |
| Non-zero last exit (excluding SIGTERM −15) | 6 | `cc-quality-audit-drain` (1), `irsyad-pii-containment-monitor` (1), `agent-wake-subscriber` (1, running), `com.wingmen.shipforge.worker` (1, running), `politemall-notes` (3), `a3-isolation-check` (1) |
| Loaded job whose program is untracked | 1 | `pooler-breaker-snapshot` → `scripts/pooler_breaker_snapshot.py` (untracked; this is the 09-30 fix #2 dead-man, running from an uncommitted file) |
| Logs | launchd stdout/err logs total **178 MB**; `logs/` total **372 MB**; largest: shipforge `tunnel.err.log` 32.7 MB (no rotation), `wake-backstop-sweep.err` 5.2 MB; `log-rotate` job is loaded but does not cover the shipforge path |

The 09-30 finding "42–48 loaded plists untracked" is **unchanged at 47** — the launchd-parity move (#7) has not landed; `pooler_breaker_snapshot.py` still runs from an untracked file.

## 5. Secrets hygiene (counts only)

| Check | Result |
|---|---|
| `.env` backups in repo root | **11** files (`.env.bak-*` ×10 from 109d→0d old, `.env.pre-rotation-20261001T150319Z` 5d), all mode 0600, all untracked. The 09-30 audit asked to purge 8; now there are 11 (two added this week by the model flips). |
| Tracked files containing secret-shaped strings | 22 by regex — on inspection the hits are the redaction/shape-pattern code and its tests (`secret_redact.py`, `hooks/secret_shape_patterns.py`, `hooks/secrets_transcript_guard.py`, `tests/test_*`), plus `nervous_system/console/Dockerfile` and `scripts/scratch_shape_replay.py` — **those last two should be checked by hand** (not opened here). |
| Transcripts under `~/.claude/projects/*/` with DSN-shaped strings | **70 of 314** jsonl transcripts |
| Transcripts with Telegram bot-token-shaped strings | 5 |
| `logs/` files containing `api.telegram.org/bot<token>` URLs | **1** (`logs/cc_cai_daemon.err`, mode **0644**, 127 KB) — down from 609 URLs across files on 09-30 |
| Defences in place | PreToolUse hooks in `.claude/settings.json`: `console_irsyad_guard.py` (console only). Lane launcher enforces `secrets_transcript_guard.py` (PR #313). Also present: `secrets_output_scanner.py`, `client_media_read_guard.py`, `secret_shape_patterns.py`, `secret_redact.py`; git hooks `pre-commit`/`pre-push`/`post-checkout` via `core.hooksPath`; `secret-hash-sweep` job (tracked, **not loaded**). |
| Gaps | (a) the transcript guard protects new writes; the 70 historical transcripts still hold live-shaped DSNs (two rotations since 09-28 — unknown which are still valid); (b) `secret-hash-sweep` is tracked but not loaded; (c) 10 senders still curl Telegram directly, outside the redaction path; (d) `.env.bak*` accumulation continues with every `env_set`. |

---

## Ranked moves (subtract / unify first)

| # | Move | Evidence | Change | Owner | Effort | Risk |
|---|---|---|---|---|---|---|
| 1 | **Launchd = repo, one way** | 47 loaded-but-untracked, 15 tracked-not-loaded, 3 differ, 23 dead plists, 1 running untracked script | `launchd/` becomes the only source: commit the 47 (or retire), `launchd_apply.sh` installs+diffs, a CI parity test fails on drift; delete the 23 dead installed plists; commit `pooler_breaker_snapshot.py` | cc-fleet-health (SRE) | 1 day | low; do it job-by-job with `launchctl print` before/after |
| 2 | **One sender core, every send logged + redacted** | 27 senders; 10 bypass the core; 4/24 log; 7 redact | fold all `*_send*.sh` into thin wrappers over `tg_group_send.py` that always logs to `operator_messages`/`client_messages`, always redacts, always supports `--ask`; delete the direct-curl paths | hub (nervous_system owner) | 1 day | medium — client channels; ship behind a parity test over every `*send*` |
| 3 | **Finish the DSN accessor migration** | 10/135 readers use `substrate_dsn.py`; 58 local `_dsn()`; 115 direct `psycopg.connect` | codemod the 58 helpers to `connect_substrate()`; CI lint that fails any new `psycopg.connect(` outside `scripts/lib/` | cc-substrate | 1–2 days | low (mechanical), high payoff on next rotation |
| 4 | **Protect `main`; inventory the 405 skips** | `main` unprotected; 405 skipped tests uninventoried | require `Lint & Test` on `main`; generate `tests/ci_skipped_inventory.txt` from `pytest -rs` and gate growth like `ci_known_failing.txt` | cc-substrate | 2 h | none |
| 5 | **Delete the dead 13** | 1,700 lines across experiments / one-shots / superseded infra | one PR deleting them; archive the two untracked one-shots under `reports/` first | cc-substrate | 2 h | none |
| 6 | **Register agent-invoked CLIs** | 9 scripts referenced only from agent memory | a `scripts/CLI-REGISTRY.md` (name, owner, caller) + a test that every `scripts/*.sh|py` is either imported, launchd-referenced, or registered — turns "orphan" into a CI signal | cc-substrate | 2 h | none |
| 7 | **One recycle/wedge detector; retire per-singleton bus-notify daemons** | 4 overlapping recycle detectors; 4 `*-bus-notify` daemons duplicating `agent-wake-subscriber` (which itself exits 1) | fix `agent-wake-subscriber`'s exit-1 first (it is the canonical doorbell), then retire the 4 per-body daemons; merge the recycle detectors into `lane_wedge_watchdog` | cc-fleet-health | 1 day | medium — doorbell regressions are operator-visible; keep the backstop sweep during cutover |
| 8 | **Secrets: purge + sweep** | 11 `.env.bak*`; 70 transcripts with DSN shapes; `secret-hash-sweep` not loaded; one 0644 log with bot-token URLs | `env_set.sh` keeps only the last 2 backups; load `secret-hash-sweep`; scrub/shred the 70 transcripts' DSN lines (or rotate and accept); chmod/scrub `logs/cc_cai_daemon.err`; add the Dockerfile + `scratch_shape_replay.py` hits to a hand-check | cc-fleet-health | half day | low |

Out of scope but noted: the stale `.claude/worktrees/agent-a530703a010986051/` repo copy should be removed (fork A/B may own worktree hygiene); the `cc-quality-audit-drain` and `irsyad-pii-containment-monitor` jobs exit 1 and are not in this fork's remit to diagnose.
