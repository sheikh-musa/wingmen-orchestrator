# Irsyad (Gazzabyte/Elly) — design scopes for the 3 pending items (2026-07-17)

Client chased ("where are the plans, 4 hours"). Approach already relayed; this doc = the scopes + honest dated timelines + the cai/client open-questions. Delete + GIRO touch audit/money → design-first → cai governance review → operator sign-off BEFORE build. Fajr is non-money → build once client confirms the inventory model.

Data layer: irsyad tabung/donation data. **RESIDENCY NOTE (verify before any bank-statement write — same crux as the COSEM one):** registry designates the **irsyad silo (goumlyne `goumlynecruxrlmzlntp`)** for "irsyad ONLY (tabung, DMS, school-fees, donor data)"; BUT the live tabung app (class-completion, tabung-report, migration 105) operates on **ceayj `ceayjeamtmcyzzvqflus`** (ihsanos multi-tenant, org-scoped). Reconcile where irsyad tabung donations ACTUALLY live before siting bank-statement PII — cai-gated governance question, standing pre-live residency gate.

---

## 1. Delete a report before signing  (audit-adjacent → cai + operator gate)
**Need:** delete a report any time after creation and before signing; once signed, immutable.
**Design:** report lifecycle = `draft → signed`. In `draft` a report is deletable (soft-delete + audit-log row: who/when/why). On `sign`, lock: no delete, no edit (immutability = the audit guarantee Elly relies on). UI: a Delete action visible only while `draft`/unsigned; hidden/blocked once signed.
**Open Q (cai):** does deleting a draft report need a reason + retention of the soft-deleted row (audit trail), or hard-delete? Recommend soft-delete + audit row.
**Timeline:** design today → cai+operator sign-off → build. **Target live: ~Sat 19 Jul.** Smallest of the three.

## 2. Tabung Fajr — outstanding-tins inventory  (non-money → build once model confirmed)
**Need:** a Fajr tabung like Keluarga + a stock/inventory view of OUTSTANDING tins (issued-but-unreturned).
**Design:** reuse the Keluarga tabung engine (issue → count → return → bank → close) for a Fajr batch; add an "outstanding tins" view = issued − returned, filterable. Report variant mirrors the Keluarga report.
**BLOCKED on client answer (asked):** how is "outstanding" shown — per class / per student / single issued-vs-returned stock list? Are Fajr tins issued per student like Keluarga, or differently? Precise scope depends on this.
**Timeline:** firm date locked on her answer; provisional build ~4–5 working days after → **~25–26 Jul** if she answers in the next day.

## 3. Bank statement / GIRO upload — reconciliation  (MONEY + audit-critical → cai + operator gate; end-Sept deadline)
**Need:** upload bank-statement/GIRO records → auto-reconcile against recorded donations so Jan–Sept ties out for the October audit.
**Design:** (a) upload (PDF/CSV bank statement or GIRO file) → parse/extract entries (date, amount, ref) with a user-confirm step (financial extract = verify-before-trust, never silently trusted); (b) match engine: statement entries ↔ recorded donations by amount+date+ref, surface matched / unmatched / partial; (c) reconciliation view: per-period tie-out (recorded total vs banked total, the delta + where it lives); (d) tamper-evident: append-only audit trail, no silent edits.
**Open Qs (cai — governance review required):** (i) RESIDENCY of bank-statement PII (goumlyne silo vs ceayj — see note above); (ii) match tolerance/rules; (iii) retention of raw statements; (iv) tamper-evidence mechanism (hash-chain?).
**Timeline:** design spec this week (**by ~19 Jul**) → cai governance review + operator sign-off → phased build through August → first working version **~mid-Aug** → done + enhancements **by end-Sept** for the Jan–Sept reconciliation in October. Matches Elly's stated deadline.

---

## Sequencing
Delete-before-sign + Tabung Fajr move first (smaller, one gated one not); GIRO designed in parallel to land well before end-Sept. Delete + GIRO specs → cai (paced to fresh-cai) → operator sign-off before their builds. Fajr build starts on the client's model answer.
