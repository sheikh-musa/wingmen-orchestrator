# Build spec — Merchant device photo upload (hero image from phone)

**Repo:** ihsanos · **Base:** origin/main · **Branch:** `feat/storefront-hero-device-upload` (worktree off origin/main)
**Operator:** op#5926 (full-tilt backlog; enables the Hadi photo-onboarding nudge). Fast-follow from slice 3 (hero was URL-only).

## Goal
Let a merchant add their store/food hero photo **from their phone** (upload), not just paste a URL. The hero image already flows to the marketplace StoreCard (`hero.image_url` → discovery `thumbnail`), so a good uploaded photo instantly makes their card look premium.

## Reuse — do NOT build storage from scratch
The customer payment-proof flow (`src/actions/customer-payment-proof.ts`, `src/modules/storefront/payment-proof-*`, `relay-proof-to-merchant.ts`) already does **Supabase Storage uploads** (bucket, upload path, type/size validation, rate-limit, RLS). REUSE that pattern for the hero image. The current hero editor (`src/app/m/hero/HeroImageEditor.tsx` + `setStorefrontHeroImage` in `storefront-settings.ts`) sets `settings.storefront.hero.image_url` — keep that as the write target.

## Scope IN
1. **Storage**: a `hero-images` (or reuse an existing suitable) bucket, **public-read** (hero images ARE public — they render on the storefront + marketplace card; no PII). Merchant can write only to their own org path (`{org_id}/...`), enforced by Storage RLS. If a new bucket/policy is needed, provide the additive migration/config `.sql` for the hub to apply via §6.6 (do NOT self-apply). PII-review is trivial (public hero images, no personal data) — but flag it.
2. **Upload UI** in `HeroImageEditor`: a "Upload a photo" action → device file picker (`accept="image/*"` + `capture` hint for camera) → upload to the bucket → set `hero.image_url` to the public URL via the existing `setStorefrontHeroImage` action (org-admin gated). Keep the paste-a-URL option too.
3. **Image handling**: validate type (jpeg/png/webp) + size cap; client-side downscale/compress large phone photos before upload (phone photos are multi-MB) to a sane max dimension (e.g. 1600px) so cards load fast. Show upload progress + a preview; graceful error copy on failure.
4. **Marketplace flow-through**: confirm the uploaded `hero.image_url` renders on the StoreCard (it already reads that field) — no discovery change needed; just verify.

## Scope OUT
No discovery-engine / privacy-projection change (hero was already a public field). No merchant location/PII change. Not a redesign of the hero editor — add the upload path.

## Gates
- **Tests**: upload happy-path (file → bucket → hero.image_url set); type/size rejection; client-downscale produces a smaller image; org-admin scoping (a merchant can't write another org's hero path); URL-paste path still works.
- **Migration** (if a new bucket/policy): additive, handed to hub for §6.6 apply + (trivial) PII-review. Do NOT self-apply.
- **cc-reviewer**: Storage RLS scopes writes to the caller's own org; bucket is public-READ only (no write/list to anon); reuses proof-upload validation (no arbitrary file types / size DoS); `setStorefrontHeroImage` authz unchanged; money/discovery/privacy untouched.
- **QA-EDGE + eyeball**: upload a photo on a synthetic store → it appears as the hero + on the marketplace StoreCard; screenshots (mobile) of the upload flow + the resulting card. Reject a non-image + an oversized file.

## Deliverable
Focused PR off origin/main from the isolated worktree. Report to cc-orchestrator with diff + tests + screenshots + the migration `.sql` path (if any). Do NOT self-merge. Capture screenshots in-repo (lane env has working Playwright).
