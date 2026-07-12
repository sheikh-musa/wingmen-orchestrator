# goumlyne↔ceayj schema-parity sweep — 2026-07-12 (task #49)

Genesis: 092/goumlyne near-miss (pos_orders missing 3 payment cols). Operator asked to sweep for "plenty more." **There are.**

**Verdict: goumlyne is materially drifted — running the OLD pre-hardening storefront/donation model.** Both silos reachable, read-only introspection. ceayj = reference (86 tables/43 fns); goumlyne = 83 tables/37 fns; 74 shared.

## CRITICAL (money/PII — the 092 class), 7 findings
1. **pos_orders — 6 cols missing on goumlyne** + idempotency unique index. Missing: `payment_idempotency_key`(text,null — double-charge guard), `payment_external_ref`(text,null), `payment_confirmed_at`(tstz,null), `currency`(text,**NOT NULL** — structural), `session_id`(uuid,null), `surface_key`(text,null). Missing: `UNIQUE idx_pos_orders_payment_idempotency_key ON (org_id,payment_idempotency_key) WHERE key IS NOT NULL`. → 5 additive-safe, `currency` structural (default/backfill).
2. **pos_orders/pos_order_items — SECURITY drift (open anon write on goumlyne).** goumlyne-only policies "Anyone can place orders"/"Anyone can insert order items" (INSERT WITH CHECK(true)) + anon AND authenticated hold INSERT/UPDATE/DELETE/TRUNCATE. ceayj revoked these + replaced with SECDEF RPC `place_storefront_order`. **This is the live D2 hole, confirmed open on goumlyne.** Structural (needs RPC + grant/policy rework, i.e. 092-class + the RPC).
3. **donations — 8 cols missing on goumlyne** + 3 indexes. Missing: `status`(text,**NOT NULL** — structural), `campaign_id`,`donation_product_id`,`fundraiser_page_id`,`parent_pledge_id`(uuid,null), `applicable_period_start/end`(date,null), `delivery`(jsonb,null). Missing idx: status_org, campaign, parent_pledge. → **Elly's donation surface.**
4. **donation_categories — column RENAME divergence.** ceayj `_deprecated_fund_raised/_deprecated_fund_target`; goumlyne `fund_raised/fund_target`. Shared code referencing either breaks on one silo. Structural (rename).
5. **organizations.fee_model** (text, NOT NULL) missing on goumlyne. Pairs w/ ceayj-only `calculate_platform_fee_for_org` SECDEF + `organization_fee_overrides` table. Structural.
6. **pos_products.currency** (text, NOT NULL) missing on goumlyne. Structural.
7. **Donation-module policies/indexes absent on goumlyne.** campaigns 4→1, donation_products 2→1, donor_tier 4→1, fundraiser_pages 2→**0** (unpolicied). Missing indexes on campaigns/donation_products/donor_tier/fundraiser_pages/persons(org_id,email). Authorization drift on donation surface.

## NOTABLE
- **hr_claims** — goumlyne missing `receipt_path`,`reimbursed_at`,`reimbursement_reference` (all additive-safe). Mizuho HR surface.

## INFO — likely intentional module scoping (confirm, don't assume)
- ceayj-only tables (12): storefront/telegram/consent/platform (telegram_users, tg_*, pos_order_counters, organization_fee_overrides, client_contracts, donor_consent, donor_invites, parent_consents, newsletter_subscriptions, academic_terms).
- **goumlyne-only tables (9): GL + WooCommerce-ingest module** (gl_accounts/entries/periods/transactions, organizations_fiscal_config, wc_ingest_*, wc_order_ingest*). Deliberate irsyad-only module (fns enforce_balanced_journal, post_journal_atomic).
- ceayj-only fns (9) incl place_storefront_order, provision_tg_merchant_org, calculate_platform_fee_for_org (all SECDEF). Their absence is WHY goumlyne still runs the open-write model.

## Counts
CRITICAL 7 (4 additive-safe cols; rest structural: 4 NOT-NULL cols, 1 rename, 1 RPC+grant rework). NOTABLE 1 (safe). INFO: 12 ceayj-only + 9 goumlyne-only tables, 9+3 fn diffs (module scoping).

## Implication for Elly go-live
Her donation+POS grant sits on a drifted silo. The narrow 093 (3 pos_orders cols) is INSUFFICIENT — must scope by schema-vs-code contract (what the donation+POS app paths actually require) and bring goumlyne's donation+POS surfaces to functional parity + close the anon-write hole, BEFORE her grant. Some ceayj drift is moot (modules irsyad doesn't run).
