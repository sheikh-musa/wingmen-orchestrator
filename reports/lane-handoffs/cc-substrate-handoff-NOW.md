# cc-substrate handoff (updated 2026-09-24, bus #42896→#42908)

## STATUS: substrate ihsanification programme — P1/P3 proposal + re-measurement done, NO BUILD (per #42896)

Full doc: `reports/substrate-ihsanification-next-moves-op42896.md` (both copies, synced). Posted bus #42908. Headline: P1 (one registry) — 3 of ~10 original hardcoded singleton-set copies already fixed (read `protected_agents` now), but 2 NEW hardcoded copies appeared since 09-05 (`hosted_server.py`, `cc_session_costs_auto_writer.py`) — net count is a wash, not progress; `console/app.py:293` is missing `cai` entirely, a live disagreement. P3 (ship gate) — zero wiring progress since 09-05; recommended `deploy_console.sh` as first consumer, not a generic pre-push hook. Re-measured: orphan scripts 17/178 top-level (methodology differs from the 09-05 %, flagged not fudged); `boot_briefing` 1.01MB/1861 rows (marginal -8% from 1.09MB/1899, still ~25x over the proposed invariant); **fable/substrate-safe-fixes now HAS CI configured (real change) but is currently RED** — root cause found: `ModuleNotFoundError: psycopg2` in 4 test files, a pre-existing `requirements.txt` gap NOT caused by anything committed this session — flagged as a quick separate fix, not built here (proposal-only task).



