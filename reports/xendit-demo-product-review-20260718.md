# IhsanOS POS — product review (op#5187): flows, friction, POS-lite vs full, Xendit reqs

**By:** cc-ihsanos (xendit-pos lane) · 2026-07-18 · demo DB brrgastul (synthetic) · mobile-first (430px) captures via system Chrome.
**Screenshots:** `logs/tg_media/` (worktree) + copied to `reports/xendit-demo-flows-20260718/` (17 PNGs, hub-durable). Prefix maps to the flow: a=onboarding, b=add-product, c=session, d=sale, e=receipt, f=lite.

---

## 1. Captured flows (each step → screenshot)

### (a) Merchant onboarding / signup — **SELF-SERVE EXISTS, 3 steps** ✅
Route `/onboarding` (`src/app/onboarding/page.tsx`). Steps: signup → create-org → welcome (stepper 1-2-3).
- `a1-onboard-signup` — Step 1: email + password + confirm → **Create Account**.
- `a3-signup-invalid-email-friction` — Supabase **rejects the `.test` email domain** ("Email address … is invalid"). Friction (see §2).
- `a4-onboard-createorg` — Step 2: Organization Name*, UEN (optional), org-type pills (Community org / Education / Non-profit / Business / Startup / Government), auto **URL slug** (`ihsanos.com/shop/your-org`) → **Create Organization**.
- `a5-onboard-createorg-filled` — filled ("Demo Merchant (Synthetic)", Business).
- `a6-onboard-welcome` — Step 3: **"You're All Set!"** — shows Org / URL / Tags / Role=Admin + CTAs: Go to Dashboard, Invite Team Members, **Set Up Point of Sale**, Add Employees.
- **POS is auto-enabled** for a new org (default module config `pos:true` + org_admin gets `pos:"full"`). **BUT** onboarding does NOT create a POS-lite outlet or any product — the merchant lands on an empty catalog.
- **Click cost:** ~type 3 fields + click (signup) → type name + tap 1 pill + click (create-org) → 1 click to dashboard ≈ **3 primary clicks + 2 short forms**.

### (b) Add product ✅
`b1-products-list` (Products page, "Add Product") → `b2-product-add-form` (Product name*, Price SGD*, Category*, SKU optional, Description optional → **Create product**). ~2 clicks + a short form.

### (c) Open POS session ✅
`c3-pos-counter` — POS counter with a session open ("Session opened …"), category tabs + product grid. Opening a session = "Open POS Session" → **opening float** → confirm (`open-session-dialog.tsx`); float is required before selling (cash-drawer discipline).

### (d) PayNow sale — **both paths captured** ✅
`d1-cart-item` (tap product → cart badge) → `d2-cart-review` (mobile slide-up cart) → `d3-payment-methods` (Cash / PayNow / NETS / Cheque / Split / **QR Pay**). Two PayNow rails:
- `d4-paynow-qr` — **static PayNow (SGQR)**: shows **"PayNow not configured. Set your organization UEN in Settings"** because the org has no UEN. Cashier can still **Confirm Payment** manually → sale completes.
- `d5-xendit-paynow-qr` — **Xendit QR Pay → PayNow**: a real **Xendit dynamic QR** renders ("Scan to pay S$25.00 via PayNow", "Waiting for payment confirmation…", honest "Paid status is set only by Xendit, never here"). No UEN needed. **This is the Xendit-integrated rail and it works** (Xendit `/qr_codes` SGD → 201).

### (e) Receipt ✅
`e1-sale-complete` — **"Payment Complete ✓ S$25.00 · Transaction #3 completed"** with **Print Receipt** (thermal via browser print) + Done. End-to-end sale confirmed.

### (f) POS-lite token counter ✅
`f1-lite-counter` — `/pos/lite?t=<token>` header "Latiff Counter (Demo) YSH294", product grid, **no login**. `f2-lite-cart` (item added) → `f3-lite-pay` ("Amount due $25.00" → Cash / PayNow / PayWave / CDC Voucher / PayNow (QR) / QRIS). **3 taps to a sale** (product → Pay → method). Token in URL = the credential.

---

## 2. Friction report (per step + fixes)

| Step | Clicks | Rough edge | Frictionless fix |
|---|---|---|---|
| Signup | 3 fields + 1 | Supabase **rejects `.test` / some domains** with a generic "invalid" error; email-confirm may be required on some projects (blocks auto-signin). | Validate email client-side with a friendlier message; ensure the project's auth allows the demo domain / disables confirm for the trusted onboarding flow. |
| Create-org | name + 1 pill + 1 | "Select all that apply" org-type is fine; slug auto-gen is nice. | Minor: prefill a sensible default tag. |
| **Post-onboarding** | — | **Merchant lands with NO products and NO POS-lite outlet.** Can't sell immediately. Token-minting for lite has **no obvious dashboard UI** (it's `POST /api/pos-lite/sessions`, admin-only). | **Auto-provision** a POS-lite outlet + a sample product on org creation; add a **"Create counter link / QR"** button in the dashboard that mints + shows the lite token. |
| Add product | 2 + form | **Category is free-text** ("e.g. Beef") — typo → duplicate categories. | Category = combobox of existing categories + "create new". |
| Open session | 3 | Opening **float required** before first sale. | Keep for cash, but allow a **"card/QR-only, skip float"** fast start. |
| Sale (static PayNow) | ~5 | **"PayNow not configured"** until org UEN is set in Settings — easy dead-end for a new merchant. | Prompt/guide UEN setup during onboarding, or hide static PayNow until UEN present; steer to Xendit QR Pay (no UEN). |
| Sale (Xendit QR Pay) | ~6 | Clean + honest. QRIS currently errors (Xendit channel not activated — §4). | Activate the Xendit channel (operator-side). |
| Receipt | 1 | Thermal print via browser API. | OK. |
| Lite | 3 taps | Excellent zero-friction. Lite shows PayNow(QR)/QRIS rails that require Xendit channel activation to work. | Gate lite Xendit rails behind activation status. |

