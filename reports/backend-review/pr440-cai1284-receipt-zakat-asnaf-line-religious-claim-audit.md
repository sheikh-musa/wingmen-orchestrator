# PR #440 — CAI-1284/1285 receipt zakat-Asnaf line (DARK) — cc-quality religious-claim audit

**VERDICT: CONDITIONAL PASS.** Mechanism (dark-gate / category-scope / platform-flag / substantiation-trace) all correct and tested. **ONE required fix before the flag flips (point 1 byte-drift):** the receipt string is missing the trailing period the LIVE letter already renders. Merging DARK is safe; do NOT flip the receipt flag live until the strings byte-match. I remain propose-only; console holds the flag-flip.
**Auditor:** cc-quality (opus-4-8) · **Date:** 2026-08-22 · **Head:** edeb09ba · **Dispatch:** cc-irsyad #31572 (P2, religious-claim review).

## ⚠ POINT 1 (WORDING / byte-consistency) — DRIFT FOUND (required fix before flag-flip)
The dispatch requires the claim be byte-consistent across receipt + letter (no drift from the CAI-1242 letter line).
- **Receipt (#440)** `pdf-template.tsx:254`: `"Zakat contributions are channeled to 4 Asnaf categories: Miskin, Riqab, Amil, and Fisabilillah"` — **NO trailing period.**
- **Letter (LIVE in main)** `src/modules/tabung/letter/appreciation-letter-data.ts:168` (PR #408, MERGED 2026-08-21; flag `ZAKAT_ALLOCATION_LINE_LIVE = true` — the letter renders this to donors TODAY): `"Zakat contributions are channeled to 4 Asnaf categories: Miskin, Riqab, Amil, and Fisabilillah."` — **WITH trailing period.**
⇒ The same religious claim would render with a period on the live appreciation letter and without one on the receipt — the exact cross-document byte-drift this audit guards against. The #440 tests validate the receipt string against itself (can't catch this). **FIX: align to ONE byte-identical string** — recommend the receipt ADD the trailing period to match the already-live letter (`appreciation-letter-data.ts:168`); if cai instead rules the no-period form canonical, the LETTER must be updated too. **Either way, resolve before console flips the receipt flag live.** (Canonical-form choice is a wording/religious-claim call → console/cai; low-risk default = receipt conforms to the live letter.)

## Point 2 (category-conditional scope) — PASS
`resolveZakatAsnafNotice(categoryType, flagLive) => categoryType === "zakat" && flagLive ? NOTICE : null`. Non-zakat categories return null EVEN IF the flag were live → an asnaf-distribution claim can never render on a non-zakat receipt (CAI-1234§2b false-claim prevention). Tested: negative cases for tabung/sadaqah/infaq/qurban/other + null/undefined all → null. **Mutation-proved**: dropping the `categoryType==='zakat'` gate reddened 6 tests.

## Point 3 (render-flag platform-controlled, false-at-rest) — PASS
`export const ZAKAT_ASNAF_LINE_LIVE = false as boolean;` — a PLATFORM build-time const in pdf-template.tsx, passed as the `flagLive` arg to the pure resolver. NOT a client-editable org_setting/JSONB, not read from any DB/org row — no admin UI can toggle a religious-compliance claim. Tested false-at-rest (`expect(ZAKAT_ASNAF_LINE_LIVE).toBe(false)`). Console flips + deploys. Mirrors the letter's `ZAKAT_ALLOCATION_LINE_LIVE` mechanism (CAI-1242).

## Point 4 (substantiation trace) — PASS (advisory confirm, not re-adjudicated)
The render decision traces to CAI-1242 (the letter's zakat-allocation line — flag now LIVE in main, the DB-verified Riqab substantiation source per the dispatch: Saddam's words, authority DB-verified, MUIS zakat.sg cross-check CAI-1236) + CAI-1285 (cai ruled render all 4 asnaf). The code comments cite CAI-1284 / CAI-1234§2a-b / CAI-1242. As advisory I confirm the traceability chain exists (not today's moot Niffira exchange, CAI-1238); the religious substantiation itself is cai's domain (already ruled CAI-1285), not re-adjudicated here.

## Gates (at edeb09ba)
- lint:all EXIT 0 · vitest 11/11 (flag-false-at-rest, exact-wording, category gate incl. all negative cases, dark-render no-crash) · mutation-proved the category gate. Route wiring passes `categoryType: category?.category_type ?? null` (session/RLS fetch, no new PII exposure — the select adds only category_type). No migration, no money/PII mutation.

**Bottom line: CONDITIONAL PASS. The dark-gate, category-scope, platform-flag, and substantiation-trace are all correct, tested, and mutation-proved. The one blocker to going LIVE is the trailing-period byte-drift vs the already-live letter (point 1) — merge dark is fine, but the receipt string must byte-match the letter before console flips the flag. Verdict to cc-irsyad + console.**

---
## RE-CONFIRM — byte-fix @12cad867 — PASS (drift resolved, cleared for flag-flip)
cc-irsyad pushed the byte-fix (#31584). Verified:
- Receipt string @12cad867 = `"...Miskin, Riqab, Amil, and Fisabilillah."` — now byte-identical to the live letter (`appreciation-letter-data.ts` ZAKAT_ALLOCATION_NOTICE, trailing period). BYTE-MATCH ✓.
- Delta edeb09ba→12cad867 is ONLY the trailing period + test updates + comment — no other change rode the fix.
- **Regression guard added (better than asked):** a new test imports the live letter's `ZAKAT_ALLOCATION_NOTICE` and asserts `resolveZakatAsnafNotice("zakat", true) === ZAKAT_ALLOCATION_NOTICE` — permanently catches any future cross-document drift (closes the gap where the self-validating suite couldn't). vitest 12/12 green.
- All other audit points (category-gate/platform-flag/substantiation-trace) unchanged and stand.
**Conditional resolved → full PASS.** Cleared for merge-dark + console flag-flip.
