# EXPEDITED FULL audit — PR #405 (P1 hotfix: preparer donations queue 414)

**Auditor:** cc-quality (expedited FULL, co-check with orch-console opus) · **Date:** 2026-08-21
**Verdict: PASS — P1 clear to merge.** 1 non-blocking finding (latent, unreachable, pre-existing).
Requested by orch-console (bus #30334, thread `f5facb8d`). Verified **at source + empirically on goumlyne (read-only)**.

Pinned HEAD `f0d287206e71b42983366a9b77c8664227ad3902` (= `gh pr view 405`, MERGEABLE, base `main`).
2 files, **no migration**: `src/modules/donations/api.ts` + new `list-donations-queue.test.ts`.
Gates: **`npm run lint:all` EXIT 0** (16 gates) · **vitest 5/5**.

## The fix
The CAI-1198 preparer lenses previously resolved the org's issued-receipt donation-id set and serialised it into a `.not("id","in",(…))` / `.in("id",(…))` URL filter. Irsyad has **990 issued-receipt UUIDs** → ~36KB GET → gateway **414** → supabase-js error → `throw` → preparer donations page stuck on the skeleton. The fix replaces the id-set with a PostgREST **embedded-resource join** (zero ids in the URL):
- `needs_receipt`: `rq:receipts!left(donation_id)` + `.eq("rq.status","issued")` + `.is("rq",null)` — anti-join.
- `issued_by_me`: `rq:receipts!inner(donation_id)` + `.eq("rq.status","issued")` + `.eq("rq.issued_by", userId ?? "")` — semi-join.
- `rq` is a filter-only alias, stripped from returned rows (`delete d.rq`).

## Empirical verification (goumlyne, org `73339164…` = the 414 victim, 990 issued receipts, read-only)
Encoded the exact SQL that PostgREST's embed filters translate to (`!left`+`rq.status=issued`+`rq is null` ≡ `NOT EXISTS issued`; `!inner`+eq ≡ `EXISTS`; `count:exact` ≡ distinct parents) and compared row-sets to the OLD id-set logic.

| Check | OLD | NEW | Δ (old∖new / new∖old) | Result |
|-------|-----|-----|------------------------|--------|
| **1. needs_receipt** (anti-join ≡ NOT-IN) | 2505 | 2505 | **0 / 0** | EXACT |
| **1. issued_by_me** (semi-join ≡ IN, user `ca2a4a9c…`) | 990 | 990 | **0 / 0** | EXACT |
| **cross-org receipts** (only divergence source) | — | — | **0** | none — OLD org-key ≡ NEW FK-key |
| **2. no double-count** (synthetic: 2 issued receipts/donation) | — | flat=2, parent(count:exact)=**1** | — | PostgREST nests children → 1 |
| **3. count:exact under embed** | 2505 / 990 | 2505 / 990 | match | correct |
| **C. voided edge** (synthetic: voided-only donation) | included | `NOT EXISTS(issued)`=true → **included** | — | matches OLD (issued-only filter) |

- The **voided edge** is only provable synthetically here: org `73339164…` currently holds *only* `issued` receipts (0 voided/other), so there is no live voided row to observe. The anti-join logic makes any non-issued receipt transparent to `rq.status=eq.issued`, leaving `rq` null → the donation counts as `needs_receipt`, matching OLD. Disclosed rather than asserted.
- NEW is in fact **more** correct at scale than OLD: OLD's `.limit(5000)` on the receipt resolve would silently truncate a >5000-issued-receipt org; NEW has no such cap. (Irsyad's 990 < 5000, so they coincide today.)

## 4/5/6 (static)
- **4. rq stripped / shape intact:** returned rows are mapped through `{...row}; delete d.rq`; the **display** embed `receipts(id,voided_at,status)` is a *separate* key (unchanged) — Donation shape preserved, no `rq` leaks.
- **5. zero access/exposure change:** same `.eq("org_id", orgId)`, same role-gated caller (`page.tsx` unchanged, always passes the authenticated `user.id`); the `rq` embed selects only `donation_id` (no PII). This RESTORES already-entitled access — no RBAC/exposure delta.
- **6. URL-safe at any receipt volume:** the new regression test asserts the select carries the embed alias AND that **no `.not` / no `.in("id",…)`** call occurs AND **no separate `from("receipts")`** resolve occurs — the id-set code path is deleted, so URL size is O(1) in receipt count.

## FINDING (non-blocking — latent, unreachable, pre-existing) — `issued_by_me` empty-userId
The brief states "empty-userId → inner yields 0 (shows nothing)". **That is not what happens.** `issued_by` is a `uuid` column; `.eq("rq.issued_by", userId ?? "")` with an absent userId sends `issued_by=eq.` (empty) → Postgres `uuid = ''` → **`invalid input syntax for type uuid` (22P02)** → PostgREST 400 → supabase-js error → `throw`. So the empty case would *error the page*, not render an empty list.

**Why it does not block the P1:** the sole caller of this lens (`page.tsx:59`) always passes `userId: user.id` (the page redirects to `/login` when unauthenticated), so the empty-userId branch is **unreachable in production**. It is also **not a regression** — the OLD code had the identical `?? ""` on the receipts query. The `?? ""` fallback is dead-defensive.

**Optional fast-follow (not a merge gate):** either drop the misleading "yields 0 / shows nothing" comment, or make the empty case a guaranteed no-match without a uuid cast (e.g. short-circuit to an empty result when `!userId`, mirroring the old sentinel intent). Advisory only — does not require cai.

## Verdict
**PASS.** Both live lenses are byte-exact vs the old id-set logic on real Irsyad data (0/0 both); no double-count; count:exact correct; no access/exposure change; URL-safe by construction; gates green. Independently confirms orch-console's opus pass on items 2–6 and 1's two live cases, and adds the empty-userId finding a pure read missed. Client-only (no DB) → merges as code; no §6.6 apply. Clear to merge on your chain (PASS + CI green).