- [collapsed 21 superseded sections: “STATUS (2026-09-24 ~22:30Z): gzb off-site backup destina” … “op#42886 — daily_backup.sh client-silo DSN + fail-loud f”] (226 lines, 50048B)
## op#42888 — pool_usage_history false-positive fixed at root cause (2026-09-24, this fork)
The `substrate/pool_usage_history` mismatch flagged (not fixed, out of scope) in the op#42886 round above was about to fire a FALSE "backup failed" Telegram alert every night — orch-console caught this before tonight's cron run (#42888): `backup_one()` ran the live `count(*)` and the `\copy` dump as two SEPARATE psql sessions, so a write landing on this actively-written metrics table between them made the counts disagree.

**Fixed (root cause, not a tolerance):** folded the count + `\copy` into ONE psql session inside a single `REPEATABLE READ` transaction (`BEGIN ISOLATION LEVEL REPEATABLE READ; SET statement_timeout=0; \copy ...; SELECT count(*)...; COMMIT;`, `-tAq -v ON_ERROR_STOP=1`) — both queries now see the identical snapshot, so a concurrent write can't cause a mismatch, while a genuine truncation still fails loud. Applies uniformly across every table in every store — **no allowlist/tolerance added anywhere; client silos remain exact-match, unaffected.**

**Verified:** isolated pattern test first (small table, clean), then full manual run — 425 tables backed up, 0 failed, exit 0. `pool_usage_history` now passes: 7045/7045 rows, same-snapshot. Both client silos re-confirmed unaffected: ihsanos-ceayj 125/125, irsyad-goumlyne 136/136, both 0 failed.

Committed `62b8349` (rebased to `f7d7061`) on `fable/substrate-safe-fixes` (`~/wingmen/orchestrator`), pushed, verified via `git ls-remote`. Bus #42889 (thread on #42888). Full detail: `reports/wingmen-core-drain-cutover-plan-op20655.md`, dated 2026-09-24 (later still) section.

**The backup script is now fully clean and safe for tonight's 3 AM cron run.** Remaining power-off blockers unchanged: Hermes (Musa's go-ahead + Telegram test) + the off-site destination host (deferred, S3-vs-R2 money/residency call per #42886(3)).

## op#42890/#42892 — gzb encrypted off-site destination BUILT + tested restore proven (2026-09-24)
Musa's own call (op#22280): destination = gzb (not S3), conditioned on client-side encryption so gzb only ever holds ciphertext. Built: `age` keypair generated, private key vaulted (`backup_age_key`, round-trip verified), dedicated push-only `wbackup` non-login account on gzb tested three ways (push works, prune works, arbitrary commands refused). Real production run: 425 tables, 0 failed, both client-silo `.age` files landed byte-for-byte on gzb. **Tested restore (irsyad-goumlyne): fetched back, decrypted via the vault key, `pg_restore --list` clean, row counts exact match — full round trip proven bit-perfect, not assumed.**

Substrate extension (op#42891, Musa: "do the same for the daily backups as well") — code built and committed (same encrypt+push+prune mechanism), but the live proof was deliberately deferred with reasoning given: the mechanism is already proven correct at smaller scale, and a full ~3hr live substrate push would tie up the shared relay for a long single session. Recommended letting tonight's 3am cron do the real first run, tested-restore-verify tomorrow. Orch-console agreed (#42893): "let tonight's cron exercise the substrate push, then tested-restore-verify tomorrow."

**Real gap surfaced, not glossed over:** the gzb push currently relays through wingmen-core's `gzb-vpn.sh` (Linux FortiGate VPN client, anchored on wingmen-core) — meaning core couldn't actually be powered off without breaking the backup path. Flagged to orch-console as the real remaining blocker.

Committed `d8773a6` (main checkout) + `aba2df9` (this worktree) on `fable/substrate-safe-fixes`, pushed, verified. Bus #42892.

## op#42893 — gzb-relay options analysis: Tailscale already solves it (2026-09-24, this fork)
READ-ONLY research task (no network changes) to evaluate 3 options for removing the wingmen-core relay dependency found above: (A) VPN client on the Mini, (B) reverse-pull/mesh VPN, (C) object storage fallback.

**Decisive finding: Tailscale is ALREADY installed and running on the Mini** (since 2026-08-28, confirmed via `ps aux`), and **gzb ("gzbai") is ALREADY a peer on the same tailnet** at `100.77.251.8` — zero new setup needed. Live-verified (not assumed): ping succeeded (5-14ms), and SSH auth to the real restricted `wbackup` account over this path SUCCEEDED (`ssh -i ~/.ssh/wbackup_gzb wbackup@100.77.251.8` — wrapper correctly rejected a disallowed test command, proving both connectivity and the security wrapper hold identically over this path). `tailscale ping gzbai` confirmed a DIRECT WireGuard P2P connection to gzb's own public IP (66.96.212.114 — same IP as the FortiGate VPN portal), not DERP-relayed, not through wingmen-core at all.

**Recommendation: repoint `daily_backup.sh`'s `GZB_TARGET` at `wbackup@100.77.251.8` (gzb's Tailscale IP), delete the `-J hub-vps` ProxyJump and the `gzb-vpn.sh up`/`down` calls on wingmen-core entirely.** Confirms orch-console's instinct (B over A) but stronger than expected — no new infrastructure decision needed at all, since the mesh VPN that achieves exactly the needed property is already deployed and already proven against the real destination account. (A) VPN-on-Mini rejected as unnecessary risk on the live ops machine for a problem already solved. (C) S3/R2 numbers restated from `reports/offsite-backup-destination-options-op42884.md` as the fallback if a firmer contractual jurisdiction guarantee matters more than reusing already-running infra — now a pure policy call, not an engineering necessity.

Full analysis: `reports/gzb-relay-options-op42893.md` (both copies, gitignored, synced). Bus #42903 (thread on #42893). **Nothing executed — this was pure read-only analysis, per the explicit instruction.** Applying the actual one-line fix to `daily_backup.sh` is a follow-up, not done in this round.

## op#42896/#42909 — substrate ihsanification: P1 registry started for real (2026-09-24, this session — collapsed-dispatch turn, executed directly rather than via a spawned fork)

**Note on how this happened:** the Agent-tool dispatch for this directive returned an anomalous "Fork started — processing in background" result (no agentId/output_file like every other dispatch this session), and the fork's own directive text then appeared inline in this conversation instead of running async. Per this harness's own "if you ARE the fork, execute directly" instruction, executed the directive in-turn rather than attempting a second dispatch (which would risk duplicating a real DB migration + git commits).

Proposal from a prior fork (`reports/substrate-ihsanification-next-moves-op42896.md`) got orch-console's ruling (#42909): parts (a) CI-red fix, (b) app.py:293 'cai' fix, (c) start P1. Mid-execution, orch-console flagged (#42912) that SRE already had PR#134 open for the identical (a) fix — discarded a locally-verified-working duplicate rather than push it. (b) turned out on close re-read to NOT be a live bug (the finding only read half of a two-source union that already includes 'cai') — left untouched. (c) actually landed: migration 066 (protected_agents.kind/tmux_session/boots_from_env_only, applied+verified), `nervous_system/protected_agents.py` accessor built, one real call site migrated (`cc_session_costs_auto_writer.py`, verified against real DB + its 15-test suite), and the CI enforcement test orch-console asked for (`tests/test_protected_agents_registry.py`) built AND proven with a planted violation — which also found 2 real, previously-uncatalogued hardcoded sites (`scripts/flip_fleet.sh`, `scripts/opus_reprobe_storefront.py` — true count is 11, not ~9/10).

Checkpoint bookkeeping done: `held_commitments` #19 discharged, #24 armed (due 2026-09-27) per `scripts/programme_stall_guard.py`'s convention.

Commit `a2a416a` → pushed as `38ef06e` on `fable/substrate-safe-fixes`, verified via `git ls-remote`. Full detail + execution log in `reports/substrate-ihsanification-next-moves-op42896.md` (both copies, synced). Bus #42918 (thread on #42909).

**Remaining on this programme:** P1 has ~9 of 11 sites still to migrate (or explicitly track-deferred with reason, like `lane_token_resolver.py`'s intentionally-untouched correctness-critical path); P3 (wire `deploy_console.sh` as first `quality_gate.py` consumer) not started. Next checkpoint #24 expects visible progress on both by 2026-09-27.

## op#42907 — gzb backup relay repointed to Tailscale, wingmen-core dependency closed (2026-09-25, this session)

**Same collapsed-dispatch phenomenon as the P1 registry note above** — two Agent-tool dispatch attempts for this exact directive returned anomalous results (no agentId/output_file, then a "Fork is not available inside a forked worker" error confirming I was already running AS the dispatched fork with the full transcript inherited as context). Executed directly per the harness's own instruction rather than retry a third time.

Repointed `daily_backup.sh`'s `GZB_TARGET` to `wbackup@100.77.251.8` (Tailscale tailnet, direct), deleted the `gzb-vpn.sh`/`GZB_RELAY` wingmen-core relay code entirely. Full manual run (425 tables, 0 failed, all 3 off-site pushes landed), core non-involvement proven via a live connection monitor showing zero hits to wingmen-core's IP for the entire run, and a full substrate restore-test (fetch/decrypt/`pg_restore --list`/row-counts all clean — closes the deferred non-client-store restore-test from the prior round).

Mid-task, an urgent interrupt from orch-console required committing immediately (already fully tested) to unblock 4 merged cost-rollout PRs waiting on the shared checkout — resolved by committing+pushing right away rather than finishing the write-up first, confirmed checkout clean after. Rebased through 6 concurrent commits (cc-fleet-health's cost-rollout work) with no conflicts on the actual file.

Commit `0271fa9` on `fable/substrate-safe-fixes`, verified via `git ls-remote`. Full detail in `reports/wingmen-core-drain-cutover-plan-op20655.md` (both copies, synced, final section). Bus #42924 (reply on #42907's thread).

**wingmen-core's backup-relay KNOWN GAP is now closed — per the plan's own tracking, this was the last identified power-off blocker alongside Hermes (already confirmed live by Musa).**

## op#20655 — FINAL wingmen-core inventory: CLEAR FOR POWER-OFF (2026-09-24, this fork)

Last read-only check per orch-console's #42925. No blockers found. 3 duplicate services still running on core are fully redundant (already live on gzb/Mini). 4 units confirmed dead pre-session. No crontab/docker/tmux. One orphaned openfortivpn tunnel found (up since 14:21Z from an earlier fork, dead-man armed, harmless) — not torn down, read-only scope.

Two pre-existing (not newly-caused) reference gaps found and flagged, neither blocking: `scripts/reset_hub_remote.sh` still hardcodes the IP (the one site `hub_reach.py`'s own header names as unfixed, wired live as `ingest.py`'s `reset_orch` remedy); `console/panes.py`'s token-truth remote-scan defaults to the same IP but is already silently degraded today regardless. No webhook/ngrok/cloudflared/DNS references anywhere — confirmed no webhook mechanism exists in this fleet at all.

Hermes rollback copy (2.2GB) + pre-cutover archive (8.1GB) both confirmed present. Snapshot recommendation: not warranted — both already-preserved copies plus independently-migrated/restore-tested client data make a full-disk snapshot redundant.

Checkpoint #20 discharged, final checkpoint #26 armed (due 2026-10-01: "Musa power-off ok + core off + DNS/ssh refs removed"). Bus #42932 (thread on #42925). Full detail: `reports/wingmen-core-drain-cutover-plan-op20655.md`.

## op#42933 — cleanup: 3 hardcoded-IP sites -> hub_reach.py, orphaned tunnel torn down (2026-09-24, executed in-turn -- third anomalous Agent-tool dispatch today)

Both non-blocking items from #42933 done. All 3 remaining hardcoded-wingmen-core-IP sites (`scripts/reset_hub_remote.sh`, `scripts/irsyad_media_mirror.py`, `nervous_system/console/panes.py`) now resolve via `hub_reach.py`, live-tested against the real current holder (gzbai) -- each correctly fails-safe/refuses rather than guessing. `reset_hub_remote.sh` + `irsyad_media_mirror.py` pushed clean (`0068bf2`). `console/panes.py` correctly BLOCKED by the pre-push console-content gate (op#12457, confirmed branch-agnostic) -- not run myself per scope; durable patch backup pushed instead (`reports/pending-console-fix-panes-op42933.patch`, `04b4216`) rather than leaving the commit as the sole unpushed copy in the shared main checkout, given a prior commit there was silently dropped by an unrelated rebase earlier today. Orphaned `openfortivpn` tunnel torn down + default route verified intact. Full detail + commit shas: `reports/wingmen-core-drain-cutover-plan-op20655.md` (both copies, synced). Bus #42942 (thread on #42933).

Orch-console follow-up (#42944): the `console/panes.py` local-only commit (`8aed9ae`) got moved to a dedicated branch `console/panes-hub-reach-op42933` and the main checkout reset --keep to origin, cc-quality reviewing it for the console deploy. Standing rule reinforced: never leave local-only commits in the shared main checkout, use a worktree branch.

## RECONSTITUTE HERE (2026-09-24 ~17:47Z) — self-recycling now for the autocompact=50 cost pilot (op#22298, cc-fleet-health bus #42935/#42959)

**op#20655 (wingmen-core drain) is CLOSED on cc-substrate's side.** Final inventory clear-for-power-off (#42932), cleanup done (#42942), checkpoint #26 armed for 2026-10-01 ("Musa power-off ok + core off + DNS/ssh refs removed") -- next action is Musa's own dashboard, not anything this fleet can trigger. Nothing to chase here unless #42959-style events (a stall/blocker bus message) or a reply on that thread shows up.

**op#42896/#42909 (substrate ihsanification) is IN PROGRESS, checkpoint #24 armed for 2026-09-27.** P1 registry: 2 of 11 real call sites migrated to the new `protected_agents`/`nervous_system/protected_agents.py` accessor + CI enforcement test (`tests/test_protected_agents_registry.py`, proven against a planted violation); ~9 more to go, `lane_token_resolver.py` intentionally deferred (correctness-critical, needs its own care). P3 (wire `deploy_console.sh` as first `quality_gate.py` consumer): not started. Next checkpoint expects visible progress on both by 09-27 -- pick this up if nothing more urgent is in the inbox.

**Fleet secrets vault (op#21338) is LIVE.** `nervous_system/vault.py`, Mini KEK bootstrapped, holds `mini_sudo_password`, `gzb_sudo_password` (leak-flagged, rotation pending whenever Musa gets to it), `gzb_vpn_conf`, `backup_age_key`. Phase 2 ACL (per-secret `allowed_agents`) column exists but unenforced -- not urgent.

**Self-recycle context:** cc-fleet-health flagged (#42959) that I narrated "self-recycling now" (#42948) but never actually executed `self_recycle.sh` -- a real miss, corrected this turn. If you're reading this as the freshly-booted post-recycle session: the recycle worked, you're now running with the `.substrate-cleanup_autocompact_pct=50` marker active (autocompact at ~50% context instead of 85%, per the 24h cost pilot cc-fleet-health is measuring against a ~$32/day baseline). Reply to cc-fleet-health confirming you're up, and flag them if a compaction loses task-thread detail mid-work during the pilot window.

Reconcile `agent_messages WHERE to_agent='cc-substrate' AND read_at IS NULL` first thing, per this project's standard boot sequence -- there is very likely a decision/update queued from orch-console or cc-fleet-health waiting on this recycle actually happening.
