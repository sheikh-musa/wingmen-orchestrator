# Schema-vs-code contract — goumlyne donation+POS required-set (2026-07-12)

Independent read-only code analysis (ihsanos repo), cross-checking the parity sweep. Controlling rule: a PostgREST call breaks only when a missing column is NAMED explicitly (insert/update payload, select column-list, or filter). Bare `.select("*")` over a table missing columns does NOT break.

## MUST-ADD to goumlyne (code names it → breaks without it) — ALL ADDITIVE-NULLABLE/SAFE
- **pos_orders.surface_key** (text, null) — HARD. In every order-insert payload (place-order.ts:499) + filtered (orders.ts:118,122). Every order placement fails on goumlyne without it.
- **pos_orders.payment_idempotency_key, payment_external_ref, payment_confirmed_at** (null) — PayNow confirm/claim/screenshot-verify (payment-confirm.ts:49-51, storefront-claim.ts:89, paynow-verify.ts:130,133). Money-path, wired → ADD (safe additive; harmless if unused).
- **idx_pos_orders_payment_idempotency_key** UNIQUE (org_id, payment_idempotency_key) WHERE key IS NOT NULL — double-charge backstop the code assumes. Safe (partial).
- **pos_products.currency** (NOT NULL) — CONDITIONAL: Telegram Mini App product admin only (miniapp-products.ts:131,155). Add with DEFAULT 'SGD' (safe) to cover it.

## DO NOT TOUCH — would BREAK code
- **donation_categories rename**: code uses `fund_raised`/`fund_target` (goumlyne's current names), never `_deprecated_*` (donations/api.ts:104 writes fund_raised). Applying ceayj's rename to goumlyne breaks donations. KEEP goumlyne's names.

## MOOT — drop from scope (code never names them)
- pos_orders.currency (insert omits it; ceayj must have a DEFAULT), pos_orders.session_id (never touched).
- donations.status + all 7 feature cols (campaign_id/donation_product_id/fundraiser_page_id/parent_pledge_id/applicable_period_start-end/delivery) — createDonation doesn't set them; unimplemented campaign/fundraiser/pledge feature.
- campaigns/donation_products/donor_tier/fundraiser_pages policies+indexes — code NEVER queries these tables.
- organizations.fee_model, organization_fee_overrides, calculate_platform_fee_for_org — never referenced; ceayj platform feature.
- pos_order_items columns — only base cols written.

## SECURITY (separate from code contract)
- App writes pos_orders/pos_order_items via **service-role** (place-order.ts:174 createServiceClient, RLS-bypass) — NOT via `place_storefront_order` RPC (which the code never calls; it doesn't need porting). BUT authenticated write paths exist (payment-confirm, order-status). So closing goumlyne's anon-write hole = the SAME **092 Option B** (drop Anyone-can policies, revoke anon writes, keep column-scoped authenticated UPDATE) — applied AFTER the pos_orders columns exist. Safe (service-role + column-scoped authenticated unaffected).

## FOLLOW-UP FLAG (ceayj-side, not goumlyne/Monday)
- Code uses `fund_raised`/`fund_target` but the sweep says ceayj renamed them to `_deprecated_*`. If that rename is truly live on ceayj, the donation write path would be BROKEN ON PROD (ceayj) — unless ceayj has a compat view/retained column. Investigate: is ceayj's rename real, or a sweep misread? Potential prod issue.

## Net remediation for goumlyne (Monday)
1. Additive migration: pos_orders + surface_key, payment_idempotency_key, payment_external_ref, payment_confirmed_at (all nullable) + the idempotency unique index; pos_products + currency DEFAULT 'SGD'. ALL SAFE additive — no risky NOT-NULL backfills, no donation migration, no rename.
2. Then 092 Option B (already reviewed) to close the anon-write hole.
3. Then Elly's POS+donation grant → E2E verify.
Dramatically smaller + lower-risk than the raw sweep suggested.
