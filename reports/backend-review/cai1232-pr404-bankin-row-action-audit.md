# FULL audit — CAI-1232 PR #404 (out-of-order Keluarga/umum report bank-in)

**Auditor:** cc-quality (co-equal FULL tier) · **Date:** 2026-08-21 · **Verdict: PASS, merge-ready.**
Requested by orch-console (bus #30273, thread `8262e0ad`). Verified **at source**, not from summaries.

Pinned HEAD `4d8265dea2cb7fa5ac89fc3f8d39a4a50a17faf0` (= `gh pr view 404`, MERGEABLE, base `main`).
3 files, **no migration**, backend `attachReportDepositAction` **untouched** (not in the diff).
Gates at pinned HEAD: **`npm run lint:all` EXIT 0** (16 gates) · **vitest 6/6** (reports-list-bankin.test.tsx).

## The change
`reports-list-client.tsx` adds a per-row `canRecordDeposit(canPrepare, r)` and a "Record deposit" link (card + table views) deep-linking to `…/reports/{public_id}#bank-deposit`; `report-detail-client.tsx` adds `id="bank-deposit"` + `scroll-mt-20` to the existing deposit `<div>` (anchor target only — no logic). The card was refactored from an outer `<Link>` to a `<div>` wrapping an inner `<Link>` (Open) + the conditional action — correct, since `<a>` can't nest.

## 5 focus items — all verified at source

**1(a) Mirror-sync — EXACT MATCH.** Detail source (`report-detail-client.tsx:175`): `canRecordDeposit = canPrepare && !hasDeposit && (report.status === "draft" || report.status === "preparer_signed")`, `hasDeposit = !!report.deposit_reference`. List mirror: `canPrepare && !r.deposit_reference && (r.status === "draft" || r.status === "preparer_signed")`. `!r.deposit_reference` ≡ `!hasDeposit`; status set identical. No drift.

**1(b) Real authz boundary is server-side & re-enforced — CONFIRMED.** `attachReportDepositAction` (tabung-weekly-reports.ts, **unchanged**): view-as write-guard (fail-closed, zero side effects under preview) → `getOrgContext` → **`if (role !== "org_admin" && role !== "preparer") → FORBIDDEN`** → report fetched **`.eq("public_id").eq("org_id", orgId).is("deleted_at", null)`** (own-org, else NOT_FOUND) → **write-once** (`deposit_reference` set → CONFLICT) → **from-state** (`status ∈ {draft,preparer_signed}` else CONFLICT) → write via **service_role RPC that re-checks org+role under `FOR UPDATE`**. So the list gate is UX-only/defense-in-depth: a stale mirror can at worst show a link the server rejects (FORBIDDEN/CONFLICT/NOT_FOUND) — not a safety hole.

**2) Viewer never — CONFIRMED.** `page.tsx` loader: `canPrepare = role === "org_admin" || role === "preparer"` (viewer *and* cashier → false); fail-closed on membership error/null (redirect). The action renders only under `canRecordDeposit(canPrepare, r)`, so a viewer never sees it. Test: "HIDES it entirely for a viewer (canPrepare=false)".

**3) Out-of-order — CONFIRMED.** The gate and the `{public_id}#bank-deposit` link are per-row inside `reports.map`, independent of period ordering. Test "an OLDER actionable report still gets its own bank-in link" asserts both `pub-old` (2026-07-01) and `pub-new` (2026-08-15) yield distinct working links.

**4) Nothing weakened — CONFIRMED.** The only detail-file change is the anchor `id`/`scroll-mt` on the deposit div; endorse/close/dual-control/slip-upload/deposit close-gate logic is untouched (single-hunk diff). Server guarded flow unchanged.

**5) Keluarga/umum-only by construction — CONFIRMED.** Loader `listWeeklyReportsAction` reads `tabung_weekly_reports` (org-scoped, `deleted_at IS NULL`) with **no explicit scope filter** — and needs none: mig060 defines `scope TEXT NOT NULL CHECK (scope IN ('keluarga','umum','both'))`, so a `jumaat` scope is **not representable**. Jumaat reports live in a *separate* table `tabung_jumaat_reports` (mig124, created deliberately "rather than a scope on tabung_weekly_reports" so the weekly enum stays untouched). The weekly-reports list therefore cannot contain a Jumaat report by table structure + CHECK constraint.

## Verdict
**PASS** — no merge-blocker, no findings. UX-only client mirror over an unchanged, correctly-enforcing server boundary; scope/viewer/out-of-order/no-weakening all hold at source. Independently confirms orch-console's opus-4-8 pass. Client-only change (no DB) → merges as code; no §6.6 apply.
