# CAI-1112 mig197 sign-completeness guard — FRESH re-audit against APPLIED code

**Auditor:** cc-quality (Opus 4.8, CAI-1170 money/PII). **Date:** 2026-08-23.
**Lens:** money-integrity-fidelity. **Trigger:** cai #32166 — mig197 now applied on goumlyne
(prosrc 0→6 hits), could_not_verify row resolved via CAI-1295, fresh audit requested.
**Verdict: rejected (nonconforming, MEDIUM)** — the 3 wet-proved claims all VERIFY TRUE, but the
guard is NOT "platform-wide fail-closed" as its own header states: an app-reachable `scope='both'`
report bypasses it entirely.

## The 3 wet-proved claims — ALL VERIFIED TRUE at source (goumlyne RO)
Read the applied `tabung_preparer_sign_report` body (guard at lines 82–139, placed after the
draft-status check, before the sign UPDATE):
- **(a) RAISEs on mismatch, never silently proceeds** ✓ — keluarga: `IF v_expected_kk <> v_got_kk THEN RAISE 'report_incomplete_tin_scope'`; umum: same. Uses `<>` so it catches BOTH shortfall (under-report) AND excess. Re-derived under SECURITY DEFINER (immune to the caller's RLS/visibility gap by construction, not luck — no join to persons/sch_students).
- **(b) jumaat-exclusion verbatim + tin_type NOT NULL** ✓ — umum branch: `AND t.tin_type <> 'jumaat'`. Cross-artifact byte-check: `JUMAAT_TIN_TYPE = "jumaat"` (src/modules/tabung/jumaat.ts:25) — verbatim match. `tabung_umum_tins.tin_type` is_nullable=**NO** (verified), so `<> 'jumaat'` cannot silently drop NULL-type banked tins from the expected count.
- **(c) pure SELECT + IF/RAISE, zero new write path** ✓ — the guard is two `SELECT count(*) INTO` + IF/RAISE. No INSERT/UPDATE/DELETE in the guard region; the only writes (sign UPDATE, audit_log INSERT) are the pre-existing sign path.

The anti-double-count `NOT EXISTS` (exclude tins already claimed by another live draft/preparer_signed report) is correct — prevents false-blocking on tins that legitimately belong to another report.

## FINDING — scope='both' bypasses the guard (MEDIUM, app-reachable, latent)
The guard branches on **two** scopes only:
```
IF rpt.scope = 'keluarga' THEN ... kk completeness ... END IF;
IF rpt.scope = 'umum'     THEN ... umum completeness ... END IF;
```
But `tabung_weekly_reports.scope` (NOT NULL) has CHECK `scope IN ('keluarga','umum','both')` —
**three** values. For `scope='both'`, NEITHER `IF` fires → the completeness guard is entirely
skipped, and the report preparer-signs unguarded.

**'both' is app-reachable (not a dead enum):**
- `src/actions/tabung-weekly-reports.ts:115` — `const scopeEnum = z.enum(["keluarga","umum","both"])`.
- `createWeeklyReportAction` validates keluarga/umum tin-matching (lines 457–460) but has NO branch for 'both', and `supabase.insert({ ... scope, ... })` (line ~516) persists `scope` **as-is** — a 'both' report is a single row carrying BOTH kk and umum tin arrays, not split.

So a 'both'-scope report — which can hold the MOST banked tins (both types) — can omit banked tins
and sign without the completeness RAISE. This is the exact under-reporting hole the guard exists to
close, for the broadest scope. **Latent, not live:** goumlyne currently has 0 'both' reports
(umum 4, keluarga 9), so no bad sign has occurred — severity MEDIUM, not HIGH.

Proof is by-construction (guard IF-branches vs the scope CHECK domain vs the app create-flow). I did
NOT write-exercise on goumlyne: the DSN is read-only and, per charter, console owns goumlyne/ceayj —
I never apply/wet-prove there.

## Verdict + recommendation
**rejected (nonconforming):** the grant/ruling is SOUND and the guard is correct+protective for the
two live scopes, but the build does not deliver the "platform-wide fail-closed completeness" its own
header claims. Routes to RE-BUILD, not re-decide. Minimal fix: branch on `scope IN ('keluarga','both')`
and `scope IN ('umum','both')` (run both checks for 'both'), OR forbid 'both' at creation if it is not
an intended report shape. Escalated to cai. (cai's CAI-1295 resolution of the prior could_not_verify
stands as history; this fresh verdict re-enters the board as unresolved-pending-cai.)
