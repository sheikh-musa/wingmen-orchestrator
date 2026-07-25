# Build spec — Marketplace slice 3: Merchant "Get Listed" guided flow

**Repo:** ihsanos · **Base:** origin/main (tip `6836236`) · **Branch:** `feat/storefront-get-listed` (fresh worktree off origin/main)
**Operator:** op#5843 "proceed full tilt, no need to wait on me" · Marketplace vision / BotFather-guided merchant side.

## Why
The marketplace (browse + nearby) is live but only demo stores are in it. All the merchant pieces EXIST but are scattered across separate `/m` pages (`/m/marketplace` opt-in+cuisine, `/m/location`, hero via storefront config, `/m/products`, `/m/paynow`) — a cook has to know to visit each. There is NO single guided path that gets a real home cook from "I have a store" to "I'm listed and my card looks good." This slice is that path — the supply side.

## What a store needs to be discoverable (already enforced in `discovery-engine.isDiscoverable` + the loader — REUSE it, don't re-derive)
opted-in (`marketplace_opt_in`) AND live AND ≥1 AVAILABLE product AND valid PayNow. Plus, to look good: ≥1 cuisine tag + a hero photo. Location/area is OPTIONAL (opt-in).

## Scope IN — a guided flow, reusing every existing editor

### 1. Listing-readiness model (pure, tested)
- A pure function `getListingReadiness(store)` → an ordered checklist of steps with done/todo state, derived from the SAME rules as `isDiscoverable` + "looks good": PayNow set, ≥1 available product, ≥1 cuisine tag, hero photo present, marketplace opt-in, (optional) location/area approved. Returns `{ ready: boolean, steps: [...], missingCount }`. No new source of truth — read the org's existing storefront config + products + paynow + marketplace/location columns.

### 2. "Get Listed" hub surface (`/m` home + a `/m/get-listed` page)
- On the merchant hub (`/m/page.tsx` / StoreHub), a prominent **"Get listed in the marketplace"** card showing readiness ("You're listed ✓" or "2 steps to get listed"), linking to the guided flow.
- `/m/get-listed`: the guided checklist — each step shows its status and a CTA that routes to the EXISTING editor for that step (cuisine → `/m/marketplace`, hero → the storefront hero setter, products → `/m/products`, paynow → `/m/paynow`, location → `/m/location`, opt-in → the toggle). BotFather-style: guided, one clear next action, encouraging copy. Do NOT rebuild those editors — link to them and reflect their state.

### 3. Hero-photo prompt (the card-quality gap)
- A store with no hero renders a placeholder card. Surface a clear "Add a photo of your food" step that routes to the existing hero/image setter (`storefront` hero config via `storefront-settings.ts` / hero-section). If uploading isn't wired for merchants, wire the minimal path or clearly flag what's missing — a good card needs a photo.

### 4. Live listing preview
- Show the merchant their actual marketplace card — reuse the customer `StoreCard` component rendered with THIS store's public projection (via the discovery projection builder or an equivalent read), so they see exactly what customers will see, updating as they complete steps. "This is how you'll appear in the marketplace."
- A clear publish/opt-in confirmation: when ready + they opt in, "You're live in the marketplace 🎉" with a link to their card in browse.

### 5. Guardrails
- Reuse `getMerchantContext` for auth (org-admin scoped); a merchant only ever sees/edits their OWN org. Do NOT touch the discovery privacy projection or the location encryption/coarse model (slices 1-2). No new anon exposure. Location step reuses the slice-2 LocationEditor + area-approval (unchanged).

## Scope OUT
Delivery-driver address release (separate slice), real ratings, multi-region. New PII columns (none needed — reuse existing).

## Gates
- **Tests**: `getListingReadiness` (each step done/todo, ready when all required met, optional location doesn't block); the preview renders the real StoreCard from the public projection; role/auth scoping (a merchant can't view another org's readiness/preview).
- **No migration expected** (reuses existing columns). If one is needed it's additive + handed to hub.
- **cc-reviewer**: privacy projection untouched; location model untouched; money/per-store flow untouched; no cross-org leak; the readiness logic matches `isDiscoverable` (no store shown as "listed" that discovery would hide, and vice-versa).
- **QA-EDGE + eyeball**: drive a synthetic store from "0 steps done" → fully listed; screenshots of the get-listed checklist + the live preview at mobile+desktop. Confirm a not-ready store shows the right missing steps and a ready store shows "listed" + appears in `/api/discover`.

## Deliverable
Focused PR off origin/main from the isolated worktree. Report to cc-orchestrator with diff + tests + screenshots when ready for the gate. Do NOT self-merge. Capture screenshots in-repo (the lane env has working Playwright).
