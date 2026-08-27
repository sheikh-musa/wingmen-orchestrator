# PR #427 / mig219 — void↔unvoid fund_raised symmetry + D1/D2 audit-write fixes — cc-quality FULL money-path audit

**VERDICT: PASS** (committed-money, CAI-1170) — with ONE apply-time condition (transition-window, below). This is the hardening of the 3 LOW findings from my own #355 audit. I remain propose-only — did NOT apply or wet-prove.
**Auditor:** cc-quality — model confirmed **opus-4-8** (`.quality_model`, CAI-1170 carve-out).
**Date:** 2026-08-22 · **Head:** a7a7820e (off main incl #355/mig202) · **Dispatch:** orch-console #31192 (P2).
**Sequence (CAI-1264/1265):** my PASS → console wet-proves mig219 BOTH silos → cai literal `execution_status='granted'` naming mig219 → apply both silos + merge.

## (a) fund_raised void↔unvoid symmetry — money-correct (by record-and-replay)
mig202's void decrement and unvoid re-increment each independently re-evaluated the live gate (`category_type='tabung' AND fund_target truthy`); if the category is reconfigured between a void and its later unvoid, the two evaluations diverge → silent phantom decrement or increment (the exact D3 edge I flagged). Fix:
- **void_donation**: after the (unchanged) decrement UPDATE, `GET DIAGNOSTICS v_fund_row_count = ROW_COUNT` (the mig028/097 idiom) captures whether it fired; the guarded UPDATE now also sets `fund_raised_reversed = (v_fund_row_count > 0)` — same statement, no new race window.
- **unvoid_donation**: re-increment now gated on `IF d.fund_raised_reversed THEN … END IF` (the donation's OWN frozen flag, WHERE id+org_id only — no live-gate re-derivation), and resets the flag to NULL on unvoid. → exact replay, symmetric regardless of category reconfiguration. ✅
- **Byte-identical check**: extracted void/unvoid from mig202 vs mig219 and diffed — the ONLY deltas are the documented fund_raised_reversed capture/replay/reset (+ whitespace). All decrement/TOCTOU/receipt/zakat/hash-audit logic untouched. No smuggled drift.

### ⚠ APPLY-TIME CONDITION (transition-window NULL edge — LOW, not a blocker)
A donation voided under mig202 (already live) but not yet unvoided has `fund_raised_reversed = NULL`. In plpgsql `IF NULL THEN` is false, so unvoiding such a row post-mig219 would SKIP the re-increment → under-reversal if the void had decremented. The migration's "NULL means never-voided for every existing row" is false for a *currently-voided* row. **Verified empirically: 0 voided donations on BOTH silos right now** (`voided_at IS NOT NULL` count = 0 on goumlyne AND ceayj), so the edge is currently unreachable and "no backfill" is correct. **Condition:** console should re-confirm `SELECT count(*) FROM donations WHERE voided_at IS NOT NULL` = 0 on both silos immediately before applying (a void could land in the audit→apply window). If 0, provably safe for all existing rows (rows voided after mig219 get the flag correctly). If >0, backfill those rows' flag first.

## (b) mig219 additive / order-independent / no privilege change — PASS
`ALTER TABLE donations ADD COLUMN IF NOT EXISTS fund_raised_reversed BOOLEAN` (nullable, additive). `CREATE OR REPLACE` void_donation/unvoid_donation with identical signatures → grants carry, no privilege change. New column + fn-replace depend on nothing in 203–218 → order-independent. Number 219 collision-free (main highest 216; 217=#423, 218=#426 unmerged; only #427 claims 219). Residency: BOTH silos correct (donations is CORE-shared; both silos carry donations + the mig202 functions). Genesis row standard convention; self-committing BEGIN/COMMIT (console strips per CAI-756).

## (c) D1/D2 fixes sound + no money-logic regression — PASS
- **D1** (discarded writeAuditLog): reissue now does `const auditRes = await writeAuditLog(...)` + `if (auditRes.error) captureActionError(...)` — a failed audit write is surfaced (Sentry, with returnedError+createdId+voidId), never silently swallowed, and still does NOT fail the already-created reissue (best-effort post-hoc record; the durable `replaced_by_donation_id` linkage remains the fail-closed protection — CAI-1250 principle). Exactly my finding's recommendation. **Mutation-proved**: removing the surfacing block turned the D1 test RED.
- **D2**: reissue audit payload now includes `donated_at: r.donated_at ?? null` (present + null cases both tested).
- **7 small app-file edits** (bank-import, dashboard-kpis, dashboard, data-export, onboarding-checklist, platform-settings, super-admin): ALL are `// schema-drift-ignore: fund_raised_reversed` comment annotations — the required check-schema-drift ripple of adding a donations column. Comment-only, zero logic change.
- **2 regenerated lint baselines** (pagination, action-error-capture): entry counts UNCHANGED (349→349, 108→108); the +/- churn is pure `file:line` renumbering in exactly the annotated files (comments shifted line numbers). No new violation grandfathered — confirmed by lint:all reporting 0-new on both.

## Gates (at a7a7820e)
- **lint:all EXIT 0** — 16/16 (schema-drift 0-new, pagination 0-new, action-error-capture 0-new, money-float clean on 169 migs). **vitest 25/25** (donations-void-correction incl. D1/D2 tests). Mutation-prove passed (D1). D3 SQL symmetry is not JS-mockable (tests honestly note this) → console wet-proves both silos.

## Findings
1. **LOW / apply-time condition** — transition-window NULL edge (above). Re-confirm voided-count=0 both silos at apply. Verified 0/0 at audit time.

**Bottom line: PASS. Wet-prove both silos (esp. the fund_raised replay symmetry + a void→reconfigure-category→unvoid case), re-confirm 0 voided donations at apply, then the CAI-1264/1265 cai-grant gate.**

---
## DELTA-CONFIRM post-rebase (2026-08-22) — PASS, verdict preserved
#427 rebased onto current main (behind #428's 10 writeAuditLog sites + #430/mig220 bank-import) by hand from clean git-show. Head 704640f1. Delta-confirmed (cc-irsyad-coord #31273):
- **mig219 BYTE-IDENTICAL** to the pre-rebase PASS head (a7a7820e) — 341L, diff empty.
- **All 11 writeAuditLog sites captured** in donations.ts; #427-net over main = ONLY the reissue (11th) D1 capture + D2 donated_at (extras `{returnedError, createdId, voidId}` — PII-clean), byte-identical to what PASS #31199 verified. #428's 10 intact (inherited from main, untouched).
- **Lint baselines = clean UNION** (not stale pre-#428): pagination 349/349 + action-error-capture 108/108 totals unchanged vs main; every changed entry is a line-number shift within #427's own schema-drift-ignore-touched files (dashboard-kpis/onboarding-checklist/super-admin/…); identical FILE set (no out-of-scope change, no drop, no carryover). lint:all EXIT 0 (pagination/action-error-capture/schema-drift all 0-new) empirically confirms the baseline matches the code.
- **_reserved.txt** 219 (#427) + 220 (#430, applied both silos) both present; **bank-import.ts** = #430's reword + #427's 3 schema-drift-ignore comments (no clobber).
- vitest 25/25.
**Verdict PASS #31199 HOLDS post-rebase.** Apply-time condition still stands: re-confirm 0 voided donations on both silos immediately before applying mig219 (transition-window NULL edge). Console wet-proves both silos → cai grant (CAI-1264) → apply.
