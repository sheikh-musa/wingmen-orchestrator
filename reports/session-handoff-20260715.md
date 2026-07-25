# Orchestrator session handoff — 2026-07-15 ~06:20Z

Written because a long session may be dulling judgment (two "assert without verifying" errors: understated a client wait as "20min" vs actual 2h22m; earlier "80% built" over-read). Everything below is also in the task list (#1–84), memory files, and the `operator_messages` durable log. Standing doctrine unchanged: money/PII/residency/definer steps stay cai- or flag-gated; lanes build authored-unapplied, hub reviews + gates apply; never `supabase db push` vs prod (direct-psycopg --expect-ref); irsyad-first smoke on shared deploys.

## IN FLIGHT (needs pickup) — TOP PRIORITY, promised Elly "live today"
- **Empty-tin fix — BUILT, ready to deploy (task #84, `feat/tabung-nil-return` @398f310, pushed, NOT merged).** Elly's P1 blocker (can't record an empty S$0 tabung return). Lane finding: **NO migration needed** — a $0 tin is already representable (mig 056 requires amounts NOT NULL, not >0); distinguished by status `counted`+`counted_at`. Code-only: client adds a "Tin returned empty (S$0)" checkbox → `is_nil_return`; server (`recordCountAction`) now REJECTS accidental all-zero blanks it previously accepted (a tightening) and accepts a deliberate nil normalized to all-zeros itemisation (reconciles, invariant #61 holds). 43/43 tests, tsc+lint clean. Downstream verified ($0 tin counts as returned/banked/closeable).
  - **NOT deployed by prior session (I was possibly degraded + a money-flow change — deferred deliberately).** FRESH SESSION TO DO: sanity-check the money-invariant surface (no schema change, so no gated migration apply — but it's the tabung money flow; a quick cai heads-up is prudent, not a hard gate since no migration), then review → build-gate (Vercel preview) → FF merge to main → deploy → irsyad smoke → **confirm to Elly via `scripts/irsyad_support_send.sh`** (standing grant op#4406: reply to the client DIRECTLY, never wait).

## LIVE / DONE THIS SESSION
- **SG storefront P0 fixes DEPLOYED** (ac8ebfc on main): sold-out visibility, daily-cap/cutoff/available-days enforcement, slot picker. Migration 104_pos_orders_daily_count_idx applied to ceayj. Awaiting operator **dogfood/eyeball** verification before onboarding more merchants.
- **Proof-upload** (auto-confirm OFF) live earlier. #46 (anon paid-forge) verified already-closed both silos.

## BUILT-BUT-DARK (task #82 integration) — do NOT deploy a sibling alone
- `feat/storefront-broadcast` @f49f42f (send gated OFF; needs opt-in-capture build + PDPA review).
- `feat/storefront-delivery-provider` @9ffd180 (book gated OFF; needs 3 Lalamove partner answers).
- `feat/payment-provider-abstraction` @199fa11 (payment rails, parked #77).
- ⚠️ THREE-WAY migration-104 collision (broadcast/delivery/p0-wiring all authored 104; p0-wiring's IS applied). Renumber the other two to 105/106 at their deploy. Also 103 (payment-proofs bucket) applied-but-untracked in schema_migrations — reconcile.

## AWAITING CAI
- **#8772** — shipforge `get_site` (11_) apply-green → then shipforge→ceayj cutover: gated-apply 11_, coordinate synthetic-copy with **Nazim** (he holds substrate pg-admin, runs 30_ decommission), CAI-435 sequence, real-PII gate = write-path-on-ceayj verified + cai confirm → onboard HK/Crumbs. Task #66. Data-layer switch DONE (feat/shipforge-datalayer-switch @fdf77ea, 25/25 e2e).
- **#8782** — UAE data-residency architecture (no Supabase GCC region). Task #81.

## AWAITING OPERATOR
- SG P0 dogfood; UAE Tap-partner-call go (task #81 — Tap Payments rec'd, UAE requires trade-licence, home-food GCC-only); payment rails resume (#77, reuse existing `gl_*` ledger not new).

## STRATEGY DELIVERED (docs in reports/)
- HBB feature roadmap (#79, hbb-feature-roadmap doc + artifact) — most P0 already built; net-new = broadcast/delivery/loyalty.
- Own-rider economics (#83) — SG own-fleet at ~50 orders/day/zone via batching; UAE partner-fleet not own (visa). Behind DeliveryProvider abstraction.
- UAE launch scope (#81) + UAE payment scan.

## cai FAST-FOLLOWS (task #78)
- Activate hub auto-wake (CAI-451 approved): AUTO_WAKE_ENABLED=1 + realtime launchd service.
- Proof-upload code-default-OFF fail-safe (CAI-452 condition).

## KEY LESSON (memory saved)
[[schema-present-not-feature-wired]] + reinforce [[verify-before-explaining]]: state NO number/conclusion without checking the data (timestamps, code trace). Two misses this session. [[gazzabyte-group-send-path]] updated: answer the client directly, never wait.
