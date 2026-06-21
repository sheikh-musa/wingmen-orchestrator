# Shipforge.ai — Product Strategy v0

*Owner: cc-orchestrator (with operator + cai). Status: shaping, pre-build. Date: 2026-06-19.*

## The bet
The fleet is a governed substrate with no revenue. Shipforge is the turn to income:
a **conversational website cloner + manager** that lets clients self-maintain their
sites through a bot. Domain owned (shipforge.ai). Born from the operator's own pain
managing WordPress/WooCommerce sites on SiteGround.

**Why it's strong (not shiny):** dogfood (operator's own pain), warm distribution
(existing WP clients), reuses what we built (storefront multi-tenant-bot pattern),
recurring revenue (management = monthly).

## The wedge
**The MANAGER** — clients update their own site (content, products, prices, pages)
**by chat**. Most painful, most defensible, recurring. Cloning/building are
acquisition; managing is the moat + the revenue. Prove on the operator's own sites
first, then one real client.

## Two tracks, one bot-management layer
- **Content / brochure sites** → clone to a clean stack (Next.js/Vercel), bot-managed.
- **Commerce sites** → migrate to **our storefront platform** (multi-tenant orgs,
  products, slugs, TG Mini App — the dookana/ihsanos foundation), extended to the
  supported set. **We do NOT replicate WooCommerce.**

The storefront platform becomes shipforge's **commerce engine** — the "no-revenue"
infra becomes the product.

## Supported feature set (the 80%) — and explicit NON-goals (no takalluf)
**Supported (commerce):** product catalog, cart, checkout, 1–2 payment providers,
orders, basic inventory, content/pages, the bot-management layer.
**NON-goals (do NOT chase WooCommerce parity):** subscriptions, bookings,
memberships, marketplaces, complex tax/shipping rule engines, arbitrary plugins.
Chasing parity is a death march = takalluf. Serve the 80% excellently.

## Qualify-the-client rubric
For each prospective site, classify:
1. **Content-only** → clone to clean stack + bot-manage. (Easiest; start here.)
2. **Commerce within the supported 80%** → migrate to storefront + bot-manage.
3. **Exotic WooCommerce (outside 80%)** → either *manage-in-place* (keep their WP,
   add a bot-management layer only — no clone) OR **decline** (not our customer yet).
Never bend the platform to a single edge case.

## MVP — thinnest first ship (sequenced, no takalluf)
1. **Clone ONE of the operator's own content sites** to a clean Next.js/Vercel stack.
2. **Wire bot-management** — update pages/content by chat (reuse the bridge pattern).
3. Prove the loop: clone → host → self-manage-by-bot. Dogfood until it's beautiful.
4. THEN: a first content *client*.
5. THEN: commerce track (storefront migration) — the hard part, designed deliberately.

## Revenue model
Per-site monthly management fee (recurring). Optional one-time clone/migration fee.
Clients pay to self-maintain; operator stops doing manual WP grunt work.

## Hard risks to design for
- **Live commerce migration** (products, customers, order history, payment re-setup,
  SEO/redirects, zero-downtime) — real risk. Consider starting commerce clients on
  NEW storefronts before tackling live migrations.
- **SiteGround/WP integration mess** for manage-in-place clients — bound it tightly.
- **Scope creep toward WooCommerce parity** — the standing takalluf risk; the rubric
  is the guard.

## Decisions (settled 2026-06-19, operator-approved)
- O1: **First dogfood site = wingmen.dev** (our own — currently WP on SiteGround).
- O2: Content stack = **Next.js/Vercel + a light content layer the bot writes to** (chat-editable).
- O3: Supported set locked; WooCommerce-parity items are explicit non-goals (see above).
- O4: Build ownership = **cc-shipforge lane** (distinct identity) owns the MVP; cc-ihsanos
  takes the commerce/storefront track later.
- O5: Pricing = per-site monthly management fee + optional one-time clone fee (~$30–100/site/mo band; exact number TBD).

**Build status:** cc-shipforge lane booted on the dogfood MVP (clone wingmen.dev →
Next.js/Vercel → bot-manage). cai strategic read pending (gates the CLIENT-facing
launch, not the dogfood). MVP build plan owed from the lane before it builds.

## Reuse map (what already exists)
- Storefront multi-tenant-bot: orgs, storefront_slug, deep-links, single platform bot,
  TG Mini App (IHSANOS-STOREFRONT-TG-001 work).
- The conversational bridge pattern (operator↔orchestrator) → client↔site-manager bot.
- The design pipeline (frontend-design ihsan bar) → beautiful cloned sites.
- The governed fleet (a lane builds + maintains it; cc-reviewer gates quality).
