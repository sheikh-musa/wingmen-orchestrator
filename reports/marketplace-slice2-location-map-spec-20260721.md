# Build spec — Marketplace slice 2: Location + Nearby Map (privacy-critical)

**Repo:** ihsanos · **Base:** origin/main (tip `9d6bfef`) · **Branch:** `feat/storefront-marketplace-location` (fresh worktree off origin/main)
**Ratified model:** cai CAI-RESP-496 / **MARKETPLACE-LOCATION-PII-1** (binding) + CAI-RESP-497 build conditions · **Operator:** op#5835 "proceed with both"
**Design doc:** share.wingmen.dev/r/dookana-marketplace (§3 Privacy & Safety is the heart)

## ⚠️ This slice handles a home cook's HOME address — physical safety, not just data-privacy
Home cooks are very often women; the exact address is a Satr / women-safety concern. The privacy model is **binding and built by construction**, not bolted on. If any part is ambiguous, STOP and ask — do not guess on a location-safety detail.

## The privacy rule (non-negotiable, cai-ratified)
- **Exact address + true coordinates**: encrypted at rest (reuse the existing **CAI-477 address-encryption pattern** — find it: `git grep -i "encrypt" src/shared/lib` + the persons PII encryption migration 062), access-scoped. **NEVER on any public path.** Not a filtered field — the public projection must have NO exact-address/coords column at all (same discipline as slice 1's `DiscoveryStore`).
- **Public location = neighbourhood/area (text) + a server-fuzzed COARSE point** (snapped to an area centroid, ~500m–1km grid; NEVER true lat/long). Approximate distance is computed from the customer's own coarse point to that area.
- **Release of the exact address**: ONLY after the cook ACCEPTS an order, and ONLY to the fulfilment party (the delivery driver on delivery; for pickup, the accepted customer gets the pickup address). Browsing and order-placed-but-not-accepted states carry coarse-only.
- **Merchant opt-in + control**: a cook opts in to location display and APPROVES the public area shown. A delivery-only cook can expose NO home-identifying area at all.

## Scope IN

### 1. Data model (migration 115 — PII-SENSITIVE, cai PII-review required)
- Store the encrypted exact address + true coords using the CAI-477 pattern (encrypted column(s), access-scoped; mirror how persons PII / the customer address is stored). Likely on `organizations` or a dedicated `organization_location` table (prefer a separate table so the sensitive columns are isolated + easy to RLS-lock).
- Public coarse fields: `area_label TEXT` (neighbourhood, e.g. "Tampines"), `coarse_point` (fuzzed centroid — store as two numerics or a geography snapped to a grid; NEVER the true point), `location_opt_in BOOLEAN DEFAULT false`.
- **Migration is HUB-applied via the §6.6 guarded path with a REAL cai PII-review** (CAI-RESP-497 condition 3) — hand the `.sql` to cc-orchestrator; do NOT apply. ceayj + goumlyne parity.

### 2. Server-side fuzzing (the safety core)
- A pure function `coarsen(lat, lng) -> {area_label, coarse_lat, coarse_lng}` that snaps a true point to an area centroid / grid so the output is deliberately imprecise (~500m–1km). Deterministic, unit-tested. The TRUE point never leaves the encrypted store; only the coarse output reaches any public read.
- Distance shown on cards/map is customer-coarse → store-coarse (approximate), never a routable distance to a home.

### 3. Discovery projection + browse (extend slice 1)
- Add to the PUBLIC `DiscoveryStore`: `area_label`, `coarse_geo` (fuzzed), `distance_km` (approx, when the customer's coarse location is known). Extend `DISCOVERY_STORE_PUBLIC_KEYS` + the no-PII regression test to include these AND to assert the exact address/true-coords keys are STILL absent.
- Distance sort + a "nearby" filter (radius) on `/api/discover`.

### 4. Nearby map UI
- The map from the design doc: fuzzed neighbourhood pins (never a doorstep), the customer's own coarse point, a radius. Pins plot ONLY coarse points. Tap a pin → the store's public card → its order flow. Telegram Mini App, mobile-first.
- Customer location: obtained with consent (Telegram/`navigator.geolocation`), immediately coarsened client-side is NOT enough — the authoritative coarse read is server-side; never send/store the customer's precise point beyond what's needed, and never log it.

### 5. Merchant location flow (opt-in + area approval)
- In merchant settings: capture the store address (goes to the encrypted store), then SHOW the merchant the public area + fuzzed pin they'll expose and require explicit approval. A "delivery-only, don't show my area" choice exposes no area.

### 6. Post-accept address release
- On cook ACCEPT of an order: release the exact address only to the fulfilment party. Delivery driver integration may not be wired yet (there was a separate `feat/storefront-delivery-provider` branch) — if delivery isn't available, scope this to: the cook sees the customer's delivery address in their own order management (already the case), and for PICKUP the accepted customer receives the store's exact pickup address post-accept. Gate the exact-address reveal behind order state = accepted. If the full driver-release path can't be completed without delivery wiring, IMPLEMENT the state-gated reveal seam + note what's deferred — do NOT leave the address readable pre-accept.

## Scope OUT / defer (note explicitly if hit)
- Multi-region federation (SG plane only; keep the region seam from slice 1).
- Full delivery-driver dispatch (depends on delivery provider wiring) — implement the release SEAM, defer driver specifics if blocked.
- Reviews/ratings (unchanged from slice 1).

## Gates (all must pass before hub integrates)
- **Tests**: `coarsen` fuzzing (true point never recoverable from output; output within an area grid); discovery projection still leaks NO exact-address/coords key (extend the recursive no-PII test with the new location fields present in input); distance sort; post-accept reveal gated on order state; merchant area-approval required before any public area shows.
- **Migration 115**: PII-sensitive `.sql` handed to hub for §6.6 guarded apply + **cai PII-review** (do NOT apply). ceayj + goumlyne parity.
- **cc-reviewer**: assert (1) NO exact address/true-coords in any public/discovery response, API, index, log, or map payload — trace the full path; (2) post-accept reveal is state-gated to the fulfilment party only; (3) fuzzing is server-authoritative + irreversible; (4) merchant opt-in/area-approval enforced; (5) money paths untouched.
- **QA-EDGE**: drive browse+map with a synthetic opted-in store — assert the response/map carries only coarse area + fuzzed point, never the seeded exact address; pre-accept order shows coarse, post-accept reveals exact only to the right party; delivery-only store shows no area. Synthetic marked data, never a real client silo.

## Deliverable
Focused PR off origin/main from the isolated worktree. Report to cc-orchestrator with diff summary, test output, and the migration `.sql` path (flagged PII-sensitive for cai review) when ready for the gate. Do NOT apply migrations or self-merge. **Report any location-safety ambiguity immediately — do not guess.**
