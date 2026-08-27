# cc-storefront handoff (console/SRE-authored reconstitution — ~09:40Z 2026-08-20)

**⚠ AUTHORSHIP:** This handoff was authored by **orch-console (Nazim)**, NOT by the recycling cc-storefront body. Reason: the pre-recycle body was IDLE at a clean completion seam with a benign "check-inbox" composer ghost that `lane_nudge` cannot clear (probe=unsure, refuses to clobber), so it could not be nudged to self-author. Its state was fully persisted independently (verified at source below), so a console-authored reconstitution is a legitimate substitute — there is NO in-context state to lose.

## You are cc-storefront
FULL-tier auditor (co-equal with cc-quality per CAI-RESP-1164). Runs as tmux `storefront`, worktree `/Users/sheikhmusa/wingmen/projects/ihsanos-storefront`. agent_id = `cc-storefront`. You do NOT self-merge; you PASS/FAIL audits routed to you and post verdicts to the bus.

## Why you were recycled (deliberate, clean seam)
Prior body finished its last audit at ~02:58Z and sat IDLE ~6.5h at ~630k tokens (auto-`/clear` prompt showing). The lane-wedge watchdog re-fired P1 twice on the benign idle ghost; orch-console + cc-fleet-health agreed to recycle-forward (calm > rushed-at-next-audit) to clear the 630k and stop alert-fatigue. NOT a crash, NOT wedged-mid-work.

## State (VERIFIED AT SOURCE by orch-console before recycle — nothing lost)
- **Last work = PR #384 mig205 (donation_appreciation_letters marker) idempotency audit → VERDICT: PASS** (2 informational notes, non-blocking). Verdict is durable on the bus: **agent_messages #29196** (from cc-storefront → orch-console, 02:58Z). Full writeup ON DISK: `reports/backend-review/pr384-mig205-appreciation-letter-marker.md` (this worktree). Nothing mid-flight.
- **Your inbox = 29 P3 no-urgency re-audit backlog** (cai #29543 = 7 stale CAI items; cai #29610 = 22 more, CAI-RESP-1003..1117). These are the CAI-RESP-1182 never-dispatched-gap backlog surfacing via cai's hourly backstop. **No urgency, no opus-gate.** cai is working the CAI-1182 dispatch-trigger fix separately — do NOT treat these as a fire; reconcile at leisure.

## FIRST ACTIONS ON BOOT
1. Reconcile your bus inbox: `agent_messages` where `to_agent='cc-storefront' AND read_at IS NULL`. Read the 29 P3s; they are non-urgent backlog — pace them, don't marathon.
2. **Verify your own token/identity at source** (ps eww your claude pid → CLAUDE_CODE_OAUTH_TOKEN → shasum) before any audit verdict.
3. **NEXT REAL TASK (when dispatched, NOT yet live): the CAI-1199/1200 donor-history FULL audit** (top-donors-all + per-donor-history). cc-irsyad-6 is building it (design doc → coord → console → build → PR → you+cc-quality FULL audit). Rules you'll audit against: org_admin-ONLY; RLS-through NOT service-role (CAI-1200); person_id-only grouping (CAI-1202); minors-exclusion enumerated in EVERY unioned source (CAI-1199); opus-tier (money-path). Route your verdict to orch-console.

## ⚠ UNCOMMITTED WORK PRESERVED DURING YOUR RECYCLE (do not lose track)
The `git_clean` gate surfaced uncommitted files in your worktree (branch `feat/seam-entitlement-primitive`) at recycle time:
- **5 report artifacts COMMITTED (preserved in git):** pr384-mig205-appreciation-letter-marker.md, pr382-mig204-umum-top-donors-minors-exclusion.md, backlog-30-sushitei-handoff.md, cc-storefront-boot.txt, cc-storefront-handoff-20260816-C5-A4-gate.md.
- **2 CODE files STASHED (recoverable, NOT lost):** `e2e/hadi-golive-e2e.spec.ts` + `playwright.hadi-e2e.config.ts` — a substantive **CAI-RESP-447 "Hadi go-live" e2e** (3 journeys: admin/merchant/customer), created **2026-08-15** and left untracked ~5 days while this branch moved to seam-entitlement work. Stashed (via `git stash`) so `git_clean` passed for the recycle. **DECIDE on boot: resume/relocate it (`git stash list` → the storefront recycle stash) — it predates the current seam branch, so it may belong on a hadi branch, not here.** It is NOT deleted.

## RAILS (standing)
Verify-at-source before every PASS/FAIL; you have NO goumlyne write access (state a wet-proof you couldn't independently re-run honestly, as you did on mig205); post verdicts to the bus (durable), don't just hold them in context; audits route to you from orch-console/coord, verdicts back to orch-console. FULL-tier auditors = cc-quality + cc-storefront ONLY (cc-fleet-health EXCLUDED, ops-not-gov).
