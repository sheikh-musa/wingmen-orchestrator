# cc-quality review — ihsanos PR #304: RCP header overlap fix (op#13177)

**Verdict: ✅ PASS (approve for merge).** Correct, complete flexbox remedy applied to both templates; low-risk (pure PDF layout, no money/RLS/migration/PII); honestly-tested. No findings.

- **Reviewer:** cc-quality — re-routed shipforge/ihsanos-style peer review (bus 21786, cc-irsyad-receipt under cc-irsyad-coord).
- **PR:** #304 `fix/rcp-header-overlap`, OPEN/MERGEABLE, +~98/-6 across 3 files (2 templates + 1 test). CI green (lint-typecheck, unit, tabung-correctness, Vercel).
- **Reviewed (UTC):** 2026-08-15 — via the authoritative `gh pr diff 304` (my local origin/main was stale and had shown unrelated CAI-710/client-#3 lines that are NOT in the PR).

## The fix (correct + complete)
Header is a `@react-pdf` flex row (`justifyContent: space-between`) with no width constraint on the left child → a long `orgName` renders as one unwrapped line and overflows under the right-aligned RECEIPT label. Fix:
- **Left column:** `flex: 1` + **`minWidth: 0`** + `paddingRight: 16`; inner org-text `View`: `flex: 1` + `minWidth: 0`. The `minWidth: 0` is the crux — in flexbox a flex item's default `min-width: auto` won't shrink below its content's intrinsic width, so text can't wrap; setting it to 0 lets the column shrink and the orgName **wrap** instead of overflowing.
- **Right label:** `flexShrink: 0` + `alignItems: "flex-end"` — the RECEIPT label keeps its own lane, never squeezed or overlapped.
- **Applied to BOTH** `pdf-template.tsx` (RCP) and `grouped-receipt-pdf.tsx` (GROUP RECEIPT, same latent pattern) — no half-fix.

This is the textbook remedy for react-pdf/Yoga text overflow; it is correct and complete.

## Test — honest and reasonable
`rcp-header-render.test.tsx` renders the RCP via the real `renderToBuffer` across short (`BAPA`), the real go-live org, and a 61-char pathological name (the one that reproduced the overlap), asserting a valid PDF (`%PDF-` magic + length). It **documents its limitation** in-code: no `pdfjs`/`pdf-parse` dep, so it cannot machine-assert glyph positions — it guards valid-render-across-lengths (a real regression guard), and the actual overlap-gone was confirmed visually on emitted samples (`RCP_SAMPLE_OUT`). Honest, not hollow. (Optional future hardening: add pdfjs to assert the label's x-position never overlaps the org block — not required.)

## Scope / risk
Low. Pure PDF template layout — no money/RLS/migration/PII. Robustness hardening, not a live break: the real go-live org 73339164 (33 chars, no logo) already rendered fine on the old template; the overlap reproduced only on the 61-char QA org name (verified at source by the requester + coord).

## Bottom line
A small, correct, well-scoped layout fix — the right flexbox constraints (`minWidth:0` to wrap, `flexShrink:0` to protect the label), applied to both receipt templates, with an honestly-limited render regression test and green CI. **Approve for merge.** Re-run the deployed-path real-inbox E2E after deploy to confirm the corrected receipt delivers clean (as the requester plans).

— cc-quality
