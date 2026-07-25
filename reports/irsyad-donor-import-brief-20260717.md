# Irsyad donor-file import package (MONEY+PII, cai-gated CAI-RESP-474)

**From:** cc-orchestrator (hub) · **Authority:** operator #5104 (import authorized) + cai CAI-RESP-474 (strict gate, conditions below) · **Date:** 2026-07-17
**Repo:** ihsanos · **Worktree:** `~/wingmen/ihsanos-wt/donor-import` (branch `chore/irsyad-donor-import`, off origin/main 14f5684). **AUTHORED + DRY-RUN ONLY — do NOT apply.** Hub reviews + cc-reviewer money+PII pass + cai grant, THEN hub applies.

## The file
`~/wingmen/ihsanos-wt/donor-import/.donor-import-scratch/Collected Tabung 2020 to 2026.xlsx` — the real irsyad donor file (Gazzabyte-provided, never loaded). Sheet 'Collected 2020 to 2026', **2,613 data rows**, columns: `ID, Name, Street, Contact, email, Type, Calculated On (date), Collected Amt (money)`. Multiple entries per donor (donation history; note dup names w/ different IDs). **MONEY-BEARING + heavy PII** (name / home address / phone / email).

## Target
**goumlyne** (irsyad's DEDICATED silo, ref `goumlynecruxrlmzlntp`, CAI-471) — the correct silo, org 73339164. Investigate the goumlyne donor/donation schema (`donations`, `donation_categories`, `donor_tier`, `persons`, etc.) and map: donor identity → the identity table (persons/donor), each row's `Collected Amt` + `Type` + date → a `donations` record. Map `Type` (e.g. 'Tabung Fajar') to `donation_categories`. Confirm the exact FK/mapping before building the import.

## cai's CAI-RESP-474 conditions (build to ALL of these)
1. **Idempotent + dedup + assert-empty/safe:** the import must be safely re-runnable (a partial/double run cannot duplicate). Assert the target is empty of this dataset first (op says it was never loaded — VERIFY, don't assume: assert 0 pre-existing matching donations, or dedup by a stable key so re-runs are no-ops). Additive INSERTs only — never mutate/delete existing rows.
2. **PII-minimize + RLS-no-anon:** the target donor/donation rows must be RLS-protected — **anon and authenticated (non-member) roles must NOT be able to read them** (donor PII incl home addresses/phones). Add/verify RLS so only the org's members read. Minimize PII surface; do not log raw PII.
3. **Money reconcile (additive-only):** after import, the sum of imported `Collected Amt` AND the per-`Type` breakdown must reconcile EXACTLY to the file (row count + total + per-category sums). Zero mutation of any pre-existing donation/total.
4. **Guarded apply method:** `scripts/db/apply-*` pattern — direct-psycopg, `--expect-ref goumlynecruxrlmzlntp` fail-closed, single txn, dry-run→apply, NEVER `supabase db push`.
5. **Post-proof (in the script):** imported row count == file rows (or deduped count, stated); sum + per-type reconcile to the file; **anon-DENIED proof** (a SELECT as anon returns 0 / is blocked by RLS); zero change to any pre-existing row; guarded ref matched.

## Report back to cc-orchestrator
Branch + SHA; the schema mapping (file col → target table.col); the guarded import script path; the **DRY-RUN output** against goumlyne (what would insert, the reconcile totals + per-type breakdown, the anon-denied check, the idempotency/dedup assertion) — ALL rolled back, nothing written. AUTHORED only. Hub then: cc-reviewer money+PII pass → send package+dry-run to cai → cai re-verifies (sample + RLS) + flips grant → hub applies + post-proof. Money+PII on a minors-PII client silo — precision + PII-safety over speed.
