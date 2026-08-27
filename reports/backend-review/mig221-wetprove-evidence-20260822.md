# mig221 (merge_persons unclassified-FK classification) — wet-prove evidence 2026-08-22

PR #434 @63189ae5 (CAI-1273 + CAI-1277 fold). cc-quality FULL opus PASS (31412/31413).

## CAI-756-safe wet-prove (scratchpad/wetprove_mig221.sql)
Stripped the mig's single top-level `BEGIN;` (L84) + `COMMIT;` (L517), wrapped in my own
BEGIN..ROLLBACK, psql ON_ERROR_STOP=1. No `\i`, no inner COMMIT (only escape hazard checked).

## Result — BOTH silos applied clean in-txn, verified, rolled back:
- op_check widened: CHECK op IN (reparent,role_softdelete,skip_unique,consent_propagate_revoke,**resolve**)
- merge_persons: has_appr_reparent=t, touches_candidates=t, has_proposed_filter=t (all silos)
- reverse_merge: has_resolve_restore=t
- ROLLBACK confirmed on FRESH conn BOTH silos: has_resolve=**f**, op_check at OLD baseline (unchanged).

## Residency
- donation_appreciation_letters.person_id -> REPARENT: goumlyne-only-live (ceayj lacks table; no-op there).
- person_merge_candidates.{a,b} -> SPECIAL resolve: both silos, 0 rows today.
- Pure function-classification; no data migration, no column/table/RLS change.

## Gate: grant requested (cai 31418, CAI-1264). Apply BOTH silos + merge #434 + deploy on cai's literal grant.
