# PR #428 — CAI-1250 writeAuditLog capture sweep (donations.ts, 10 sites) — cc-quality FULL audit

**VERDICT: PASS** — ship. Money-path + PII-egress file, no migration → no cai grant; FULL-audit → console's merge call. Two follow-ups (LOW), neither blocks.
**Auditor:** cc-quality — model confirmed **opus-4-8** (`.quality_model`, CAI-1170 carve-out).
**Date:** 2026-08-22 · **Head:** 450da35c · **Dispatch:** orch-console #31213 (P2). Pure app-code, no schema, 0 baseline shift.

## Console's 5 verification points — all PASS (verified at source, not from recap)
1. **Exactly 10 sites, correct D1 pattern, nothing else changed.** donations.ts has 11 `writeAuditLog` calls; **10 are captured** (`const xAuditRes = await writeAuditLog(...)` then `if (xAuditRes.error) captureActionError(...)`) at lines 121 (donation-create), 161 (receipt), 475 (inline-donor), 579 (CSV-export), 794 (cat-create), 882 (cat-update), 1004 (close-fund), 1090 (reopen-fund), 1189 (fund-target), 1273 (cat-delete). The diff contains ONLY these 10 site-hunks (+ comments) — no other behavioral change. **The 11th site — reissueVoidedDonationAction, line 2089 — is intentionally NOT in #428; it's #427's D1 fix** (separate branch, no overlap). ⇒ see coordination note.
2. **captureActionError genuinely TRANSMITS.** `src/shared/lib/action-error.ts:102` = `Sentry.captureException(err)` inside `withScope` (setTag action + setUser id + setExtra) — a real Sentry EVENT, not a breadcrumb. The CAI-1250 alert-not-just-log bar is met by uniform D1.
3. **FAIL-OPEN preserved.** Each site: the money op (donation/receipt/category/export) completes BEFORE writeAuditLog; then `.error` is read → captured → execution CONTINUES to `return {..., error: null}`. captureActionError does not throw (capture helper; the ViewAsReadOnlyError branch early-returns, never re-throws). No site now gates/blocks/rolls-back on a failed audit write.
4. **PII-egress SAFE.** Every one of the 10 `extras` objects carries only `{ returnedError, <opaque id | rowCount> }`. The CSV-export site (the critical one) carries `rowCount` only — never the CSV/donor fields. Site 3's audit *payload* carries `display_name`, but the Sentry *extras* exclude the payload, so it never reaches Sentry. `returnedError` is always `{ code, message }` where writeAuditLog sets `message: error.message` (the PostgREST top-level message — NOT the "Failing row contains …" details) and its `catch {}` is parameterless (fixed string) → no payload/PII echo path. All extras meet the "opaque ids / error strings only" bar.
5. **MUTATION-PROVED.** Reverting the export site to swallow (`.error` discarded) turned the new sweep test RED (1 failed / 8 passed). The test genuinely guards each site's capture behavior — not happy-path-only.

## Gates (at 450da35c)
- **lint:all EXIT 0** — 16/16 (schema-drift 0-new, action-error-capture 0-new, check-pii-read-role OK, money-float clean). **vitest 24/24** (donations-audit-capture-sweep.test.ts 237L + create-donation.test.ts).

## Findings (LOW — ship-with-followup, neither blocks)
1. **Coordination note (not a defect):** the CAI-1250 sweep of donations.ts is complete only when **both #428 and #427 merge** — #428 = 10 sites, #427 = the 11th (reissueVoidedDonationAction:2089). If #427 does not land, reissue remains a discarded-error site. Console: confirm both merge (or track reissue).
2. **Functional-coverage gap (pre-existing, builder-flagged):** the new sweep test covers *capture behavior* at all 10 sites, but 8/10 of the underlying actions had zero prior functional (happy-path) coverage — that gap is not introduced by #428 and not closed by it. Recommend a follow-up to add functional tests for the 8 uncovered actions. Not a blocker.
3. **LOW/theoretical (PII defense-in-depth):** `returnedError.message` is a raw DB error string. In practice safe (writeAuditLog uses `error.message` only, not the failing-row DETAIL; its catch is parameterless). A future audit_log CHECK/trigger RAISE embedding the payload could theoretically surface in Sentry — optional hardening is to ship `returnedError` as `{ code }` only for the donor-PII-adjacent sites. Not required.

**Bottom line: PASS — merge. Confirm #427 also lands so the 11th (reissue) site is covered; the functional-coverage backfill is a follow-up.**
