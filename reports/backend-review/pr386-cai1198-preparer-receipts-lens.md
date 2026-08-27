# cc-quality FOCUSED Satr review — PR #386 (CAI-1198 preparer receipts lens)

- **PR:** #386 `feat(donations): preparer receipts LENS — Needs receipt / Issued by me [CAI-1198]`
- **Files:** `src/modules/donations/api.ts` (+35), `src/app/dashboard/donations/{donations-list.tsx,page.tsx}` (UI wiring). No migration, no receipts-list file, no persons/name-resolve module touched.
- **Reviewer:** cc-quality (Sonnet 5, per orch-console — standard tier, not opus-required for this PII-adjacent-not-money change)
- **Requester:** orch-console (bus #29291), FOCUSED pre-merge Satr pass, after coord PASS + console's own source-verify
- **CI:** all green (lint-and-typecheck, tabung-correctness, unit-tests, e2e skipped, both Vercel previews) — `mergeStateStatus=CLEAN`

## Verdict: **PASS.**

## The 6 requested checks, each verified at source

**1) Receipts sub-query selects only `donation_id`, no PII.** `src/modules/donations/api.ts`: `supabase.from("receipts").select("donation_id").eq("org_id", orgId).eq("status", "issued")` (+ `.eq("issued_by", userId)` for the `issued_by_me` branch). Single-column resolver, no name/NRIC/email/phone column ever selected. Confirmed.

**2) Uses the caller's RLS-bound client, never service-role.** Traced the client all the way from `page.tsx`: `const supabase = await createServerClient();` passed directly into `listDonations(supabase, orgId, {...})` — the new receipts query runs on that same `supabase` parameter, never a separately-created service-role client. No RLS bypass anywhere in this diff. Confirmed.

**3) The key Satr check — persons-embed unchanged, CAI-1192 minors carve-out still applies.** Diffed the exact select string between `origin/main` and this branch: `"*, donation_categories(id, name, category_type), persons(id, display_name, email, phone)"` — byte-identical on both sides (only the surrounding line number shifted). This PR does not touch that query at all; it only adds a donation_id-filter step before it. The `persons` table's RLS (mig192, CAI-1030) still governs exactly what that embed can return — no new donor-name exposure, no widening. Confirmed, not assumed from the diff being small.

**4) Fail-closed empty-set semantics.** Read the actual branches: `needs_receipt` → `if (donationIds.length) query = query.not("id","in",...)` — when zero receipts exist yet, the exclusion is **skipped entirely**, so the base (unfiltered) query returns, correctly showing "all donations" = "all needing" since none have receipts. `issued_by_me` → `query.in("id", donationIds.length ? donationIds : ["00000000-0000-0000-0000-000000000000"])` — an empty result set resolves to a sentinel UUID that can never match a real donation, so it correctly shows nothing rather than falling through to "all rows." Both directions confirmed by code, not inferred.

**5) The 5000-cap — assessed, non-blocking.** Queried the real data directly rather than trusting the PR's "990 << 5000" claim: goumlyne's single largest org has **exactly 990** issued receipts (matches exactly); ceayj currently has **zero**. ~20% cap utilization at the worst case across both silos — comfortable headroom. Also checked whether `receipts` RLS itself could silently truncate the exclusion set independent of the cap: `auth_user_staff_org_ids()` (the policy backing "Staff can see receipts in their org") includes `preparer`, so a preparer session sees the full org's receipts, not just their own — the 5000-row `.limit()` is the only truncation source, not RLS scope. **Failure mode when the cap is eventually hit is benign, not a Satr issue**: `needs_receipt` could under-exclude (show a donation as needing a receipt when one already exists) — a UX/correctness gap in a self-described "proxy lens, not a compliance-exact filter," never a PII leak or a change to the underlying donation/receipt record. **Recommendation: fast-follow, not a merge blocker** — add a log-when-`rRes.data.length === 5000` so the team gets a signal before it silently degrades, but current volume doesn't warrant blocking this PR on it.

**6) Queue param whitelisted; userId is session-derived.** `page.tsx`: `queue: params.queue === "needs_receipt" || params.queue === "issued_by_me" ? params.queue : undefined` — strict two-literal allowlist via `===`, any other value (including injection attempts) becomes `undefined`. `userId: user.id` where `user` comes from `supabase.auth.getUser()` — a genuine server-verified session, never `searchParams`/URL input. Confirmed.

## One extra check I ran on my own initiative

`donations-list.tsx`'s `userRole` prop type gained `"preparer"` in this diff, which could read as a role-access widening. Verified it isn't: the page-level access gate (`if (membership.role !== "org_admin" && ... !== "preparer") notFound()`) is **untouched by this diff** — grepped the diff hunks directly, that block doesn't appear — so preparer already had access to this page before PR #386; this PR only fixes a pre-existing TypeScript union-type omission (the prop type previously didn't list "preparer" even though a real preparer session could already reach the component). `requireModule` still fails closed per-org (`entry.access === "none"` → `notFound()`), matching the established per-org-grantable pattern. Not a new grant.

## Bottom line

This is a genuinely narrow, well-scoped filter over data the caller already sees — no new PII surface, no RLS bypass, no widened role access, fail-closed on both empty-set edges, and the one soft spot (the 5000-cap) is real but low-risk today with a clean non-blocking fast-follow. Safe to merge (console's gated pen, not mine).
