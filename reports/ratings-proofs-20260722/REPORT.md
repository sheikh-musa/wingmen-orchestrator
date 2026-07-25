# Storefront REAL customer ratings — build + verification report (2026-07-22)

**Lane:** cc-ihsanos-ratings · **Branch:** `feat/storefront-real-ratings` · **SHA:** `b2460ac`
**Worktree:** `~/wingmen/ihsanos-wt/ratings` (off origin/main, not colliding with cc-ihsanos-store)
**Spec:** `reports/storefront-real-ratings-spec-20260722.md` · **Gate:** design + reviewer (security/privacy). NOT self-deployed.

## Identity binding (spec §B) — hub-ruled, both accepted
- **TG orders:** HMAC-verified Telegram `initData` → `pos_orders.telegram_user_id` (mig 112 / CAI-RESP-494 §6.6). Strongest, already-trusted; server-authoritative.
- **web/cash orders:** email + order_number challenge — the SAME bar `getReturningCustomer` already trusts to release saved PII (a review is lower-stakes).
- `reviewer_ref` = opaque token (`tg:<id>` | `email:<sha256(lower)>`), no raw PII. No weaker binding invented.

## Rate-limit answer (hub asked)
`getReturningCustomer` has **no** throttle. Added a dedicated per-IP fixed-window limiter (`review-rate-limit.ts`, **5/min/IP**) on the audited `ip-rate-limit` primitive; the IP gate runs FIRST in `/api/storefront/submit-review`, before any DB touch — bounds brute-forcing order_number+email combos and plain floods.

## Deliverables
- **A — Schema** `supabase/migrations/118_storefront_reviews.sql` (additive/nullable/idempotent): `storefront_reviews` (UNIQUE order_id), PII-free `storefront_store_rating` aggregate view, RLS on, `REVOKE ALL` from anon+authenticated. Apply is HUB-OWNED (`--expect-ref` psycopg, synthetic-first; ceayj + goumlyne parity per hub policy).
- **B — Submit path** `submit-review.ts` (service_role only) + `/api/storefront/submit-review` route + `review-rate-limit.ts` + customer `rate-order-form.tsx` on the completed-order page.
- **C — Honest display** `store-rating-badge.tsx` (0 reviews → "New", never fabricated) + fail-closed `store-rating.ts` read, wired into `getStorefrontData` + `/shop` header. Merchant testimonials remain a separate labelled block.

## Bug caught by driving it
The DB submit-gate simulation caught a real defect: `pos_orders` has **no** `deleted_at` column, so `submitReviewCore`'s `.is("deleted_at", null)` would 42703 → NOT_FOUND on **every** submit (gate denies all real customers). Fixed in `b2460ac`.

## Verification (see `deny-proofs.txt`)
- **DB proofs: 22/22** (apply-in-txn → ROLLBACK, nothing committed). Grants (SET ROLE anon → permission denied), UNIQUE one-per-order (23505), stars/comment CHECKs, empty→"New", anon reads only the PII-free aggregate, soft-delete excluded, + submit-gate simulation (non-customer/non-completed/wrong-email/wrong-order# all deny).
- **TS gate unit tests: 14/14** — drive the real `submitReviewCore`: happy TG+web + EVERY denial (non-customer→NOT_FOUND, non-completed→FORBIDDEN, 2nd review→CONFLICT, unbound TG, invalid session, wrong email/order#, bad stars/comment).
- **Rate-limit tests: 4/4.** Full storefront suite: **419 passed.** `tsc` + `next build` + eslint green.

## Screenshots (real `/shop`, synthetic store `qa-shop-test`)
- `new-mobile.png` / `new-desktop.png` — empty state: **"New" · No ratings yet** (no fabricated stars).
- `rated-mobile.png` / `rated-desktop.png` — rated state: **★★★★★ 4.8 (128 reviews)** (temp stub, reverted before commit).

## Awaiting hub
Live single-process e2e (real POST to the deployed route → row persists → aggregate updates) needs **118 applied** (hub-owned). `prove-118 --no-apply` is ready to re-verify against the live objects post-apply.
