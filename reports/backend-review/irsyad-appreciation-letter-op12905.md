# cc-quality review — appreciation-letter data-builder (op#12905, FRS §3.9)

**Verdict: ✅ PASS** — pure, correct, well-tested, correctly scoped (letter-only), and dormant/safe. No code bugs. Two accepted-on-default follow-ups endorsed with notes (neither blocks; both are already correctly designed for the swap).

- **Reviewer:** cc-quality (Head of Quality) — re-routed from the dead cc-reviewer (orch-console bus 20966; original coord cc-irsyad→cc-reviewer #20013). Standing in as independent code reviewer.
- **Scope:** LETTER side only (cc-irsyad-b owns the Online Receipt on the same branch — excluded per coord).
- **Commit:** `9f8ed4d` (ihsanos), on `origin/feat/irsyad-appreciation-letter` — purely additive: 2 new files, no edits to existing PDF/email/receipt scaffolding. Not in main (dormant feature, expected).
  - `src/modules/tabung/letter/appreciation-letter-data.ts` (261 lines, pure)
  - `src/modules/tabung/letter/__tests__/appreciation-letter-data.test.ts` (16 tests)
- **Reviewed (UTC):** 2026-08-14T09:16:25Z
- **Method:** verify-not-assert — read both files in full, then RAN them: 16/16 tests pass and `tsc --noEmit` = 0 project errors (both executed in a temp worktree at 9f8ed4d with a real node_modules; the letter worktree itself has none installed).

---

## Verification

| Item | Result | Evidence |
|---|---|---|
| Tests pass | ✅ | `vitest run …/appreciation-letter-data.test.ts` → **16 passed** (executed, not asserted). Passing also proves the `.tsx` scaffolding imports resolve. |
| Type-clean | ✅ | `tsc --noEmit` → **0 errors** project-wide (0 in the letter files) — the returned `AppreciationLetterData` / `DonationDocEmailInput` / `DonationLine` structurally match the existing `.tsx` types. |
| Pure / safe | ✅ | No DB, no I/O, no `Date.now()` — `letterDate` is injected, so output is deterministic. Confirmed by reading (no db/fetch imports). |
| Honorific (CAI-854) | ✅ | `male→Tuan`, `female→Mdm`; `""`/`null`/`undefined`/junk → `null` → caller uses the bare display name, never guesses a title. Tested for all four null-cases. |
| Amount formatting | ✅ | bare `$` per the client sample (deliberately NOT the app's `S$`), Intl grouping; non-numeric → `$0.00` (not `$NaN`) — tested. |
| Address / dates | ✅ | `splitAddress` (newlines→commas→trim→drop-empty, null→[]); Gregorian / 2-digit fund date per sample — tested. |
| Seam ownership | ✅ | `buildLetterDocEmailInput` refuses `bank_transfer` at BOTH the type level (`Exclude<DonationSource,"bank_transfer">`) AND a runtime `throw` — respects the cc-irsyad-b Online-Receipt boundary. Tested (`@ts-expect-error` + throw). |
| DORMANT / gated | ✅ | Only assembles the input object; never calls `sendDonationDocEmail`; `DOC_EMAIL_LIVE_SEND` stays false; no migration. Nothing client-facing ships from this change. |

The 16 tests are substantive (edge cases: all honorific null-forms, `$0.00` degradation, address split variants, opt-out/already-sent passthrough, bank_transfer refusal) — not hollow.

## Two accepted-on-default follow-ups (endorsed; non-blocking)

1. **Hijri: Umm al-Qura (ICU `islamic-umalqura`) vs MUIS-exact.** The builder emits `"11 Zulkaedah 1447H"` where the client's own hand-written sample showed `"10 Zulkaedah"` for the same day (documented ±1-day umalqura-vs-MUIS difference; **month + year agree**). Umm al-Qura is the defensible programmatic standard, it's documented on the function, and swapping to exact MUIS tables is a one-function change. **Endorse the default** — but this is a client-VISIBLE 1-day discrepancy from their own sample, so before the letter goes client-facing, confirm the exact Hijri day with the client / MUIS and swap if needed. Safe to land now (dormant, nothing rendered to a client yet).

2. **Default signatory hardcoded** (`"Ustazah Warintek Ismail"` / `"Principal"`, from the sample). The builder is correctly **overridable** (`signatoryName?`/`signatoryTitle?`) and documents the intent that live wiring source these from org config. **Endorse** — the follow-up lands on the WIRING: the live caller must pass org-config values so a signatory change is one config edit, not a code change. Builder itself is correct.

## Bottom line

A clean, pure, deterministic data-builder that turns a donor Person + donations + issue date into the presentational letter data and the (gated, dormant) email input — faithful to the client sample, CAI-854-correct on honorifics, seam-respecting on bank_transfer, and safe (no I/O, no live send). 16/16 tests green + tsc clean, both verified by execution. **PASS.** The two decision points are sound defaults with clean swap paths; fold the Hijri client-confirmation and the org-config signatory wiring into the live-wiring follow-up.

— cc-quality
