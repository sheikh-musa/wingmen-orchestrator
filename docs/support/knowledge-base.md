# cc-support knowledge base — v1 (irsyad-only)

**Scope:** the irsyad / Gazzabyte support group ONLY. This is the grounded product knowledge cc-support answers from. If a question is not covered here and cc-support cannot verify it read-only against the live silo, it ESCALATES — it never guesses. Keep this file in sync with shipped reality; it is the source of truth for answers.

## Who's who
- **Client:** Madrasah Irsyad (Elly = the tabung/finance lead). The **Gazzabyte team** is the partner that relays to Elly — address the TEAM, not "Elly" directly (op#4432).
- **Data layer (LAYER-VOCAB-001):** irsyad's data lives on the **irsyad silo (goumlyne) `goumlynecruxrlmzlntp`** — NOT the pooled ceayj DB. Every data answer is about goumlyne. Read-only only.
- **App:** the shared ihsanos frontend (`irsyad.ihsanos.com`).

## Roles
`org_admin` (full), `preparer` (prepares tabung reports), `cashier` (counts/handles tins), `viewer` (read-only), `parent` (family-scoped). Answers about "who can do X" map to these.

## Tabung (KK — kotak/tin donation) flow — the core of irsyad's use
- **Tins** are issued to students (serial + barcode), returned, counted (notes + coins + denomination counts), banked (bank reference), then closed via a report.
- **Nil / empty tin:** a tin can be returned with **S$0** — the app has a server-authoritative nil-return guard (rejects accidental blanks but allows a genuine S$0). Shipped 2026-07-15 (`feat/tabung-nil-return`). If Elly reports "can't submit an empty tin," it's supported now.
- **Denomination counts:** notes/coins counted by denomination; totals derived server-side (no client money math).
- **Slips:** tabung report slips are generated for records.
- **Class / keluarga completion report:** at **`/dashboard/tabung/keluarga`** — shows per-class completion (unreturned = total − returned; total_amount carried verbatim from the DB SUM). Shipped 2026-07-15 (PR #161). This is a v1 for the team's review.
- **TIN money is separate from storefront/POS:** tabung uses `tabung_kk_tins`; do not conflate with storefront orders.

## Donations
- `donation_categories` carries `fund_raised` / `fund_target` per category (e.g. Zakat Fitrah, Zakat Harta, Sadaqah). Donation reports read these.
- Money figures are always DB-sourced; never assert a total cc-support hasn't reconciled to source (money-discrepancy rule: reconcile BOTH sides before reassuring).

## Test / sandbox (current, 2026-07-15)
- A **synthetic test sandbox** exists for Gazzabyte vendor testing: ceayj org `515a862b`, Abdul (abdul.sukur@gazzabyte.sg) = org_admin, isolated mock data, fully removable after the test period. This is DELIBERATELY on a synthetic tenant, NOT live irsyad data (residency).
- (Historical: a stray ceayj test stub `14a55c8f` was purged 2026-07-15 — not relevant to answers.)

## Hard limits (what cc-support must NEVER do — inform, don't execute)
- **Money:** never assert a correction, refund, or moved figure — escalate.
- **PII:** never echo NRIC/phone/email/address of any person; never export a person list — escalate.
- **Residency / provisioning:** new org/silo/access/data-move — escalate (hub+cai gate).
- **Builds / bugs:** a feature ask or a reproducible bug — escalate as an attributable bus row, never "I'll build that."
- **Below high-confidence on ANYTHING:** escalate to Nazim/orch (operator's standing rule op#4537). A correct escalation always beats a confident-but-wrong answer.

## Escalation targets
- Money/residency/PII/governance → `cai` (+ hub) via attributable `agent_messages` rows.
- Builds/bugs/product changes, and "unsure of anything" → `cc-orchestrator` (hub) / Nazim.
