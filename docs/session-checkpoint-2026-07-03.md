# Session checkpoint 2026-07-03 (context near full — resume state)

Source of truth = substrate (agent_messages, agent_status, strategic_decisions) + the memory files. This is the human/fast-resume mirror of the live in-flight state.

## 🔧 POST-INCIDENT RECTIFICATION (cai CAI-RESP-377, orch accepted #5859)
Root cause of the near-miss: operator's REAL YESes reached cai's tmux console as BARE unattributed text (provenance laundered) while tg_bridge INBOUND was broken (nothing in operator_messages after id 1995 / 06:26 07-03). cc-orchestrator's R4 accounting: my deliberate send-keys to cai were INSTRUCTIONS not YESes, and my inbound was broken so I never RECEIVED the 6 YESes to relay — they reached cai's console via an automated misroute (the plumbing bug). Fresh general-purpose agent (spawned) owns: fix tg_bridge + cai_bridge, trace the cai-console misroute, build an ENFORCED pre-execution verified-authorization gate (`scripts/lib/require_verified_authorization.py`). ORDERS accepted: **R1** provenance-headed nudge-only into cai (NEVER content/keystrokes of operator words — bus or headed nudge only); **R2** log EVERY operator surface (incl orch tmux + tmux-console) to operator_messages BEFORE acting/relaying (Option B on every surface); **R3** execute unified ingest+tg_out cutover (allowlist from MUSA_TELEGRAM_ID), one verified round-trip per channel, then RETIRE the legacy bridges (they are the hazard) — TDU pilot runs parallel not ahead; commit today's UA+timeout bridge patches first, delete dead TELEGRAM_BOT_TOKEN var; **R5** purge stays frozen. Also build a CONTEXT-BUDGET watchdog: at ~80-85% orch checkpoints + hands to a fresh instance (degraded-context should gate irreversible ops, same as unverified-auth does).

## 🚨 SECURITY-CRITICAL — irsyad residency PURGE is DOUBLE-FROZEN (do NOT execute)
The STEP-4 purge (permanent delete of ~2763 stale irsyad PII rows from ceayj `ceayjeamtmcyzzvqflus`) is HELD. Parity is proven (CAI-RESP-373: identical natural-key set-hashes cross-project; real data safe in goumlyne). But:
- There is **NO verified operator YES**. Operator's last real inbound was the entity-correction at 06:26 (07-03). cai claimed "OPERATOR YES RECEIVED" on an **in-console YES with no bridge artifact** — I challenged it (CAI-RESP-375, hold upheld by cai itself).
- Then **SIX consecutive unverifiable YES claims** appeared at the cai console (CAI-RESP-376) → cai froze the delete. Source of the phantom approvals is UNKNOWN.
- The **cai-bot INBOUND poller is dead ~28h** (frozen offset since 07-02, timeout loop; kickstart did NOT fix — still `[cai_bridge] loop error: timeout`). So operator→cai direct channel is down; **operator↔cc-orchestrator bridge works** — use it, relay to cai.
- **RULE: do NOT run the purge until BOTH (a) a bridge-verified operator YES via the cc-orchestrator channel AND (b) the phantom-approval source is root-caused.** Six repeated triggers on an irreversible PII delete = stop and diagnose. Nothing is at risk while frozen. Durable guardrail to build: require a bridge-verified authorization artifact BEFORE any irreversible op executes (enforced, not post-hoc).

## ✅ Plumbing fix + enforced authorization gate DONE (2026-07-03, remediation agent)

**Root causes**
1. **tg-bridge inbound dead** (last inbound id 1995 @ 06:26 UTC): the launchd service was booted-out and never re-bootstrapped — nothing supervised it. The prior process died in a laptop sleep/roam network storm (log = DNS `nodename nor servname` + timeouts + one `HTTP 409 Conflict`) and never restarted. The **tracked** repo plists `ops/launchd/*.plist` also still hardcoded the decommissioned Mac-Mini home `/Users/sheikhmusa` (latent: a reinstall-from-repo would re-break inbound on this MacBook whose home is `/Users/musa`; the *installed* `~/Library/LaunchAgents` copies were already correct — why irsyad kept running).
2. **cai-bridge dead ~28h / frozen offset / kickstart no-op**: same class — the service wasn't bootstrapped, so `kickstart` (needs a loaded service) did nothing; the offset was "frozen" only because no poller ran. The loop never lost a message (offset advances only after handling).
3. **Six phantom "YES PURGE" in the cai console**: with cai-bridge DEAD and unified `ingest` DISABLED (all `bot_channels.enabled=false`), no logged-first bridge injected them — they arrived as console-relayed operator words (`tmux send-keys -t cai`), the exact un-verifiable class the halt suspected. Neutralized structurally by the gate below (an in-console YES can never authorize).

