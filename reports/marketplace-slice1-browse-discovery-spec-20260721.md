# Build spec — Marketplace slice 1: Browse + Search discovery (location-free)

**Repo:** ihsanos · **Base:** origin/main (tip `3ffa52e`) · **Branch:** `feat/storefront-marketplace-discovery` (fresh worktree off origin/main)
**Ratified by:** cai CAI-RESP-497 (design), CAI-RESP-496 / MARKETPLACE-LOCATION-PII-1 (privacy) · **Operator:** op#5819 "full tilt ahead"
**Design doc:** share.wingmen.dev/r/dookana-marketplace

## Why this slice first
It reuses the already-merged, marketplace-ready catalog engine (`src/modules/storefront/catalog/catalog-engine.ts` — `queryCatalog({orgIds, search, category, availableOnly, sort})` already scopes by an ORG SET), needs **zero location data**, and is the fastest path to a working marketplace surface. Location + map are slice 2 (separate spec) and stay out of this slice entirely.

## Scope IN

### 1. Merchant opt-in + cuisine tags (BotFather-style)
- Additive migration on `organizations`: `marketplace_opt_in BOOLEAN NOT NULL DEFAULT false`, `cuisine_tags TEXT[] NOT NULL DEFAULT '{}'`. (Columns, not settings-jsonb — they're filtered/indexed by discovery.) Partial index `WHERE marketplace_opt_in AND deleted_at IS NULL`.
- Merchant settings UI + TG-bridge flow: a toggle "List my store in the Dookana marketplace" + a cuisine-tag picker (≥1 required to opt in). Guided/inline-button style per the storefront north star.
- **Do NOT write the migration to any DB.** Hand the `.sql` to the hub — hub applies via the §6.6 direct-psycopg guarded+parity path (cai condition 3). Additive, so no cai gate, but PII-review still runs (trivial here — no PII in these columns).

### 2. Discovery public-projection read model (the core discipline)
- A typed, explicit projection that returns **ONLY public fields**: `org_id, slug, name, cuisine_tags, open_state (derived), menu_summary (category list + a few sample dish names/prices), thumbnail?`. Nothing else.
- **NO location, NO address, NO private/PII column may appear** — not even filtered-out, physically absent from the projection. (Slice 1 captures no location at all; build the projection so that when slice 2 adds an encrypted address + fuzzed geo, the exact address can never reach this path.)
- Implement as `getDiscoveryStores(query)` in a new `src/modules/storefront/discovery/` module (pure read + a thin data loader), selecting only the public columns. Optionally back it with a DB view `discovery_stores_public` for physical separation — acceptable but the column-scoped select is the hard requirement.

### 3. Discovery API — `GET /api/discover?search=&cuisine=&open=`
- Loads opted-in + transact-capable stores (see gate below) and their public products, runs `queryCatalog` scoped by that org SET, returns store cards (+ optionally matching dishes for a search).
- Region-scoped seam: accept an (optional, defaulted-SG) region param now; single SG index today, federates later per residency registry — do NOT build federation, just don't hardcode a single global assumption.

### 4. Browse/search home UI (the marketplace landing)
- The design-doc mockup: store cards (name, cuisine, open/closed badge; distance is slice 2), cuisine filter chips, a search box (stores + dishes), an "Open now" filter. Telegram Mini App, native feel.
- **Entry:** default landing when the Dookana bot is opened WITHOUT a specific store deep-link. Every existing direct store deep-link (`/shop/<slug>`) must keep working byte-for-byte — additive only, zero regression to the per-store flow (Hadi et al.).

### 5. Discoverability gate (cai Q3 — can-transact)
- A store is discoverable **only if**: `marketplace_opt_in = true` AND `deleted_at IS NULL` AND has ≥1 published/available product AND **PayNow is set up** (valid identity per `src/shared/lib/paynow-identity.ts`). Never list a store a customer can't actually pay — no dead-ends.

### 6. Ratings (cai Q4 — no fabrication)
- Show "New" or an order-count only. **Never** render a fabricated star rating. Real reviews wait for an order-completion loop (later slice).

## Scope OUT (explicitly slice 2+)
Location capture, map, server-fuzzed coarse geo, encrypted exact address, neighbourhood/area approval, post-accept address release, reviews. **No location data is captured or stored in slice 1.**

## Gates (must all pass before hub integrates)
- **Tests:** extend `catalog/__tests__` + new `discovery/__tests__`: opt-in org-set scoping; a not-opted-in store is absent; a PayNow-less store is absent; a soft-deleted store is absent; search/cuisine/open-now filters; **an assertion that no discovery response object contains any address/location/PII key**.
- **Migration:** additive `.sql` handed to hub; hub applies via §6.6 guarded+parity (+PII-review). Lane does NOT touch any DB.
- **cc-reviewer:** asserts (1) no location/PII field in any discovery response or projection/index; (2) PayNow-gate enforced; (3) per-store deep-links unchanged; money paths (place-order/payment) untouched.
- **QA-EDGE (read-only, deployed artifact):** browse with zero opted-in stores (empty state), one store, many stores, search-no-results, cuisine filter, open-now filter, not-opted-in store hidden, PayNow-less store hidden. Synthetic + marked + cleaned data only; never touch a real client silo.

## Deliverable
Focused PR off origin/main from the isolated worktree. Report back on the bus (to cc-orchestrator) with the diff summary, test output, and the migration `.sql` path when ready for the review gate. Do not self-merge; hub owns integration + the release signal.