---

## 3. POS-Lite vs Full POS (code-grounded)

| Dimension | **Full POS** (`/dashboard/pos`) | **POS-Lite** (`/pos/lite?t=…`) |
|---|---|---|
| **Auth** | Supabase login + `org_members` + role gate (org_admin/cashier/preparer) + `requireModule("pos")` (`page.tsx:12-41`). | **No login.** Public route; **token-in-URL = credential** (`supabase-middleware.ts:104`). SHA-256 token hash in `outlet_sessions` (`token.ts`, `033_pos_lite.sql`). |
| **Who provisions** | Cashier logs in. | Admin mints a token via `POST /api/pos-lite/sessions` (org_admin only, TTL 7d default/30d max, plaintext shown once); rotate = mint+revoke (`revoke/route.ts`). |
| **Features** | Sessions w/ opening float + close/reconcile, cash/paynow/nets/cheque/split/QR-Pay, discounts, void, misc items, quick donation, inventory, transaction history, receipts. | Product grid + cart (qty +/− only), subset tenders, server-recomputed total, human order no. `[OUTLET]-[YYYYMMDD]-[NNN]`, idempotency + 200/hr rate limit, WhatsApp receipt. **No** float/close, discounts, void, history, inventory mgmt. |
| **Intended use** | Staffed counter w/ drawer reconciliation. | **Zero-friction volunteer/bucket-shop counter** — scan a QR, no account (pilot: Latiff & Sons, `033_pos_lite.sql:1-2`). |
| **Data** | `pos_transactions` `pos_tier` default, `session_id`=cashier session, `created_by`=user. | Same table, `pos_tier='lite'`, `session_id=null`, `outlet_session_id` set, `created_by=null`, `idempotency_key` req. CHECK: `created_by IS NOT NULL OR outlet_session_id IS NOT NULL`. |

**Why a merchant uses each:** Full = a shop with staff accounts + cash drawers + full back-office. Lite = a pop-up/bucket/volunteer counter where handing someone a login is overkill — you hand them a QR link instead.

---

## 4. Xendit merchant payment requirements (what a merchant must have/do)

To actually RECEIVE money via Xendit — none of which the ihsanos app can do for the merchant:
1. **Xendit business account.**
2. **KYC / business verification** — required for **LIVE**; **TEST** mode works without full KYC.
3. **Per-channel activation** — EACH payment channel must be activated in the Xendit dashboard. **We hit this live:** a QRIS (IDR) charge returns **`403 CHANNEL_NOT_ACTIVATED`** on the test account; **PayNow/SGQR (SGD) is activated and works (201).** So even in TEST, each rail (PayNow, QRIS, cards…) is a separate dashboard toggle / CS request.
4. **API keys** — `XENDIT_SECRET_KEY`; **TEST = `xnd_development_…`**, LIVE = `xnd_production_…`. Our app's key-guard refuses a non-test key unless `XENDIT_MODE=live`.
5. **Webhook callback URL (dashboard-configured)** — **our app sends NO per-charge `callback_url`**, so Xendit uses the **dashboard's** QR-payment callback URL. The merchant MUST set it to `‹app›/api/xendit/webhook` **and** set the **verification token** (= our `XENDIT_WEBHOOK_TOKEN`). If unset, our webhook **fail-closes 401** and **payments never confirm** (sale sticks on pending).
6. **TEST vs LIVE** — LIVE additionally needs a **settlement bank account** (where Xendit deposits) + completed KYC. TEST = simulate payments, no settlement.

**Implication for "frictionless onboarding":** the *in-app* signup→org→sell path is short (§1), but **receiving Xendit payments is gated entirely on Xendit-side merchant setup** (account, KYC, per-channel activation, dashboard webhook + token, keys). A future "connect Xendit" onboarding step could guide keys + webhook, but channel activation + KYC remain on Xendit.

---

## Notes
- Demo integrity intact: PayNow (SGD) via Xendit works live; QRIS waits on the operator activating the Xendit QRIS channel (reported #9698).
- Synthetic artifacts added for this review (teardown-tracked): auth users `admin@bapa.test`, `cashier@bapa.test`, `demo.merchant.5187@gmail.com` (+ failed `.test` signup rows), org "Demo Merchant (Synthetic)", any "Demo Kopi O" product. All in ihsanos tables / auth.users — dropped at teardown.
