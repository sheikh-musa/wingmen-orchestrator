# FULL audit — PR #414 letter reference → top-right + smaller signature (op#15546)

**Auditor:** cc-quality (FULL, no-self-merge) · **Date:** 2026-08-21 · **Verdict: PASS, merge-ready.**
Requested by orch-console (bus #30666, thread `b9517526`). Styling-only on the appreciation-letter PDF. Verified at source + tests + mutation.

Pinned HEAD `e1a18a0c3847b160759b19d9d54f8ed93d6bf38b` (= `gh pr view 414`, MERGEABLE, base `main`).
2 files: `appreciation-letter-pdf.tsx` + its test. **No migration.** Gates: **lint:all EXIT 0** · **letter-pdf 6/6 on head**.

## Diff (styling only)
- `receiptRef` style `{marginTop:6, marginBottom:6, …}` → `{textAlign:"right", marginBottom:14, …}`; the `receiptRef` `<Text>` block is **byte-identical**, just **relocated** from below the donations block to just under the letterhead **before** the date (top-right header). The non-null guard `data.receiptReference ? <Text> : null` is preserved.
- `signatureImg` 120×45 → 100×38 (aspect 2.63, ~unchanged).

## Confirm items
1. **No compliance-text drift.** Neither `appreciation-letter-data.ts` (zakat constant/flag/`resolveZakatAllocationNotice` + `nonDeductibleNotice` passthrough) nor `non-deductible-notice.ts` (the CAI-1229 tax notice text) is in the diff — both byte-untouched. In `pdf.tsx` the tax-notice render (`data.nonDeductibleNotice ? …`, line 175) and the zakat-allocation render (`data.zakatAllocationNotice ? …`, line 148) are **unchanged** by the diff (the relocated block is `receiptRef`, not either compliance line). **Correction to the brief:** the brief says "zakat stays flag-gated OFF" — at this head `ZAKAT_ALLOCATION_LINE_LIVE = true` (the #411 / CAI-1242 flip is present), so the zakat line is **LIVE**, not OFF. This PR changes **neither** the flag nor the render gate, so the zakat behaviour is byte-untouched by #414 regardless — but the record should say LIVE, not OFF.
2. **Ref omit-guard holds.** The relocated block keeps `data.receiptReference ? <Text style={s.receiptRef}>{data.receiptReference}</Text> : null` — no blank ref when `receiptReference` is null. Test "omits the reference entirely when none is present" confirms.
3. **The 3 position tests are mutation-meaningful.** New `collectTexts` DFS helper collects every `<Text>` with merged style in document order.
   - *renders a valid PDF when a ref is present* — smoke (`%PDF-`).
   - *places the reference right-aligned and BEFORE the date* — asserts `texts[refIdx].style.textAlign === "right"` AND `refIdx < dateIdx`. **Mutation-proven:** swapping in the pre-#414 `pdf.tsx` (ref BELOW the date, no `textAlign`) turns **exactly this test RED** (`1 failed / 5 passed`) — it genuinely verifies the relocation + right-alignment.
   - *omits the reference when none present (guard preserved)* — asserts no `"Reference: Receipt No."` text when `receiptReference` is absent; guards against dropping the guard / hardcoding the ref.

## Verdict
**PASS.** Pure presentational change: `receiptRef` right-aligned + moved to the top-right header, signature 100×38; compliance text (tax notice + zakat line) byte-untouched, omit-guard preserved, position tests mutation-meaningful, lint green, no migration. Client-only → merges as code; no §6.6. Routing to orch-console for merge (no self-merge). Note for the record: zakat line is LIVE (per #411), not OFF — unchanged by this PR.

---

## DELTA re-confirm — head `e1a18a0c` → `64fa5691` (numbering-drop bundle) — bus #30694, thread `08d84804`
Combined head after my PASS. **Delta = numbering-drop only** (`git diff e1a18a0c 64fa5691` = `appreciation-letter-pdf.tsx` + its test; no migration, no compliance-source file). **DELTA verdict: PASS.** 44/44 letter surface + lint:all EXIT 0 on `64fa5691`.

- **CRITICAL re-confirm — LIVE zakat block + tax notice byte-untouched (independently verified).** Precise byte-check on the delta's +/- lines: **`zakatAllocationNotice`, `nonDeductibleNotice`, `receiptRef`, `textAlign`, `width:100` are absent from every +/- line.** The zakat conditional `{data.zakatAllocationNotice ? <Text style={s.para}>{data.zakatAllocationNotice}</Text> : null}` and the tax-notice block appear only as unchanged CONTEXT — byte-identical. My prior ref-top-right + signature-100×38 changes are likewise untouched.
- **Numbering-drop is clean.** Removed `numRow/numCol/numText` styles + the `Numbered` wrapper; the 3 paragraphs (`n=2/3/4`) → plain `<Text style={s.para}>` with **byte-identical prose**. Rationale (comment): the numbered treatment rendered the list starting at "2" (item 1 was missing), coord/console ruled to drop numbering → prose. Presentational only.
- **New tests — the regression is guarded (mutation-proven).** "previously-numbered paragraphs still render as prose" + "no bare-number `<Text>` node". Mutation: swapping the pre-delta (numbered) `pdf.tsx` back in turns the **prose-render** test RED (`1 failed / 7 passed`) — it genuinely catches a `Numbered` reintroduction. *Minor observation (non-blocking):* `collectTexts` walks the un-rendered element tree and does NOT expand the `Numbered` function component, so the "no bare-number" test would not independently catch a `Numbered` regression (the bare `n` lives as a prop, never a `<Text>` at tree level) — the prose-render test is the effective guard. Harmless; noted for accuracy.

**Delta bottom line:** numbering-drop is presentational; the LIVE zakat block + tax notice are byte-untouched (independently confirmed), ref/sig unchanged, regression mutation-guarded by the prose test, 44/44 + lint green, no mig. Still PASS — merge-ready.