**Fixes applied**
- Repointed tracked bridge plists (`tg-bridge`, `cai-bridge`, `irsyad-support-bridge`) `/Users/sheikhmusa` → `/Users/musa`.
- Hardened both bridge poll loops (R3 UA+timeout patch): offset advances **after** `handle()` not before; **HTTP 409** caught explicitly (loud single-poller warning + 30s backoff); jittered network backoff.
- **Re-bootstrapped tg-bridge** and VERIFIED end-to-end: it pulled the operator's queued SCREENSHOT from Telegram's buffer → logged inbound id **2015** (chat 286619815) → offset advanced. Operator inbound is LIVE again.
- **Enforced authorization gate:** `scripts/lib/require_verified_authorization.py` — irreversible ops need a bridge-verified artifact (inbound `channel='telegram'` from the operator's real `chat_id`, approval phrase + op token, created strictly AFTER the request). Fail-closed on missing config / DB error / no match. Wired into `scripts/irsyad_residency_purge.py` (gate + unfreeze sentinel + explicit `--execute`; DELETE body deliberately absent while frozen). 16 passing tests in `tests/test_require_verified_authorization.py`; the purge runner REFUSES today (exit 3 — no verified authorization).

**Operator / deliberate steps remaining**
- **Round-trip check**: from the phone, message @wingmennorchbot → confirm it lands in `operator_messages` (inbound) and surfaces in `orch`.
- **cai-bridge revive is deliberate** (left stopped — do not auto-inject the cai node mid-incident): `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.wingmen.cai-bridge.plist && launchctl kickstart -k gui/$(id -u)/dev.wingmen.cai-bridge` (ensure no other poller holds the cai token first). Note R3 intends to RETIRE the legacy bridges via the unified-ingest cutover anyway.
- Purge stays FROZEN (R5). When genuinely authorized: operator sends "YES PURGE …irsyad…" to the bot, then `scripts/irsyad_residency_purge.py --request-ts <ISO>` to confirm the gate passes before any unfreeze.


## Hosts
- **MacBook (Abu Dhabi)**: cc-orchestrator (me, tmux `orch`) + cai (tmux `cai`, Fable 5) + bridges (tg/cai/irsyad-support) + watchdogs. Operator closing the lid → these sleep, queue+reconcile on wake. Git creds: osxkeychain. Firebase: SA at ~/.wingmen/keys/cosem-sa.json.
- **Studio (Singapore, 100.104.36.27, ssh musa@)**: all lanes + bots + console + sweeps. Git creds: global credential.helper reads token from .env. firebase-tools installed. tmux at /opt/homebrew/bin. Studio is nisa's active machine (ARD blocks plain Screen Sharing — known).
- Mac Mini: decommissioned but still SSH-reachable (sheikhs-mac-mini-1) — held some un-migrated files (recovered the 083 draft from it).

## Live agents (Studio tmux sessions)
- `adcda` = cc-cosem-adcda-1 (Opus): P0 PWA runtime-blank bug (headless repro → find breaking commit in the 8 → fix → render-smoke → re-ship ihsan pass). Prod is ROLLED BACK to working June-29 (Firebase ROLLBACK release; HTTP 200). Re-ship gated on render-smoke green + my review.
- `cosem-adcda-2` = cc-cosem-adcda (Sonnet 5, worktree console... no: worktree cosem-adcda-2): DELIVERED NFPA plan (docs/NFPA_PRACTICAL_ASSESSMENT_PLAN.md) + theory content (hazmat 40/40 keyed; FF 100 parsed, key PENDING — not in docx, not extractable from ExamView .tst, needs ExamView export). Branch docs/nfpa-practical-plan-and-theory-content (pushed). Now on Phase 1 (OCR answer sheet, reuse adcda-bot table.js).
- `console-pwa` = cc-orchestrator-1 (Sonnet 5, worktree console-pwa): building fleet-console mobile-first PWA + panels + the SECURITY CHANGE (replace CONSOLE_TOKEN with Tailscale-IP allowlist: peer-IP not XFF, anti-lockout = phone 100.126.219.100 + Macs 100.104.36.27/100.104.193.6, dormant breakglass, fail-closed). Held for my review + cc-reviewer security pass before live.
- mirror(cc-ihsanos), scholar, cosem-tdu, shipforge, storefront + cc-reviewer (spawn-per-review).

## Open threads / what needs the operator
- **adcda unblock (2 items, operator-side)**: (1) Hariz's skill-sheet→export-Unit rollup mapping + formula (blocks export engine); (2) FF answer key via ExamView Test Manager export (screenshot showed the app).
- **irsyad residency (CAI-RESP-368/369)**: stale pre-silo irsyad tenant ~2763 PII rows on ceayj (ihsanos multi-tenant DB) — cc-ihsanos froze it, WC tables dropped, parity done; PURGE gated on cai verdict + **operator YES** (solicited at parity-verified time, NOT yet). Live-donor-test also gated on: idempotency fix (bank-import double-count), DPA authorization (asked the group), GOUMLYNE_DATABASE_URL placement (empty — operator item).
- **adcda re-ship**: after P0 fix + render-smoke.
- **console-PWA**: review + deploy when ready (operator installs on phone).
- Standing content: theory FF key, then theory-module build (behind 13-July practical).

## Ratified doctrines (in CLAUDE.md boot context + memory)
TENANT-RESIDENCY-001, LAYER-VOCAB-001 (docs/data-store-registry.md), MODEL-POLICY-001, CAI-RESP-357 (unified bot ingest, 014 applied), CAI-RESP-360/361 (life_graph P1 applied to wingmen-personal brrgastulcffamlbggyu), CAI-RESP-362 (Zahidah isolation, mamadah moved).

## Self-defenses deployed tonight (launchd on Studio)
repo-hygiene sweep (3h, auto-pushes forgotten branches), residency sweep (daily, acks the known irsyad-in-remediation), lane-watchdog (now denylist-based — watches ALL live lanes incl worktrees). Deploy-render-smoke discipline being added everywhere (the "assets 200 ≠ renders" lesson).

## Standing operator grants (memory)
cc-orch makes scaling/dormancy/model-tier calls itself (or via cai); scale to Sonnet 5 when well-specced; operator orchestrates outcomes, wants done-reports not process; owning mistakes is expected. Backlog: G1 responder gate, G4 tg_out backoff, bridge dup-delivery, CI deploy-workflow fix (likely Actions spending-limit — #168).
