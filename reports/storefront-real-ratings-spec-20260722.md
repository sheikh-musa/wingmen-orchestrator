# Storefront REAL customer ratings — build spec (2026-07-22)

**Repo:** `~/wingmen/projects/ihsanos` (storefront lives here per IHSANOS-STOREFRONT-TG-001). **From:** cc-orchestrator (hub), operator op#6097/6106 (full tilt). **Owner:** a fresh isolated storefront lane (worktree off origin/main — cc-ihsanos-store is on the shared checkout; do NOT collide). **Gate:** design + reviewer (security/privacy dimension) before merge. Do NOT self-deploy.

## Problem (from the code-grounded gap map)
Today's storefront "stars" are **merchant-authored testimonials** (`src/modules/storefront/components/testimonials-section.tsx`, seeded in `src/app/dashboard/storefront/page.tsx:184` as `{name,text,rating:5}`) — marketing copy the merchant types, NOT customer ratings. There is **no** reviews table, no submit path, no aggregate. We want REAL customer store ratings, honestly gated and never fabricated.

## Design principles (ihsan bar)
- **Honest by construction:** a store with 0 real reviews shows "New" — never a fabricated star average, never reuse the merchant testimonial stars as if they were customer ratings. Aggregate + count are the real signal.
- **Integrity-gated:** a rating can only be left by the actual customer who placed an order that reached `completed`, exactly once per order. No anon spam, no self-review by the merchant, no rating an order you didn't place.
- **Privacy:** reviews are lightly-personal. Public projection exposes stars + optional comment + a coarse display name at most — never email/phone/person PII. Verify GRANTS, not just RLS ([[pii-table-verify-grants-not-just-rls]]): REVOKE anon on the base table; expose only a safe aggregate/projection.
- **Future-shaped:** schema keyed so it extends to per-product reviews later without a rewrite, but build store-level now (no speculative gold-plating).

## Seams (verified in the gap map)
- Completion signal: `pos_orders.status='completed'` (enum in `supabase/migrations/012_storefront_ecommerce.sql:43`; transitions `src/actions/orders.ts:50`, `ready→completed`, merchant-set). There is NO `delivered` state — `completed` is terminal success.
- Customer↔order identity: `pos_orders.customer_email`; returning-customer via `storefront_customers` (`096`, `person_id` + `saved_phone_hash`); customer-side proof today = email challenge (`src/actions/storefront-orders.ts:300-322 getReturningCustomer`). Also `pos_orders.public_id` (096) for deep-link identity.
- Display surface to reuse: `testimonials-section.tsx` / `section-renderer.tsx:114`.

## Deliverables

### A. Schema (additive migration, guarded apply — NEVER `supabase db push`; use the `--expect-ref` psycopg path)
- `storefront_reviews`: `id`, `org_id` (FK organizations), `order_id` (FK pos_orders) + `order_public_id`, `reviewer_ref` (the customer identity used to gate — see B), `stars smallint CHECK (stars BETWEEN 1 AND 5)`, `comment text NULL` (length-capped), `created_at timestamptz default now()`.
- **UNIQUE (order_id)** — one review per order (the anti-spam/one-per-purchase invariant).
- RLS ON. Policies: INSERT only via the server path proving completed-order ownership (see B; realistically INSERT is `service_role`/SECDEF-only and the action enforces the check — no broad client INSERT). SELECT of individual rows: none to anon; the PUBLIC read is the AGGREGATE only.
- **Aggregate:** a `storefront_store_rating` view (or SECDEF function) returning `org_id, avg_stars, review_count` — the ONLY public-facing rating surface. No PII in it.
- GRANTS: `REVOKE ALL ON storefront_reviews FROM anon, authenticated` (writes go through the action/RPC); grant SELECT on the aggregate view to the read role the storefront uses. Confirm `SET ROLE anon` → 0 rows on the base table.

### B. Submit path (server action / SECDEF RPC) — the integrity core
- Input: order identity (public_id) + stars + optional comment + the caller's customer proof.
- Verify ALL, server-side, atomically: (1) the order exists and `status='completed'`; (2) the caller IS the order's customer — reuse the EXISTING returning-customer identity path (`getReturningCustomer` / `storefront_customers.person_id` / email challenge), do NOT invent a weaker one; (3) no existing review for this order (the UNIQUE handles the race — catch + friendly error). 
- **DESIGN DECISION to confirm before building** (flag to hub): the customer↔order binding — (a) the email-challenge returning-customer path, or (b) TG Mini-App `initData` identity (HMAC-verified) if the order carries a TG identity, or (c) `person_id`. Pick the strongest available for the actual order's identity; if orders don't reliably carry a verifiable customer identity, STOP and flag — a rating gate is only as good as the identity binding. Prefer the same mechanism the order flow already trusts.
- Money/PII posture: this is not money, but treat the identity check with the same rigor — no client-supplied "I am the customer" trust.

### C. Display
- A real ratings component: aggregate stars + `(N reviews)`; when `review_count=0` → "New" (no stars filled as if rated). Optionally list recent comments (no reviewer PII — coarse name or "Verified customer").
- Keep merchant testimonials as a SEPARATE, clearly-labelled marketing block (don't conflate). The marketplace StoreCard (when built) consumes the aggregate, not testimonials.

## Verification (honesty gate — drive the REAL flow, [[test-end-to-end-before-declaring-live]])
- Drive it e2e on a non-prod/synthetic target: place order → mark completed → the real customer submits a rating → aggregate updates → displays; a NON-customer / non-completed-order submit is DENIED; a second review on the same order is DENIED. Empty state shows "New". Screenshots mobile+desktop.
- `next build` + tsc + tests green. Report branch/SHA + the deny-proofs + screenshots to hub for the design+security review gate. Migration additive/nullable; flag the identity-binding decision.

## Out of scope (later)
Per-product reviews; review moderation/reporting; merchant replies; marketplace StoreCard wiring (consumes the aggregate when discovery ships).
