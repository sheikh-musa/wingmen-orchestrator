# Xendit POS — call-ready build brief (operator Xendit call MON 2026-07-20)

**To:** cc-ihsanos lane, worktree `~/wingmen/ihsanos-wt/xendit-pos`, branch `feat/xendit-pos-integration` (@40ba3dd)
**From:** cc-orchestrator (hub) — relayed from Nazim #9608 / operator op#5136
**Priority:** P1, deadline Monday 2026-07-20. Goal: a STABLE, clickable TEST-mode demo URL the operator can drive on the call + a webhook URL he can optionally paste into the Xendit dashboard for a test transaction. **TEST MODE ONLY — never enable live Xendit tender / no real money movement.**

Current state: BUILD-COMPLETE @40ba3dd (Xendit gateway TEST mode — PayNow SG + QRIS ID, full + lite POS; e2e both pass) but built off OLD main (f623979). Main has since advanced to 14f5684 (WP-C NETS/cheque + mig 106/107).

## STEP 1 — Rebase onto current origin/main
- Rebase `feat/xendit-pos-integration` onto `origin/main` (@14f5684). Resolve conflicts.
- **KNOWN COLLISION — migration 071:** this branch carries `supabase/migrations/071_pos_tender_nets_cheque.sql`, but main ALREADY has a 071 from WP-C (#170, "NETS + Cheque POS tenders + migration 071"). Do NOT end up with two 071s / duplicate tender schema. Compare them: if the Xendit branch's 071 is the same NETS/cheque feature that landed via WP-C, **DROP this branch's 071 and adopt main's**. If the Xendit gateway depends on POS schema that main's 071 does not provide, reconcile into a NEW migration (see Step 2 numbering) — never redefine an applied object. Verify the POS tender code paths still compile against main's 071.

## STEP 2 — Migration numbering (HUB-COORDINATED — do not free-pick)
- Main tops at **107**. **105 is in-flight** elsewhere (fix/ceayj-mig105 worktree) — do NOT use 105.
- **Xendit gateway migration = 108.** Keep `108_pos_xendit_gateway_payment.sql` as **108** (108 is free on main). Ensure `_reserved.txt` records 108 = xendit gateway.
- Do NOT use 109 — the paused irsyad donor-import lane's `persons.address_encrypted` migration is reserved **109** by the hub. If you genuinely need a SECOND migration, use **110** and tell the hub so I keep the ledger straight.
- If Step 1 forces a reconciled tender migration, number it **110** (not 071-rewrite).

## STEP 3 — Build + test green (deploy gate is REAL build, not just tests)
- `next build` (real production build) MUST pass — not only vitest/tsc. Absolute cross-repo paths pass locally but break in the deploy container; the deploy gate runs the production build.
- Re-run the e2e (full + lite POS PayNow/QRIS test-mode flows). Confirm still green post-rebase.
- POS UI is mobile-first + must clear the ihsan bar (the operator will screen-share it live) — self-check the demo pages render cleanly on mobile + desktop before handing back.

## STEP 4 — Report to hub for the gate (do NOT self-deploy)
- Report: rebased SHA, how the 071 collision was resolved, migration numbers used, `next build` + e2e results, and the intended TEST-mode deploy target.
- Hub then: spawns cc-reviewer (payment path correctness + POS UI design/mobile) → verifies the production build → drives the TEST-mode deploy to a demo-accessible env (stable URL + webhook URL). **The migration 108 apply target for the demo must be a TEST/preview DB — NOT live goumlyne/ceayj** (no live-silo schema change without the money gate). Confirm the demo DB target with hub before applying any migration.

## Guardrails
- TEST mode only; no live Xendit keys, no real settlement. Webhook is for a Xendit-dashboard test transaction.
- Do not push to origin or deploy yourself. Authored + built + tested; hub owns review + deploy.
- Money-path code (payment gateway) → cc-reviewer finance + security dimensions before any live-adjacent step.
