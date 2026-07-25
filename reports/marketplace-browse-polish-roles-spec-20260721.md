# Build spec — Browse polish + Customer/Merchant role entry-points

**Repo:** ihsanos · **Base:** origin/main (tip `9d6bfef`) · **Branch:** `feat/storefront-browse-polish-roles` (fresh worktree off origin/main)
**Operator:** op#5835 "proceed with both" + the role-model question (answered on @ihsanos)

## Part A — Browse polish (visual quality, the ihsan bar)
Slice 1 browse cards use a letter-avatar placeholder. Make it feel like a real marketplace.
1. **Food photos on cards**: use the store's existing hero image (`settings.storefront.hero.image_url` — already loaded as `thumbnail` in the discovery projection) as the card image; if absent, keep a tasteful branded placeholder (not a bare letter box — e.g. a subtle food-themed gradient tile + initial). Never break layout on a missing/broken image (`onError` fallback, fixed aspect ratio, `object-fit: cover`).
2. **Card polish**: the sample-dish preview reads well; make the "Open/Closed" state + "New"/order-count rating visually clean; ensure long store names / many cuisine tags wrap gracefully (mobile 390px). No horizontal scroll on the page body.
3. This is CUSTOMER-facing storefront UI → **eyeball bar applies**: capture mobile (390px) + desktop screenshots of the populated + empty browse and include them in your deliverable so the hub can eyeball before ship.
- No data-model / discovery-engine change needed — this is presentation only (MarketplaceBrowse.tsx + card components). Do NOT touch the privacy projection.

## Part B — Customer/Merchant role entry-points (the model the operator asked about)
**Design decision (hub, op#5835):** a person is ONE Telegram identity; "customer" and "merchant" are CONTEXTS that identity holds simultaneously, NOT mutually-exclusive account types. Customer is the default (anyone browses + orders, no signup — orders link to their telegram_user_id via the initData work). Merchant = the same person also belongs to an org (`org_members`). A merchant ordering elsewhere is just a customer there; a customer opening a store creates an org and keeps ordering everywhere. **Never gate the user with an "are you a buyer or seller?" prompt.**

Make these entry points explicit in the Dookana bot / storefront app:
1. **Default = customer**: opening the bot with no store deep-link → marketplace browse (slice 1, already live). A store deep-link → that store's order flow. No change to these except surfacing (2).
2. **Merchant surface, context-driven**: resolve the current Telegram user (verified initData → telegram_user_id) → look up `org_members` for that person.
   - Has ≥1 org (is a merchant): show a persistent **"Manage my kitchen"** entry (button / menu item) → the merchant/admin surface (existing `/m` merchant area). If they own multiple orgs, let them pick.
   - Has no org: show **"Open your own kitchen"** → the existing TG-native onboarding (createOrganization flow). After onboarding they become a merchant, keeping customer everywhere.
3. **Reuse, don't rebuild**: the merchant area (`/m/*`) + onboarding already exist. This task WIRES the identity→role resolution + the entry-point buttons; it does not rebuild merchant screens. Find the existing merchant entry + onboarding (`src/app/m/`, the createOrganization action) and route to them.
4. Keep it BotFather-guided on the merchant side, Durger-King-native on the customer side (the storefront north star).

## Gates
- **Tests**: role resolution (a telegram_user_id with an org → merchant entry shown; without → "open your kitchen"; multi-org → picker); image fallback (missing/broken hero → placeholder, no layout break). No change to the discovery privacy projection — add a test asserting Part A/B touch none of the `DiscoveryStore` public-key contract.
- **No migration expected** (org_members + telegram_user_id already exist). If you find you need one, it's additive-only and handed to hub — do NOT apply.
- **cc-reviewer**: per-store flow + money paths untouched; the privacy projection unchanged; role resolution can't leak one person's org access to another; images can't break layout.
- **QA-EDGE + eyeball**: screenshots of browse (polished, populated + empty) mobile+desktop; the merchant "Manage my kitchen" vs "Open your kitchen" surfaces for a with-org vs without-org synthetic user.

## Deliverable
Focused PR off origin/main from the isolated worktree. Report to cc-orchestrator with diff summary, test output, and screenshots. Do NOT self-merge. This branch + the slice-2 location branch both fork off `9d6bfef` — the hub integrates BOTH onto one branch + runs a combined gate before any deploy (parallel-branch integration discipline).
