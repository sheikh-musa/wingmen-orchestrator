# CAI-1272 batch — CAI-1232 + CAI-1233 money-path UI audits

**Auditor:** cc-quality (Opus 4.8, CAI-1170 money/PII). **Date:** 2026-08-23. Both goumlyne/ihsanos, money-path (kept on me/opus).
Both rulings scoped the build as **UI/routing over existing substrate, no data-model change** — and cai ruled any substrate
touch would be a NEW gate. So the core audit question for both: is the shipped build pure frontend, leaving the money
invariants untouched? **Verified YES for both → verdict accepted.**

## CAI-1232 — money-path-invariants-preserved — ACCEPTED
Build: PR #404 (`op#15384 per-row out-of-order bank-in on the reports list`, commit 4d8265de).
- **Zero substrate touch** (file-level diff): only `reports-list-client.tsx` (+110), `report-detail-client.tsx` (+7), and a new
  test `reports-list-bankin.test.tsx` (+89). No `supabase/migrations/`, no `.sql`, no RPC.
- **Reuses the existing gate, doesn't create a weaker path:** the new "Record deposit" per-row action is a `<Link>` deep-linking
  to the EXISTING detail deposit form (`/dashboard/tabung/reports/${id}#bank-deposit`), shown only when
  `canRecordDeposit = canPrepare && !deposit_reference && status IN ('draft','preparer_signed')` — documented as an exact mirror
  of the detail page's gate (viewer never). The gate only controls SHOWING the link; the actual bank-in still flows through the
  existing guarded form → the already-audited RPC (slip upload + deposit-ref close-gate + dual-control unchanged).
- **The 5 invariants cai verified are preserved by not being touched** (all live in the untouched RPC/constraints):
  dual-control (RPC raise + table CHECK), TIN-freeze exact-rowcount, deposit-ref close-gate, RLS closed-report immutability,
  and there is NO sequencing invariant in the DB/RPC (sequence was UI habit) — so out-of-order bank-in is safe by construction,
  exactly as ruled. Test covers the gate.
- **Verdict: accepted.** Build matches the ruling ("new per-row action deep-linking to the existing detail deposit form; nothing
  is removed"). No new migration implied or shipped (if one ever is, it's a new §6.6 gate — none here).

## CAI-1233 — anti-double-count-invariant-untouched — ACCEPTED
Build: PR #407 (`op#15400 surface Jumaat reports from the Reports section (CAI-1233 A)`, commit ed5e5b90).
- **Zero substrate touch** (file-level diff): only `reports-list-client.tsx` (+45) + a new test `reports-list-jumaat-surfacing.test.tsx` (+64).
- **Option (a) only — pure navigation, no merge:** adds a cross-link to the EXISTING separate Jumaat surface
  (`/dashboard/tabung/jumaat-reports`) + a "New Jumaat report" entry routing to the EXISTING jumaat flow
  (`/dashboard/tabung/jumaat-reports/new`). In-code comment: "PURE navigation … No data-model contact, reads no table — Jumaat is
  NOT folded into the weekly reports (option b, cai-rejected)." No union/concat/filter/join of jumaat+weekly, no table reads.
- **Anti-double-count invariant untouched:** the DB CHECK `tin_closed_via_single_report` (a tin cannot carry both
  `closed_via_report_id` and `closed_via_jumaat_report_id`) is a substrate constraint the build never touches; Jumaat stays a
  separate report type reached by a link. Option (b) (folding, which cai rejected as reopening the double-count surface) is NOT built.
- **Verdict: accepted.** Build matches ruling (a); Jumaat kept structurally separate; the load-bearing DB invariant is preserved
  by construction. (CSV export op#15403/PR#406 is out of scope — cai ruled it "not a gate.")

## Board movement
2 money-path audits accepted → cleared from the stale board. My stale 13 → 11. (Deploy-provenance of the merged PRs is console's;
these are pure-UI, no substrate/deploy-risk surface.)
