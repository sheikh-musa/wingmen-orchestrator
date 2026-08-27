# mig230 (CAI-1297) both-scope guard-widen — cc-quality re-verify — VERDICT: PASS

**Auditor:** cc-quality (Opus 4.8, CAI-1170 money/PII). **Date:** 2026-08-23. **PR #469, goumlyne-only.**
**Context:** closes the scope='both' completeness-guard bypass I filed in my CAI-1112 mig197 re-audit
(`reports/backend-review/cai1112-mig197-reaudit-applied.md`). cai directed "widen, don't forbid" (CAI-1297);
cai is holding the §6.6 grant on this verdict.

## Structural — verified independently (diff = EXACTLY the 2 widenings)
Normalized executable-line diff of the LIVE goumlyne `tabung_preparer_sign_report` (mig197 version, read
from `pg_get_functiondef` on the RO DSN) vs the proposed mig230 body — 134 executable lines each, only TWO differ:
- `IF rpt.scope = 'keluarga' THEN` → `IF rpt.scope IN ('keluarga', 'both') THEN`
- `IF rpt.scope = 'umum' THEN` → `IF rpt.scope IN ('umum', 'both') THEN`

**Every other line byte-identical** (normalized): caller-role check, displacement guard (CAI-668), audit-fallback
bind, draft-status gate, BOTH recount queries incl. the anti-double-count `NOT EXISTS`, the `tin_type <> 'jumaat'`
exclusion, the period bounds, `FOR UPDATE`, the freeze UPDATE, the hash-chained audit append, and the ACL
(REVOKE anon/authenticated + service_role-only EXECUTE, re-asserted). Nothing else was touched. Matches
orch-console's functiondef-diff claim.

## Behavioral — by construction (goumlyne is read-only + console-owned; I never wet-prove there)
- **scope='keluarga'**: kk branch fires (`IN ('keluarga','both')` TRUE), umum branch skipped — UNCHANGED (regression-safe).
- **scope='umum'**: umum branch fires, kk skipped — UNCHANGED.
- **scope='both'**: BOTH branches fire → runs BOTH completeness recounts, each independently against its own snapshot
  array (`snapshot_tin_kk_ids` / `snapshot_tin_umum_ids`); a shortfall (or excess, via `<>`) in EITHER half refuses the
  sign. This closes the bypass exactly.
- **Scope domain is exactly {keluarga, umum, both}** (CHECK re-verified on goumlyne) — all three valid scopes are now
  covered; no uncovered value remains.
- The money-integrity properties I verified on mig197 (RAISE-on-mismatch, jumaat-exclusion, `tin_type` NOT NULL,
  anti-double-count, zero new write path, SECDEF re-derive immune to caller RLS) are preserved byte-identical and now
  extended to 'both'.
- orch-console's independent rolled-back wet-prove on live goumlyne (7/7: the 5 mig197 scenarios hold + scope='both'
  shortfall REFUSED + scope='both' full set signs) corroborates.

## Residency / latency
goumlyne (irsyad) ONLY — ceayj re-confirmed to still lack `tabung_preparer_sign_report` (0 rows), so goumlyne-only
apply is correct (same as mig197). 0 live scope='both' reports today (re-confirmed) — this was a latent gap, no
historical bad sign.

## Verdict: PASS
Minimal, exact, fail-closed tightening; closes my CAI-1112 'both'-scope finding with zero collateral change. Clear
for the §6.6 grant → console applies goumlyne-only on grant. Once applied, my CAI-1112 rejected verdict is fully
resolved (guard now platform-complete across all scopes).
