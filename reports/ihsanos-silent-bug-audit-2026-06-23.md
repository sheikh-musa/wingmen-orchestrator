# ihsanOS Silent-Bug Audit — 2026-06-23

**Scope:** full `ihsanos` codebase (the irsyad / Tabung surface + all shared modules).
**Verified against:** `origin/main` @ `e6b66cb` (post PR #64). Findings below are CONFIRMED PRESENT on the deployed code (not the stale `feat/irsyad-role-coverage` scan).
**Trigger:** operator request during irsyad UAT — "can we do a full review to catch more of these silent bugs" — after the PR#64 embedded-`.or()` join-search bug.
**Method:** 4 parallel pattern-finders → consolidation → independent re-verification on a clean `origin/main` worktree.

## Bug classes
1. Client-side SUM/aggregate over a capped fetch (PostgREST implicit ~1000-row cap) → money silently under-counts.
2. Embedded/foreign-table column inside a top-level PostgREST `.or()`/`.ilike()` → 400 or silent non-filter (the PR#64 class).
3. Silent list truncation (`.slice`/`.limit`/implicit cap) with no pagination/total → rows vanish unseen.
4. Error-swallowing (`data ?? []` / `count ?? 0` with no error check; gate fall-through) + false-green tests.

## Already fixed on main (excluded — confirmed)
- `tabung-keluarga` tins-still-out `.slice(0,100)` → now `listUnreturnedKkTinsAction` with `.range()` + `count:'exact'` (PR#64).
- `inv-customers getInvCustomers` LIST → real `.range()` + `count:'exact'` (PR#64). *(stats fn `getInvCustomerStats` is separate, still buggy — C1-10.)*
- People directory `people/page.tsx` → `listPersonsPaginated` (PR#64); remaining `searchPersons .limit(50)` is a legit typeahead bound.

---

## CLASS 1 — Capped money SUM (17)
| # | file:line | fn | total at risk | sev |
|---|---|---|---|---|
| C1-1 | super-admin.ts:117 | getPlatformStatsAction | platform-wide donation total (**>1000 today → wrong now**) | CRITICAL |
| C1-3 | platform-settings.ts:235 | getPlatformSettingsAction | platform donation total (dup) | CRITICAL |
| C1-2 | super-admin.ts:1347 | getOrgDetailAction | per-org lifetime donation total | HIGH |
| C1-4 | donations.ts:524-568 | getDonationReportAction | report total + group sums (`pagination-ignore` comment WRONG) | HIGH |
| C1-5 | reports.ts:108 | getDonationSummaryAction | total/avg/top-donor | HIGH |
| C1-6 | reports.ts:189 | getCategoryBreakdownAction | grandTotal + per-category | HIGH |
| C1-7 | reports.ts:254 | getPosSalesAction | totalSales + per-product | HIGH |
| C1-8 | inv-invoices.ts:664 | getInvoiceStats | revenue/paid/pending/overdue | HIGH |
| C1-9 | inv-invoices.ts:781/798/806 | getAgingReportAction | aging buckets + 90d revenue/DSO | HIGH |
| C1-10 | inv-customers.ts:541/553 | getInvCustomerStats | total_revenue + outstanding (`pagination-ignore` WRONG) | HIGH |
| C1-12 | dashboard-kpis.ts:219 | fetchInvoiceKPIs | outstanding A/R | HIGH |
| C1-11 | dashboard-kpis.ts:124/126 | fetchDonationKPIs | today/month donations | MED-HIGH |
| C1-13 | dashboard-kpis.ts:221 | fetchInvoiceKPIs | month_revenue | MED-HIGH |
| C1-15 | dashboard.ts:104 | getPreviousMonthDonations | MoM baseline | MED-HIGH |
| C1-14 | dashboard-kpis.ts:154 | fetchPosKPIs | today_revenue | MED |
| C1-16 | orders.ts:392 | getOrderStats | counts + todayRevenue (no ordering) | MED |
| C1-17 | bot-monitoring.ts:160 | getBugPipelineMetricsAction | bug counts/avg | LOW |

*Excluded (bounded): sch-fees.ts:517/518 (single fee via `.like`), hr-payroll.ts:351/371 (one employee/period).*
*Fix: SQL `SUM()`/`GROUP BY` via PostgREST aggregate or RPC/view — never client-sum a capped fetch.*

## CLASS 2 — Embedded-column search (4 — all real)
| # | file:line | fn | impact | sev |
|---|---|---|---|---|
| C2-1 | inv-invoices.ts:981 | getInvCustomersForSelect | invoice customer name search returns nothing | HIGH |
| C2-2 | sch-students.ts:104 | getSchStudentsAction | roster name search broken; count/data diverge | HIGH |
| C2-3 | donations/api.ts:144 | listDonations | donor-name search no-ops/400s | MED |
| C2-4 | hr-employees.ts:111 | getHrEmployeesAction | employee name search broken | MED |

*Fix: 2-step resolve-ids-then-`.in()` (same as PR#64), or relationship/`!inner` filter.*

## CLASS 3 — Silent truncation (10)
| # | file:line | fn | impact | sev |
|---|---|---|---|---|
| C3-1 | super-admin.ts:173-231 | getAllOrganisations | per-org donationTotal over default-capped donations | HIGH |
| C3-2 | tabung-umum.ts:618 | listUmumTinsAction | `.limit(200)` no pagination/total; tins+amounts drop past 200 | MED-HIGH |
| C3-3 | governance.ts:158/166/175 | getGovernanceData | footers show "X of {capped len}" as total | MED |
| C3-4 | inv-quotations.ts:57 | getQuotations | default cap, no pagination (latent) | MED |
| C3-5 | inv-time.ts:64 | getTimeEntries | default cap, feeds billing (latent) | MED |
| C3-6 | platform-audit.ts:232-236 | getAuditStatsAction | distributions over `.limit(1000)` beside true total | LOW |
| C3-7 | sch-attendance.ts:331 | getAttendanceStatsAction | rate over capped slice w/o date filter (latent) | LOW |
| C3-8 | sch-grades.ts:80 | getGradesAction | default cap (latent) | LOW |
| C3-9 | tenant-monitor.ts:91 | listTenantHealthAction | `.limit(1000)` + per-org counts capped (latent) | LOW |
| C3-10 | tabung-weekly-reports.ts:249/289 | listBankedTinsForPeriodAction | `.limit(1000)` period-bounded pickers (latent) | LOW |

*Excluded (bounded): storefront-orders.ts:64 (single email), hr-self-service lists (single employee), pos/api.ts:10 listProducts (<200).*

## CLASS 4 — Error-swallow + false-green (17)
### Fail-open render — HIGH (hygiene; downgraded from CRITICAL after exploitability verification)
| # | file:line | impact | sev |
|---|---|---|---|
| C4-1 | tabung/umum/page.tsx:18,26 | membership query drops `error`; on RLS/PGRST116/no-membership → role="viewer" and UmumDashboardClient renders. Fail-open RENDER only. | HIGH |
| C4-2 | tabung/keluarga/counter/page.tsx:18,26 | identical → CounterScanClient renders on membership error | HIGH |

**Exploitability VERIFIED (not critical):** (1) every cash mutation (count/return/issue/bank/lock/override) is independently server-gated in tabung-umum.ts/tabung-keluarga.ts via `getOrgContext()` + explicit role checks → FORBIDDEN; the client `role` is cosmetic. NO privilege escalation. (2) Reads are RLS-scoped to own org (`org_id IN auth_user_org_ids()`); the org is not targetable in the query. NO cross-org exposure. (3) Upstream `dashboard/layout.tsx` (→/onboarding) + `tabung/layout.tsx` (`if(!membership) redirect`) block non-members before render → fall-through effectively unreachable. **Fix = page-only:** destructure `error` + `if (error || !membership) redirect("/dashboard")`. No server-action change, no migration.

### HIGH money error-swallow ($0-on-error)
| # | file:line | impact |
|---|---|---|
| C4-3 | dashboard-kpis.ts (L120/151/183/215/252/286/333) | every money KPI renders confident $0 on error |
| C4-4 | super-admin.ts:1324-1347 | super-admin org overview shows $0/0 on error |
| C4-5 | hr-payroll.ts:343/363/753 | payslip silently omits reimbursement/overtime (underpay) |
| C4-6 | hr-self-service.ts:1062/1072/1092 | employee sees $0 own pay/YTD/claims |
| C4-7 | inv-invoices.ts:798 | 90d revenue zeros → corrupts DSO |
| C4-8 | donations.ts:447/524 | donation export/report empty/zero on error |
| C4-9 | storefront.ts:200 | public storefront shows no products on error (lost sales) |
| C4-10 | inv-customers.ts:112 | every customer shows $0 outstanding on error |

### MED / LOW
C4-11 api/storefront-update/route.ts:59 (false-green write — returns success on failed save) · C4-13 dashboard.ts:96 (MoM delta) · C4-14 inv-payments.ts:289/304 (degrade to NOT_FOUND) · C4-12 public read endpoints products/qurban/storefront-config (by-design empty) · C4-15 bot-monitoring · C4-16 members enrichment.

### False-green test
| # | file:line | impact | sev |
|---|---|---|---|
| C4-17 | payment-confirm.test.ts:43-55 | `matches()` models the `.in()` status-guard but NOT the UNIQUE index `(org_id, payment_idempotency_key)` — the real double-spend defense — so the money double-confirm guard could regress undetected | MED-HIGH |

*Excluded NOT-A-BUG: module-permissions.ts catches (fail-CLOSED), pos-lite-rotate-tokens (surfaces failures[]), donations/export audit readback (checked), bug-report catch (scoped by-design).*

---

## Fix-first ranking
1. **C1-1 / C1-3** (+ C3-1 rides along) — platform-wide donation total, *wrong today* (>1000 donations). SQL `SUM`. **The genuine critical.** Money-gated.
2. **C4-3..C4-6** — money figures silently $0 on error (KPIs, super-admin, payslips, own-pay). Zeros worse than errors.
2b. **C4-1 / C4-2** — fail-open render on cash surfaces. Verified NOT exploitable (server-gated mutations, RLS-scoped reads, upstream redirects) → HYGIENE fix (page-only `if (error||!membership) redirect`). No migration; rides along cheaply.
4. **C1-4..C1-10** — authoritative financial reports/stats (donation/category/POS/invoice/aging/customer).
5. **C4-17** — payment-idempotency test must exercise the UNIQUE index before trusting it.
6. **C2-1 / C2-2** then C2-3 / C2-4 — broken name searches.
7. **C3-2 / C3-3** — cap-as-total footers reading as complete.
8. per-org + dashboard money, DSO, storefront, false-green write (MED-HIGH); then MED/LOW + latent.

## Gating / remediation
- **Money (Class 1, C3-1, C4 money):** DB-aggregate RPC/view → §6.6 grant + cc-reviewer (money path) + direct-psycopg dry-run→apply (decision-962; never `supabase db push`).
- **Security (C4-1/C4-2):** independent security review; fix is app-code (gate), no migration.
- **Search (Class 2):** 2-step app-code (like PR#64) + real-DB smoke per fix.
- **Error-swallow (Class 4 non-security):** app-code error checks; surface/throw instead of zero/empty.
- **Test (C4-17):** real-DB smoke against the UNIQUE index.

**Totals confirmed real on main:** C1=17 · C2=4 · C3=10 · C4=17 (incl. 2 CRITICAL security + 1 false-green test) = **48**. Excluded: 3 fixed-by-#64 + several verified not-a-bug.
