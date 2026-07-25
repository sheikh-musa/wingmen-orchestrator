# Xendit POS — call-ready build RESULT (reply to brief 20260717)

**From:** cc-ihsanos (xendit-pos lane), worktree `~/wingmen/ihsanos-wt/xendit-pos`
**Branch:** `feat/xendit-pos-integration`
**Rebased feature SHA:** `07516f3` (was `40ba3dd`) — linear, ahead 1 of `origin/main@14f5684`
**Status:** REBASED + BUILD-GREEN + UNIT/INTEGRATION-GREEN. Not pushed, not deployed (per guardrails). Ready for hub gate.

---

## STEP 1 — Rebase result
- Rebased `feat/xendit-pos-integration` onto `origin/main@14f5684`. Merge base was `f623979`.
- **One conflict, resolved:** `src/app/dashboard/pos/payment-dialog.tsx` — import line + `PaymentMethod` union. Both WP-C (nets/cheque) and Xendit (xendit) extend the same POS payment surface. Resolved to the **union**: `PaymentMethod = cash|paynow|split|nets|cheque|xendit`, imports merged (`CreditCard, FileText, QrCode`), kept `XenditRail`. The `METHODS` array and the nets/cheque + xendit render bodies auto-merged cleanly and both are present.
- The other 4 touched files (`pos.ts`, `modules/pos/api.ts`, `shared/lib/schemas.ts`, `migration-tracker-baseline.json`) auto-merged with no conflict.

## STEP 1b — Migration 071 collision: **DID NOT EXIST**
- The brief anticipated this branch carrying its own `071_pos_tender_nets_cheque.sql`. **It does not.** `git ls-tree 40ba3dd` shows the branch's commit adds only migration **108** — no 071. So there was never a duplicate 071 to drop.
- Resolution: **adopted main's 071 as-is** (WP-C #170, the CAI-RESP-466 tracked idempotent guarded re-assert). No redefinition of any applied object.
- Verified 071 + 108 coexist with no dupes; POS tender code paths compile against main's 071 (type-check + build green).

## STEP 2 — Migration numbering (as coordinated)
- **108 = `108_pos_xendit_gateway_payment.sql`** — kept at 108. `_reserved.txt` records 108 = Xendit gateway.
- **No second migration needed** — the single conflict was code-only. Did NOT touch 105 / 109 / 110.
- **Order-safety verified:** 071 re-asserts the `payment_method` CHECK to EXACTLY 7 values (with a post-condition RAISE guard) when it runs; 108 then re-asserts the full canonical **9-value** set (`+ xendit_paynow, xendit_qris`). Sequence 071→108 is consistent; 108 also carries the full set so it's correct even if 071 hasn't run. Both remain **AUTHORED-UNAPPLIED / money-gated**.

## STEP 3 — Build + test (post-rebase)
| Gate | Result |
|------|--------|
| `next build` (real production build) | **PASS** — compiled in 7.5s, 110/110 static pages generated. `/api/xendit/webhook` route present. (`/dashboard` cookies warnings are pre-existing benign dynamic-render fallbacks.) |
| `tsc --noEmit` | **PASS** |
| `vitest run` (full) | 1541 pass / **18 fail** — the 18 are ALL in `use-theme.test.ts` + `product-tour.test.tsx`, a **pre-existing jsdom `localStorage`-undefined env issue**. Files are **byte-identical to origin/main** and fail the same way on a clean main tree. **Unrelated to Xendit.** |
| Xendit suite (client/confirm/webhook/region/pos-lite) | **41/41 PASS** |
| No absolute cross-repo paths in shipped `src/` | Clean (grep) |

### e2e — NEEDS THE HUB'S TEST/PREVIEW DB (not runnable in this lane)
- There is **no Playwright spec named for xendit/qris**. Xendit's end-to-end coverage is the vitest integration suite (webhook signature verify, confirm lifecycle, region routing) — all green.
- The Playwright POS specs (`pos.spec.ts` full, `pos-lite-counter.spec.ts` lite) depend on **seeded `.test` accounts + org fixtures** and **create POS sessions/transactions**. The only Supabase creds in this shell are **live ceayj PROD** — running the mutating full-POS spec there would write test data to a live money DB (guardrail violation). No separate seeded staging Supabase is available here.
- **→ e2e is correctly coupled to the hub's TEST-mode demo deploy env** (the seeded preview DB the demo stands up IS what the e2e needs). Please run `pos.spec.ts` + `pos-lite-counter.spec.ts` there after the 108/071 apply.

### UI self-check (mobile-first / ihsan bar) — PASS
- Full-POS `payment-dialog.tsx` "QR Pay" → 2-col rail select (PayNow · SG/SGD, QRIS · ID/IDR), 72px touch buttons, 240px QR, honest copy: *"Paid status is set only by Xendit, never here."* Waiting indicator while polling.
- Lite `pos/lite/page.tsx` QrWaitScreen: bilingual "Imbas untuk bayar · Scan to pay", auto-updates on webhook.
- Both render **friendly rail labels** ("PayNow"/"QRIS" from `region.rail`, not raw enums), logical properties (`ps/pe/start`), 48px+ targets throughout.
- **One money-path note for cc-reviewer (NOT fixed — out of display scope):** the QRIS rail displays the SGD total (`S$…`) while the Xendit charge is created in `region.currency` = **IDR**. Currency-shown vs charge-currency is a finance-dimension question, not a cosmetic one — flagging for the reviewer's money-path pass rather than silently editing it.

## STEP 4 — Handoff (I did NOT push or deploy)
Hub owns from here:
1. **cc-reviewer** — money-path correctness + security (webhook auth, pending→paid transition, single-tender reconciliation, QRIS currency-display note above) + POS UI design/mobile.
2. **Verify the production build** (done here: PASS) and **run Playwright** `pos.spec.ts` + `pos-lite-counter.spec.ts` on the seeded TEST DB.
3. **Apply migration 108 (after 071) to a TEST/preview DB ONLY** — never live goumlyne/ceayj. Confirm the demo DB target before applying. `scripts/db/apply-migration-psycopg.py --expect-ref <TEST_REF>`, ceayj-first is for PROD lanes — for the demo use the preview ref.
4. Deploy TEST-mode demo → stable URL + webhook URL for the operator's MON 07-20 call.

**Intended TEST-mode deploy target:** a preview/TEST Supabase + Vercel preview — hub to confirm the specific ref. TEST keys only; no live settlement.
