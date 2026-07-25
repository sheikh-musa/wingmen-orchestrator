# Build spec — Back navigation everywhere (storefront + merchant)

**Repo:** ihsanos · **Base:** origin/main (tip `5144a23`) · **Branch:** `feat/storefront-back-nav` (fresh worktree off origin/main)
**Operator:** op#5865 "i realize there's no back button anywhere. can we add one everywhere?" (found while dogfooding the marketplace)

## Goal
A consistent back affordance on EVERY sub-page, so a user (customer or merchant) can always go one step back. In the Telegram Mini App use the NATIVE Telegram back button (top-left in the TG header — what users expect); on web / outside Telegram, an in-page back arrow with identical behaviour. One shared mechanism, not per-page one-offs (there are scattered inconsistent backs today — unify them).

## What exists (reuse/extend, don't reinvent)
- `src/app/m/NativeChrome.tsx` already integrates `Telegram.WebApp` on the merchant side — extend this pattern (or factor a shared hook out of it).
- Layouts to drive it: `src/app/shop/layout.tsx`, `src/app/shop/[slug]/layout.tsx`, `src/app/m/layout.tsx`.
- Scattered one-off "back" links exist in several `/m` pages (get-listed, location, products, store, orders) — REPLACE them with the shared mechanism for consistency.

## Scope IN

### 1. Shared back-nav primitive
- A hook `useBackNav({ href?, onBack? })` + a small `<BackNav>` component:
  - **In Telegram** (`Telegram.WebApp` present): call `WebApp.BackButton.show()`, register `onClick` → navigate back (`router.back()`, or a provided logical parent `href` when back would leave the app), and `BackButton.hide()` on unmount / on top-level pages. Handle the WebApp lifecycle correctly (no leaked handlers, no double-registration; use `offClick` cleanup).
  - **Outside Telegram**: render an in-page back arrow (top-left of the page header) with the same navigation. Do not render both (TG native OR in-page, not duplicated).
- Respect Telegram theme + safe-area (the header notch); keyboard-focusable + a visible focus state; `prefers-reduced-motion` respected.

### 2. Apply it EVERYWHERE below the top level
- **Storefront** (`/shop/[slug]/*`): product, cart, checkout, confirmation, orders, order detail, track — each gets back to its logical parent.
- **Merchant** (`/m/*`): every editor + sub-page (marketplace, location, hero, get-listed, products + product sub-pages, hours, fulfillment, delivery, paynow, onboard, orders, order detail).
- **Top-level pages get NO back** (nothing to go back to): the browse home `/shop`, a store landing `/shop/[slug]`, the merchant home `/m`. Confirm these are treated as roots.
- Prefer wiring via the LAYOUTS (route-depth aware) so a new sub-page inherits back automatically — but ensure each page's logical "back target" is correct (back from checkout → cart, from product → menu, from a /m editor → /m or the store hub), not just blind history when history is ambiguous (e.g. deep-linked entry with no history should still go somewhere sane, not a dead end).

### 3. Consistency + de-dupe
- Remove the existing one-off back links/arrows in the `/m` pages listed above; they now come from the shared mechanism. No page should have two backs.

## Scope OUT
No data/model/discovery/privacy changes. No migration. No money-path change. Not a redesign of the pages — just add/unify back navigation.

## Gates
- **Tests**: the hook shows/hides the TG BackButton on mount/unmount + top-level vs sub-page; cleanup removes the handler (no leak/double-register); the web fallback renders only outside Telegram; back target resolves to the logical parent (not a dead-end on no-history deep-link).
- **cc-reviewer**: no duplicated back controls; TG BackButton handlers are cleaned up (offClick) — a lingering handler navigating the wrong page is the main risk; top-level pages have no back; no money/discovery/privacy change; deep-linked entry (no history) doesn't dead-end.
- **QA-EDGE + eyeball**: screenshots (mobile) of a storefront sub-page and a `/m` sub-page showing the back affordance; a simulated Telegram context (WebApp present) showing the native back is wired and in-page back is suppressed; drive back from a few deep pages → lands on the right parent.

## Deliverable
Focused PR off origin/main from the isolated worktree. Report to cc-orchestrator with diff + tests + screenshots. Do NOT self-merge. Capture screenshots in-repo (lane env has working Playwright).
