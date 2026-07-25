# WP-C #35 — rebase + conflict-resolve + re-test brief (cc-ihsanos)

**From:** cc-orchestrator (hub) · **Op:** WP-C NETS/cheque #35 · **Authorized:** operator #4954 (merge), Nazim #9348 (parallel prep GO)
**Date:** 2026-07-17

## Your job (AUTHORED ONLY — do NOT merge, do NOT apply, do NOT push to main)
PR #35 (`feat/irsyad-wp-c-nets-cheque-tenders`) is 4 weeks stale — its base is ~205 commits behind `origin/main`, so GitHub reports it `mergeable_state=dirty` with real conflicts in POS money-logic files. Rebase it onto current `main`, resolve the conflicts correctly, and re-prove it green. The hub then gates (fresh cc-reviewer money pass + cai §6.6 + residency verify) and does the irreversible merge/apply itself.

## Where you are
- Worktree: `~/wingmen/ihsanos-wt/wpc-rebase`
- Branch: `feat/irsyad-wp-c-nets-cheque-tenders-rb` (a fresh copy of the PR tip 87b67c2; the original PR branch stays intact as a safety net — do not touch it).

## Steps
1. `git rebase origin/main` (in the worktree). Expect conflicts in these "changed in both" files:
   - `src/actions/pos.ts` — **the sale-commit action; MONEY-CRITICAL.** Resolve so BOTH the WP-C nets/cheque tender handling AND whatever main added since June are preserved.
   - `src/shared/lib/schemas.ts` — payment_method enum/validation.
   - `scripts/lint/migration-tracker-baseline.json` — migration registry; keep BOTH sides' entries (main's newer migrations + 071). 071 must remain registered known-unapplied.
   - `src/modules/pos/api.ts`, `src/app/dashboard/pos/{payment-dialog,close-session-dialog,pos-receipt}.tsx`, `src/actions/__tests__/pos-create-transaction.test.ts`, `src/shared/types/index.ts` — reconcile with current main.
2. **Preserve the WP-C invariant (CAI-RESP-260 D3):** a sale is NEVER split across nets/cheque + another tender. nets/cheque are single-tender: full `total` is the tender amount, `cash_amount`/`paynow_amount` stay 0, so `expectedCash` cash-drawer reconciliation is UNAFFECTED. Tender reference (cheque no / NETS code) folds into the existing `remarks` field — **no new column.**
3. Migration `071_pos_tender_nets_cheque.sql` is a CHECK-only extend (adds `'nets','cheque'`, preserves `'cash','paynow','split','paywave','cdc_voucher'`). It should NOT need conflict resolution (new file) — just confirm it survives the rebase intact and stays registered known-unapplied. **Do NOT apply it.**
4. Re-run and confirm GREEN, pasting real output:
   - The 2 WP-C sale-commit tests (NETS + Cheque) in `pos-create-transaction.test.ts`.
   - Full unit suite + `tsc` (0 errors) + lint (0).
   - The **production build** (`next build`) — local test-green ≠ deployable (see the deploy-gate lesson). Confirm it builds.
5. **Do NOT** `git push` a merge, do NOT merge to main, do NOT run any migration apply. Commit the rebased branch locally (the launcher auto-pushes YOUR branch `-rb` on exit — that's fine, it's a feature branch, not main).

## Report back (to cc-orchestrator, via agent_messages)
- Rebased branch name + final SHA.
- Conflict resolution summary: for pos.ts and schemas.ts, exactly what each side contributed and how you reconciled (the hub + reviewer will scrutinize this).
- Test output: NETS/Cheque cases + full-suite counts + tsc/lint + `next build` result — REAL pasted output, not a summary claim.
- Anything that changed semantically vs the original PR because of main's drift (esp. anything touching money math or payment_method handling).

**Trust nothing unverified — the hub re-runs your tests before gating.** Money path: correctness over speed, but move fast.
