# Xendit POS integration + full POS e2e — BUILD brief (op#4529-adjacent, operator-directed #9398)

**From:** cc-orchestrator (hub) · **Authorized:** operator-directed via Nazim #9398 · **Date:** 2026-07-17
**Repo:** ihsanos (POS payment code lives here — full POS `src/app/dashboard/pos/`, lite POS `src/app/pos/lite/` + `src/app/api/pos-lite/`; dookana is sunset, NOT used). **Worktree:** `~/wingmen/ihsanos-wt/xendit-pos` (branch `feat/xendit-pos-integration`, off origin/main f623979).

## Mission
Wire **Xendit** (TEST mode) as the real payment gateway for POS, replacing the PayNow-screenshot-OCR confirm step, and make the **ENTIRE POS flow flawless e2e on BOTH the full POS AND the lite POS**. AUTHORED build only — do NOT deploy-live, do NOT merge, do NOT push to main. Money rail → hub reviews + cc-reviewer money-path + gated.

## Credentials (read by NAME — never print the secret)
`XENDIT_SECRET_KEY` + `XENDIT_WEBHOOK_TOKEN` are already set in the ihsanos Vercel project env (encrypted, all targets). Read via `process.env` by name. For local test runs, read from the ihsanos env; do not hardcode or echo. TEST-mode keys only.

## Scope
1. **Xendit client module** (e.g. `src/modules/payments/xendit/`): create charge / QR, region-routed — **PayNow (SG, SGD)** and **QRIS (Indonesia, IDR)** by region/currency. TEST mode. Typed, no secret in logs, timeouts + error handling.
2. **Payment flow:** POS sale → create Xendit charge/QR (PayNow or QRIS) → present QR/instructions to the customer → customer pays (test) → **Xendit webhook** → mark the `pos_transaction` paid. This REPLACES the PayNow-screenshot-OCR step (keep OCR path intact/behind a flag unless it cleanly retires; don't break existing tenders incl. cash/paynow/nets/cheque).
3. **Webhook endpoint:** there's a 501 placeholder at `src/app/api/storefront/payment-webhook/route.ts` ("validates signature → confirmPosOrderPaymentCore") — build the real Xendit callback here or a dedicated `/api/xendit/webhook`. MUST verify authenticity with `XENDIT_WEBHOOK_TOKEN` (Xendit's `x-callback-token` header), be idempotent (same event twice → one paid transition), replay-safe, and fail-closed on bad/missing token. **REPORT the webhook URL back** so Nazim hands it to the operator to paste into the Xendit dashboard.
4. **Wire BOTH variants** (operator explicitly wants both flawless):
   - **Full POS:** `src/app/dashboard/pos/payment-dialog.tsx` + the POS transaction action/`src/modules/pos/api.ts`.
   - **Lite POS:** `src/app/pos/lite/page.tsx` + `src/app/api/pos-lite/transaction/route.ts`.
5. **Money invariants:** a Xendit-paid sale records the tender correctly (does not corrupt cash-drawer reconciliation / `expectedCash`); paid state is set ONLY by a verified webhook, never client-asserted. Idempotent on the `pos_transaction`.

## ⚠️ Coordinate with WP-C (imminent main change)
WP-C (NETS/cheque tenders) is merging to main NOW — it touches `payment-dialog.tsx`, `pos.ts`, `src/modules/pos/api.ts` (adds `nets`/`cheque` payment methods + `payment_reference`). Keep your Xendit payment-method additions **modular** to minimize conflict. Once WP-C lands, the hub will signal you to **rebase onto the updated main** so you build on top of the nets/cheque changes. Build against current main meanwhile; don't fight it.

## Proof / test (Xendit TEST mode — no real money)
- Unit: the Xendit client (charge/QR creation, region routing), webhook verification (valid token passes, bad/missing token 401, replay → single paid transition).
- **ENTIRE POS FLOW e2e on BOTH variants**: sale → add items → payment (Xendit PayNow AND QRIS) → webhook confirms → transaction paid → confirm → receipt. Drive against Xendit TEST mode. Prove the full flow flawless for full POS and lite POS separately.
- Regression: cash/paynow/nets/cheque tenders still work; cash-drawer reconciliation unaffected.
- tsc 0, lint 0 (change-relevant), `next build` green. Paste REAL output.
- Any new migration = money-adjacent → register known-unapplied, do NOT apply.

## Governance (money rail)
TEST-mode build proceeds now. The **LIVE switch** (real key + real payments) is GATED — do NOT flip: cai review + residency sign-off (**SG for PayNow + Indonesia/QRIS** — flag the ID residency question explicitly) + operator KYC. When test-verified, REPORT ready-for-live-gate + exactly what the live switch needs.

## Report back to cc-orchestrator
Branch + SHA; files changed; the **webhook URL**; the e2e proof output for BOTH POS variants; any new migration (unapplied); the residency/live-gate flags. Hub runs cc-reviewer money-path + eyeball + re-runs your tests before anything ships. Trust-nothing.
