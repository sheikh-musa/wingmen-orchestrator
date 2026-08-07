# Tabung Pre-UAT Bug-Hunt (ultracode workflow) — 2026-06-24

**Method:** ultracode multi-agent workflow (`wlrmed6tu`) — 10 Tabung flows fanned out, static error-class analysis on `origin/main`, every finding adversarially verified (refute-by-default). 59 agents, ~27 min.
**Result:** 49 suspected → **47 CONFIRMED** (3 CRITICAL · 11 HIGH · 21 MED · 12 LOW). Classes: capped-aggregate 34 · schema-drift 6 · error-swallow 3 · org-scoping 4. ~13 distinct issues (findings overlap across flows).
Complements mirror's runtime synthetic-test gate (#18) — this is the static sweep.

## KEY INSIGHTS (the headline takeaways)

1. **`max_rows=1000` DEFEATS the `.limit(2000)`/`.limit(5000)` "bounds"** — `supabase/config.toml max_rows=1000` caps every PostgREST select at 1000 rows regardless of a larger `.limit()`. So the ~34 capped-aggregate sites that fetch with `.limit(2000/5000)` then SUM/COUNT in JS are **silently wrong past 1000 rows** — the `.limit()` is false safety. The money-tail (#3) is FAR wider than the ~13 originally scoped. **Fix = DB-side aggregates (the C1 `platform_donation_aggregate` pattern) for ALL of them** (SUM/COUNT in SQL, never JS-over-a-capped-fetch).

2. **NEW CRITICAL — sch-students plaintext-PII vs migration 068:** `createSchStudentAction` (357-365), parent-person creation (493-518), and the dedup-update (338-345) INSERT/UPDATE **plaintext `email`/`phone`**, which migration 068's `CHECK(email IS NULL)`/`CHECK(phone IS NULL)` (live) **REJECTS** → student/parent **enrollment fails** for anyone with contact details (returns generic INTERNAL_ERROR; no student created). Also the email-dedup `.eq("email", …)` is now always-empty (068 nulled email). **Fix:** write `email_encrypted/email_hash + phone_encrypted/phone_hash` via the `createPerson` contract (core/persons/api.ts), not plaintext. Breaks enrollment that feeds Tabung Keluarga (class→students).

3. **endorserSignAction NON-ATOMIC (HIGH)** — `tabung-weekly-reports.ts:621-700`: flips the report to `status='closed'` (TERMINAL) across multiple non-atomic Supabase calls → a partial failure leaves a signed financial report in an inconsistent terminal state. **Fix:** transaction/RPC for the sign+close.

4. **org-scoping (LOW, defense-in-depth):** `tabung-umum.ts` `transitionTin`/`lock`/`unlock`/`markAcknowledged` filter by `.eq("public_id", tinId)` only, **no `.eq("org_id", orgId)`** — RLS likely backstops + public_id is a UUID, but add the org filter.

5. **4th fail-open gate (LOW):** `tabung/keluarga/page.tsx:19-31` doesn't capture the org_members query error (the C4 class; PR#69 fixed 3 sibling gates, this one was missed).

## CRITICAL (3)
- reports.ts `getCategoryBreakdownAction` (177-203) — capped-aggregate: donation category grandTotal/per-category/percentages understated >1000 in-period donations.
- sch-students.ts `createSchStudentAction` (357-365) — schema-drift: plaintext email/phone INSERT rejected by 068 CHECK → enrollment breaks.
- sch-students.ts dedup-update (338-345) — same CHECK rejects plaintext-phone UPDATE.

## HIGH (11, deduped)
- tabung-keluarga `listKkBatchesWithStatsAction` (691-724) — per-batch counts over `.limit(2000)` (capped 1000).
- tabung-weekly-reports `endorserSignAction` (621-700) — non-atomic sign+close (see insight 3).
- reports `getDonationSummaryAction` (96-108) + `getPosSalesAction` (241-263) — capped-aggregate (money-tail).
- tabung-umum dashboard `listUmumTinsAction` `.limit(200)` no pagination/count — tins+amounts drop past 200.
- sch-students parent-person creation (493-518) — schema-drift plaintext PII (same as crit #2).
- tabung-keluarga `getKkClassCompletionAction` (905-911) + `getKkTopStudentsAction` (987-994) — capped-aggregate `.limit(5000)`.

## MED (21) / LOW (12) — summary
Mostly more capped-aggregate sites (the `.limit(2000/5000)`-vs-`max_rows=1000` class) across tabung-keluarga (getKkTopStudents, getKkClassCompletion, getKkStudentsByClass, listClassesForIssuance student_count, issueKkTinsToClass), tabung-umum (getUmumTopDonors, listUmumTins), reports (getReceiptLog), pos-lite-daily-cash (getDailyCash), tabung-weekly-reports (listBankedTinsForPeriod, in-flight exclusion). Plus: org-scoping on umum transitions; schema-drift `searchPersonsForUmumAction` selecting bare `persons.email` (now NULL); error-swallow in the keluarga search-resolver + the keluarga page gate.

## FIX PLAN
- **Capped-aggregate (CRIT+HIGH+MED, ~34 sites):** fold into the money-tail batch (#3) — now WIDER. DB-side aggregates (the C1 RPC pattern), service-role-only + REVOKE-FROM-anon,auth ([[reference_supabase_revoke_anon_auth]]). Money-gated (§6.6 + cc-reviewer + direct-psycopg). Note the `max_rows=1000` insight in the batch.
- **sch-students plaintext-PII (CRITICAL):** fix the INSERT/UPDATE to use encrypted columns (createPerson contract) — app-code, mirror; breaks enrollment.
- **endorserSign atomicity (HIGH):** transaction/RPC for sign+close — money/signature path, gated.
- **org-scoping + fail-open gate (LOW):** add org_id filters + fail-closed gate — app-code.
- **synthtest gate (#18):** the runtime harness should ASSERT these classes (no-error + correct totals) so they're caught in CI.

Full per-finding detail + adversarial reasoning: workflow result (task wlrmed6tu).
