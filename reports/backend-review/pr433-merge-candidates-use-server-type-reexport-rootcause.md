# PR #433 — merge-crash ROOT-CAUSE fix (use-server type re-export → SSR module-eval ReferenceError) — cc-quality diff-review

**VERDICT: PASS** — merge + deploy both silos. Export-mechanics only; minors-exclusion (CAI-1199/1222) byte-identical, zero behavioral change. Root-cause fix is complete repo-wide. The definitive runtime close is console's post-deploy prod-log check (0×500 on /duplicates AND /merge).
**Reviewer:** cc-quality (opus-4-8) · **Date:** 2026-08-22 · **Head:** 6476e719 · **Dispatch:** cc-irsyad-coord #31321 (P1, client waiting).

## (1) Export-mechanics only + minors-exclusion byte-identical — PASS
- The diff DELETES `export type { MergeCandidate, MergeCandidateFlag, MergeTier, DetectPersonRow };` (the bug) from the `"use server"` module `merge-candidates.ts`, adds a cautionary header comment, and repoints the 3 consumers (duplicates-client.tsx, merge-candidate-queue.tsx, merge-candidates.test.ts) to import the 4 types from `@shared/lib/merge-candidates-core` (where they are defined — L15/18/29/42).
- **Logic diff is EMPTY**: filtering the merge-candidates.ts diff to non-import/export/comment lines yields nothing — computeMergeCandidates + the detect/list/flag/resolve action bodies are byte-identical. `merge-candidates-core.ts` is NOT in the PR (untouched). So the minors-exclusion (CAI-1199/1222) logic is unchanged.
- `check-minors-exclusion-fail-closed` lint gate: PASS. `tsc --noEmit`: clean on all changed files (the repointed imports resolve). vitest merge-candidates: 10/10.

## (2) The "real proof" (build-clean is inconclusive) — SOURCE-LEVEL PROOF is decisive; compiled-output re-run INCONCLUSIVE (reported honestly)
- **Decisive source proof:** at head, `merge-candidates.ts` exports ONLY 4 `export async function` (detect/flag/list/resolve) — NO `export type`, no value re-export. A use-server module with only async-function exports has no type identifier that Turbopack could emit as a bare runtime `registerServerReference`. The bug construct is structurally gone.
- **Completeness sweep (my addition):** across ALL 97 `"use server"` modules in src, ZERO have an `export type { X }` re-export statement (the only matches are the new cautionary JSDoc examples in this file's header). So merge-candidates.ts was the sole instance; **no latent siblings** will 500 other routes.
- **Compiled-output re-run — could-not-corroborate (not a finding):** I built the fixed head (real `npm ci` + `next build`, EXIT 0): 0 `registerServerReference(<type>)` / `ensureServerEntryExports([...<type>...])` in executable SSR code (the only 19 bare `MergeCandidate` tokens are in `.map` sourcemaps, never executed). BUT when I reintroduced the re-export and rebuilt (contrast), the bug signature did NOT reappear in the production build either — consistent with coord's flag that a production `next build` is INCONCLUSIVE for this runtime bug. So I do NOT rely on the fixed-build "0" as independent proof, and could not reproduce the builder's WITH-bug compiled-output contrast via a production build. The builder's method (grep .next/server/chunks for the bare-identifier server-ref) is sound in principle; on a production build it did not discriminate for me. **The verdict rests on the source proof above; the real runtime confirmation is console's post-deploy prod-log 0×500 check.**

## (3) merge-persons.ts MergePreview — not a sibling — PASS
`export type MergePreview = { ... }` (L33) is a LOCAL type-alias DECLARATION (fully erased at compile, no imported binding to mis-resolve), NOT an `export type { X }` re-export. Not the same bug class; correctly untouched. Confirmed by the repo-wide sweep (no use-server module has the re-export form).

## Gates (at 6476e719)
- tsc clean (changed files) · lint:all EXIT 0 (incl. check-minors-exclusion-fail-closed) · vitest 10/10 · fixed `next build` EXIT 0.

**Bottom line: PASS — merge + deploy both silos.** Source-level proof is decisive and the fix is complete repo-wide; the definitive runtime close is console's prod-log 0×500 on /duplicates AND /merge. (Note for the record: a production `next build` does not surface this runtime bug in grep-able form — build-clean is not proof either way, exactly as coord flagged.)
