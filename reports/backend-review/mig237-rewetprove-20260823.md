# mig237 (FIXED) re-wet-prove — tabung_preparer_sign_report → +subadmin

**Reviewer/applier:** orch-console (Nazim), opus-4-8. **Date:** 2026-08-23. **PR:** #483 head e1adc7d6 (cc6 re-derived).
**Residency:** goumlyne ONLY (fn absent ceayj). **Grant:** pending cai (adversarial re-verify + this re-wet-prove).

## Why re-derived
The first mig237 was derived from mig133 alone; live goumlyne fn = mig133 + mig197(CAI-1107) + mig230(CAI-1297). The naive CREATE OR REPLACE silently STRIPPED the completeness guard (report_incomplete_tin_scope) for all callers — cc-storefront caught it, cai confirmed (CAI-RESP-1310). cc6 re-derived from the LIVE composed body. Lesson banked: [[feedback_wetprove_create_or_replace_diff_body_vs_live]].

## Re-wet-prove result: ALL PASS 10/10 (goumlyne, BEGIN..ROLLBACK)
**Body-diff vs LIVE pg_get_functiondef (difflib, normalized):**
- Completeness guard FULLY PRESERVED (v_expected_kk/v_got_kk/v_expected_umum/v_got_umum + report_incomplete_tin_scope RAISE, both scope branches).
- NO guard/control line removed; removals are ONLY the pre-delta role/displacement lines (the -old of modified lines).
- Additions are ONLY the 3 intended subadmin deltas (role +subadmin; scope=umum backstop block; subadmin in the v_fallback=false displacement branch — fallback-trigger query left preparer-only).

**Behavioral (SECDEF call, real test data, subadmin membership in-txn):**
- REGR scope backstop: subadmin sign umum OK; keluarga + both REFUSED (subadmin_scope_not_permitted).
- NEW NEGATIVE (cai-required): subadmin AND org_admin signing an INCOMPLETE umum report (1 banked umum tin in-period, empty snapshot) BOTH RAISE report_incomplete_tin_scope ("umum expected 1 got 0"). Guard holds for every caller.
- POS-with-guard: complete snapshot (tin included) signs OK — guard doesn't false-block.

## Gate
Adversarial re-verify (cc-storefront, 32667) in-flight → on its PASS + this, cai §6.6 grant for 237_subadmin_weekly_sign_rpc.sql (goumlyne-only) → apply → #483 merges (mig238 already applied both silos).

## APPLIED
2026-08-23: APPLIED goumlyne-only on cai grant CAI-RESP-1313. Apply-time body-diff-vs-live re-check clean (guard preserved, no control removed); post-apply verified fresh-conn (subadmin role + scope backstop + report_incomplete_tin_scope guard present); ceayj residency confirmed (fn absent). Piece-2 COMPLETE (mig237 goumlyne + mig238 both silos). #483 PR-merge on CI-green (studio wake).
