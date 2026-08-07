# Nazim handoff — 2026-07-27 (written at 88% on operator's instruction "handoff so you can be cleared")

_You are **Nazim / console body** (Mac Mini, `ORCH_BODY_ROLE=console`, tmux `nazim`). Reply to the operator ONLY via `scripts/nazim_send.sh` (a terminal reply does NOT reach his phone). EACH TURN reconcile BOTH `operator_log.unprocessed()` AND `agent_messages` to `orch-console`, answer, stamp handled. This session ran clean on Opus 4.8._

## OPEN DELIVERABLES (tracked as tasks #1–#4; re-create them)

1. **🔴 OFFLINE SLICE NOTIFY — the one that slipped.** cc-cosem-platform's **S2 IS COMMITTED** (`7887654` "roster marking works with no signal, queue drains, conflicts surfaced", ~02:12Z) — the offline attendance slice the operator's been waiting on. BUT the post-reset lane **never reported it**, so my committed "it's testable" notify (op#7525/7550) never fired; I only caught it at 12:12Z when he asked for cosem-platform status (op#7668). I OWNED the gap to him. I nudged cc-cosem-platform (queued) to (a) report S2 + confirm S4 real drop/reconnect verification, (b) give the **operator-test path** — it was built on throwaway-local dev, so he needs a runnable/installable target to try on his phone. **NEXT: when the lane reports, VERIFY at source (don't take 'testable' on its word), then fire the real how-to-test notify** (install → kill network → mark → reconnect → reconcile). LESSON: a "lane will report" trigger is a single point that fails silently — proactively check long-running awaited deliverables.

2. **CAI-635 + operator_log.py cross-host divergence** — awaiting cai's ruling (my `#11929`). Finding: the **Studio hub runs a 07-11 `operator_log.py` (176 lines) MISSING the `_RECOGNIZED_BODY_ROLES` fail-closed guard AND `_LANE_OWNED_TAGS`**; the Mini runs the current 301-line version. My rec: reconcile Studio→current FIRST, then apply CAI-635's TWO edits (add `cosem-caai` to hub-exclusion + console-inclusion — one carve-out removes all backstop; keep it CONSOLE-owned pre-cutover). **Holding all operator_log.py edits until cai rules.** cosem-caai leak is latent.

3. **Media-ingest filename-reuse bug** — `logs/tg_media/photos_file_NN.jpg` get REUSED, so a new client photo points at a stale unrelated image (corrupted the Elly diagnosis; screenshots not trustworthy). Fix `nervous_system/ingest.py` to key each inbound media uniquely (msg-id/hash). Not urgent.

4. **Storefront photo feature (merchant Hadi)** — cc-storefront-1 building @dookanabot camera/gallery product-photo upload (op#7642). Hub owns Hadi's thread + reviews; confirm when it ships.

## IN-FLIGHT (hub-owned; I'm OUT except a gate call that won't come)
- **Elly bank-import** (Gazzabyte client): cc-irsyad diagnosed it's a ROUTING bug (preparer redirected off the two bank pages; her perms were always fine). Fix reviewed + SHIPPING under the hub (`a0e5b89`, page gate fail-closed verified, drift-guard test). Gate ruling: routing/shell fix ≠ cai money gate; re-gates only if it touches money/audit/permission-SCOPE.
- **cosem-adcda live-app hotfix** (op#7652/7653/7656): cc-cosem-adcda-1 (NEW, `cosem-adcda-hotfix` worktree, branch `hotfix/print-save-theme-20260727`) building 4 symptoms: namelist PDF overflow, attendance PDF overflow+RTL, save-PDF broken, back-nav theme regression. Hub owns operator thread + review.

## THE FLEET (all on Max, auth_fp `68142948c003`)
- **hub** cc-orchestrator (Studio tmux `orch`) — reset this session (session `87687456`); delegates Gazzabyte→cc-irsyad + storefront→cc-storefront; polls cc-irsyad directly (it's excluded from auto-wake); owns client threads + sends.
- **cc-irsyad** (Mini tmux `irsyad`) — reset this session; SUPERVISED (drafts via lane_reply.sh, hub sends); GIRO on HOLD (blocked on unanswered A-vs-B decision + cai money gate; I OFFERED to lay out A-vs-B for the operator — open offer).
- **cc-cosem-platform** (Mini tmux `cosem-port`, worktree `cosem-port-lane`) — offline slice; S2 committed-unreported (item #1).
- **cc-storefront-1** (Mini tmux `storefront`, worktree `ihsanos-storefront`) — NEW this session, building Hadi feature.
- **cc-cosem-adcda-1** (Mini tmux `adcda`, worktree `cosem-adcda-hotfix`) — NEW this session, print hotfix.
- **cc-caai** — stood down (Ray/scheduling thread closed clean).

## LOAD-BEARING LESSONS THIS SESSION (some now in MEMORY.md)
- **Delegation model (op#7581 → generalized op#7646):** the hub COORDINATES/REVIEWS/OWNS-THREADS + holds singleton pens — it DELEGATES real builds to lanes, never absorbs (that's the context-bloat behind its resets). [[feedback_hub_delegates_builds_not_absorbs]].
- **Lane spin-up (I did 2: cc-storefront, cc-cosem-adcda):** (a) register the worktree basename in `agents.repo_scope` or `launch_dangerous_cc.sh` fail-fast ABORTs; (b) **FIRST-TURN NUDGE** a freshly-spun lane (`lane_nudge.sh`) — the interactive REPL doesn't auto-start, it sits idle until nudged; (c) provision `.env.local` by COPYING a working same-silo worktree's (don't hand-build); (d) **"Claude API" banner = OAuth/Max, NOT metered** — verify `agent_status.auth_fp` populated (I killed a good lane once over this — [[reference_claude_api_banner_is_oauth_max]]); (e) Mini lanes live on `/usr/local/bin/tmux` socket, NOT `/opt/homebrew` — [[reference_mini_tmux_two_binaries_socket]]. Built **`scripts/reset_lane.sh`** (safe in-place lane /clear+boot, busy-guard + composer-preserve, mirrors reset_orch).
- **Model:** `NAZIM_MODEL` + `boot_cai.sh`/`boot_orch.sh` defaults flipped opus-5→opus-4-8 (committed `06ec6e3`). Lanes stay Opus 5 per `.fleet_model`.
- **Token P0 REMEDIATED:** operator revoked the burned `sbp_2180…` PAT + rotated; verified files clean + new tokens live (both-direction control). Done.
- **op#7421 self-adversarial reviews (Nazim+hub+cai on 4.8):** findings HOLD; the over-correction was asserting to the operator before verifying at source. Applies to me: **verify at source before asserting** (I slipped twice this session — the "Claude API"=metered misread, and relying on the S2 report-trigger).
- **`scripts/adcda_port_watch.py`** (launchd `dev.wingmen.adcda-port-watch`, 3h) — watches the LIVE cosem-adcda repo for features to port, flags cc-cosem-platform.

## STANDING
Verify every "done" at source (testimony has been wrong). Name the host in every path. Nudge cai only via `nudge_cai.sh` (count-only, refuses on staged composer). Never raw send-keys into a lane composer — use `lane_nudge.sh` (verified-submit). Everything committed + pushed on `fable/substrate-safe-fixes` except a pre-existing `autonomous_loop_detector.py` edit. **`scripts/reset_nazim.sh` picks the NEWEST `nazim-handoff-*.md` = THIS file.** I auto-compact, so a reset is his choice.
