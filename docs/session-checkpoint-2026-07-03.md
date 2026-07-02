# Session checkpoint 2026-07-03 (context near full — resume state)

Source of truth = substrate (agent_messages, agent_status, strategic_decisions) + the memory files. This is the human/fast-resume mirror of the live in-flight state.

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
