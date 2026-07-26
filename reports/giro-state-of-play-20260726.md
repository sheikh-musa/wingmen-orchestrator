# GIRO — verified state of play, 2026-07-26 ~03:20Z

Author: cc-orchestrator (hub, Mac-Studio), fresh body booted 02:51Z.
Trigger: the operator's `now do giro`, typed 02:13Z, left UNSENT in the hub composer and
never carried out. Nazim rescued it from the reset script and handed it over.

**Everything below was verified this session. Where I could not verify, it says so.**

---

## 0. The one-line answer

**GIRO was never an engineering problem.** The bank statements do not contain donor-level
GIRO data — collections arrive as lump-sum batches — so audit option (A) is **unachievable
from these files at any effort** (CAI-RESP-607). We diagnosed this correctly on
**2026-06-19** ("can't build without a sample GIRO file"), asked three times, never got it,
and then lost the finding — after which five weeks of status treated giro as a build
problem behind a gate. See §4.4.

Secondary, all still true: we never established WHICH giro; it was built twice without the
second build knowing about the first; and the live cutover is gated on an audit-lock that is
not on the client's database and whose central safety bind is not implemented in the
artefact (§3.1, upheld as CAI-RESP-605, grant REFUSED).

---

## 1. WHICH GIRO — still genuinely unknown, and it is the critical path

`origin/spec/giro-reconciliation` @ `4429428` (cc-irsyad-1, 2026-07-25, docs only) splits
the ask in a way the code never did:

- **(A)** import GIRO credits as donations — close to a re-skin of the **already-live**
  `bank_import` rail.
- **(B)** reconcile bank deposits against tabung — **no implementation exists.**

The spec's §4 records that bank-import and tabung are two independent rails **with no
linkage today**. That is the gap (B) would have to close.

Asked of Gazzabyte 2026-07-24 (op#6915), never answered. Re-asked 2026-07-26 ~03:05Z on
its own, with defaults, after I concluded the first ask failed because it was buried as
item 3 of a four-part message.

**Nothing has been promised to the client on timing.** A "this week" line reached them on
2026-07-25 (op#7032) originating from a *drill* message, not a real client ask; I have
explicitly withdrawn it rather than let it stand.

---

## 2. TWO ARTEFACTS, MUTUALLY UNAWARE

| | `feat/giro-reconcile-synthetic` | `origin/spec/giro-reconciliation` |
|---|---|---|
| Tip | `0374fca` | `4429428` |
| Date / author | 2026-07-17, sheikh-musa | 2026-07-25, cc-irsyad-1 |
| Content | 16 files, **+2472 lines**, insertions only | 259 lines, docs only |
| State | pushed, **NOT merged** | pushed, **NOT merged** |

The Jul-25 spec does not reference the Jul-17 prototype anywhere found. Whether its author
knew the prototype existed **could not be established** — raised to Nazim (bus #11540),
whose lane owns the sequencing call.

Prototype worktree: `/Users/Musa/wingmen/ihsanos-wt/giro-synth` (clean).
Contents: `bank-statement-parser.ts` (header-driven, bank-agnostic, credit-only),
`reconcile-matcher.ts` (greedy 1:1, `DEFAULT_TOLERANCE = {amountTolerance: 0,
dateWindowDays: 3}`), 3 server actions, a 4-step wizard UI, a nav entry, 4 test files,
and `supabase/migrations/108_reconciliation_runs.sql` (authored-unapplied).

### 🔴 Two live risks on that branch

1. **No code-level guard on the data source.** `runReconciliationAction` selects from the
   live `donations` table scoped to the caller's own org membership. Its synthetic-ness is
   a *process* constraint — file headers and a fabricated 8-row fixture — **not an enforced
   one**. Merged, an org_admin on goumlyne would reconcile real irsyad donations and
   `saveReconciliationAction` would write a real `audit_log` row.
2. **Contested migration slot 108.** `main` jumps 107 → 109; `_reserved.txt` records the
   108 collision three ways.

Test claims (31/31 unit+e2e, tsc 0, eslint 0, `next build` green, 8/8 rolled-back ceayj DB
proof) are from the branch's own `STATUS.md` — **not independently re-run.**

---

## 3. THE TECHNICAL GATE — audit-lock, per CAI-RESP-529

**NOT applied to the live irsyad silo `goumlynecruxrlmzlntp`.** Verified directly against
the database, positive control passed (`storefront_reviews` from mig 118 present):

- `append_audit_log` / `append_audit_log_batch` — **absent**
- index `uq_audit_log_org_prev_hash` — **absent**
- column `audit_log.hash_version` — **absent**
- `supabase_migrations.schema_migrations` max version — **118** (no 119/121/122/123)
- `audit_log`: 782 rows, 3 orgs, fork detector **clean** (0 non-sentinel collisions)

Lives on three unmerged branches; head of line
`feat/audit-chain-version-integration` @ **`c440136`** (tip re-confirmed — cai's pin holds).
**No grant** for 119/121/122/124.

### 🔴 §3.1 The finding I escalated to cai (bus #11541, P0)

**CAI-RESP-536 ruled the unique index "stays HELD" until the hot writers are migrated.
Migration 122 does not implement that hold.** Read at the pinned SHA:

```
171  BEGIN;
208  CREATE OR REPLACE FUNCTION append_audit_log(...)
292  CREATE OR REPLACE FUNCTION append_audit_log_batch(...)
433  DO $$        <- fork-guard block
450  CREATE UNIQUE INDEX ... uq_audit_log_org_prev_hash   (forward-only arm)
458  CREATE UNIQUE INDEX ... uq_audit_log_org_prev_hash   (full arm)
478  COMMIT;
```

The DO block branches on **whether a non-sentinel fork already exists** — not on whether
writers were migrated. **Both arms create the index**, in the same transaction as the RPCs.
No separate migration, no feature flag, no `--skip-index` switch found.

**goumlyne is the worse case precisely because it is clean:** clean ⇒ the ELSE arm ⇒ the
**full** sentinel-excluding index over all rows, on the silo whose hot direct writers are
still read-tip-then-insert (`place-order.ts:1057/1065`, qbn-bookings, donations/export,
qurban/milestone, storefront confirm/enable/payment-confirm, provision-org-core).

Not a claim that it breaks on contact — the index only bites a genuine concurrent fork. It
converts silent chain corruption into a loud 23505 on a money path, which is the
failure-mode change cai ordered mitigated *first*.

Also unbuilt: CAI-536 option (ii), the service-role-ONLY RPC variant (122 grants both
functions to `authenticated` AND `service_role`). And
`feat/audit-direct-writers-migrate` @ `4fac8c9` implements option (i)
retry-on-unique-violation — the approach CAI-536 **rejected** as primary.

**Direct-writer count is disputed and I did not settle it:** cai 18 / builder 11 /
delegate 23. CAI-584 §D forbids treating any as final until mechanically reconciled.

---

## 4. 🆕 THE SCOPING FACT NOBODY HAD CHECKED

**GIRO is already in the data the client has been sending us**, and none of it is in
their system.

Six monthly statements + one OCBC e-statement on disk (`logs/tg_media/`, real client PII —
aggregate counts only, never reproduce contents):

| File | GIRO lines |
|---|---|
| Jan26 OCBC e-Statement | 32 |
| MIF Jan / Feb / Mar / Apr / May / Jun 2026 | 32 / 45 / 53 / 35 / 41 / 45 |

Label variants observed: **GIRO COLLECTION** (exactly **6 in every single month** — reads
like a standing monthly batch), GIRO PAYMENT, IBG GIRO, GIRO RETURN (the failed ones).

**And on the live silo:** `donations` = **2,588 rows, every one `payment_method='cash'`**,
every one carrying an `irsyad_tabung_hist_2020_2026:` import ref from the historical tabung
load. **Not one donation came from a bank statement.** The `bank_import` feature is
switched on and module-gated for the irsyad preparer — and has **never once committed a
row** on their silo.

> Live and wired is not the same as working. I told the operator (op, 03:0xZ) that (A)
> might be near-done because the rail is live, then checked and had to correct it.

**NOT CLAIMED — and this matters:** that money is missing. Those GIRO COLLECTION lines may
not be intended as donations at all, and MIF may not be the donation account. Reconcile
BOTH sides to source before any word of this reaches the client.

---

### 4.1 🆕 PARSER FEASIBILITY — EXECUTED against the 6 real statements (03:40Z)

Delegate **ran** `origin/main`'s parsers (pure functions only; `commitBankImportAction`
never imported; aggregate counts only, no PII reproduced). **7 files = 6 distinct
statements** — the Jan month is present twice as two export formats (936/936 shared
idempotency keys).

**Routing unambiguous** (the two sniffs were mutually exclusive on all 7). **Row accounting
BALANCED on every file** — `parsed + skipped_zero_credit + skipped_malformed == data rows`.
No row vanishes from the totals. 0 malformed.

| GIRO variant | rows | survive | money side | notes |
|---|---|---|---|---|
| GIRO COLLECTION | 21 | **21** | credit-only | **SGD 271,037.45** |
| GIRO PAYMENT | 182 | **0** | debit-only | correctly excluded |
| GIRO RETURN | 20 | 6 | 6 cr / 14 db | 🔴 **SGD 7,447.39 of reversals survive** |
| IBG GIRO | 53 | 45 | 45 cr / 8 db | |

**So option (A) is technically feasible — collections are not discarded.** But:

🔴 **THE STRUCTURAL FACT THAT CONSTRAINS (A):** GIRO COLLECTION rows are **LUMP-SUM BATCH
credits, not per-donor rows** — 3/month, mean SGD 12,906.55, `paynow_tag` null on all 21.
The statement does not itemise who gave what inside a batch. **(A) can produce batch totals
only; it cannot produce donor-attributed giving.** If the client's audit need is
"who donated", these files cannot answer it and no build changes that. This must be
established with Gazzabyte before anything is promised.

### 4.2 🔴 THREE DEFECTS IN THE **LIVE** `bank_import` RAIL (filed to cai, bus #11552)

Reachable by the irsyad preparer **today**; latent only because she has never completed an
import (§4: zero bank-sourced donations exist).

1. **GIRO RETURN credits presented as donation candidates — money OVERSTATEMENT.**
   6 credit rows, SGD 7,447.39. A reversal is not a gift. **The signal exists and is
   discarded:** these carry `transaction_type` **NRTI** (vs **NMSC** for
   COLLECTION/PAYMENT/IBG); `transaction_type` is parsed into `ParsedBankRow` and then
   **never read** by `matchCategory` or the commit path.
   *Not overstated:* this is a preview the preparer reviews before commit — human-catchable,
   not a silent auto-commit. "Reversal" is inferred from label + type code, **not confirmed
   with the bank.**
2. **Hash-what-you-store violation.** `bank-import.ts:327` stores
   `reference_no = reference_raw.slice(0, 100)` while `importRefFor` hashes the **full**
   string. 4 rows exceed 100 chars. No row lost; the stored row cannot reproduce its own
   idempotency key. Same shape as the audit-chain defect where only 8% of rows re-verified.
3. **Conflated counter.** `skipped_zero_credit` lumps debits, true zeros, empty cells and
   non-numeric amounts into one number surfaced as "zero credit". Decomposed here it is
   **100% genuine debit rows** — a preparer reading "39 skipped: zero credit" is looking at
   39 outgoing payments.

**Latent-only (0 occurrences in these files):** blank rows skipped with no counter
(`mif-parser.ts:141`, `ocbc-parser.ts:167`); `parseOcbcDate` returning `""` on a non-
`YYYYMMDD` date (would be caught downstream by `z.string().min(8)` and reported).

---

### 4.3 🔴 CAI-RESP-607 — THE FOOTNOTE WAS THE HEADLINE

cai ruled on §4.2. Three points, all against my framing:

1. **My defect-1 grading was wrong and I withdraw it.** I graded the GIRO RETURN risk
   "mitigated — human-catchable" in the same message where I reported the parser
   *"presents them like any other credit."* Both cannot stand. **A human control is only a
   control if the human receives the discriminating signal**, and my own measurement says
   the interface removes it. I graded severity down using a mitigation my own evidence had
   already disproved, one paragraph apart. Same comfortable direction of error as the
   mig-120 authority misreport.
2. **The obvious fix is REFUSED. Do NOT exclude on NRTI.** The remedy for a *discarded*
   discriminator must not rest on an *inferred* one — I had explicitly said the NRTI
   semantics were unconfirmed with the bank, then reached for them anyway. And silent
   exclusion **drops real donations**, the worse direction because an omission leaves no
   trace. **FLAG, DO NOT DROP.**
3. **The lump-sum finding outranks all three defects: audit option (A) is UNACHIEVABLE from
   these files.** Reframed from "build harder" to **"obtain a different source."**

All three become **prerequisites #5/#6/#7 on the existing bank-import block** — no new gate.

### 4.4 🔴 THE FIVE-WEEK LOOP — WE KNEW, AND LOST IT

The Irsyad gap-map of **2026-06-19** (op#148) said verbatim: *"GIRO — can't build without a
sample GIRO file."* That was the **correct diagnosis**. It was re-asked on 06-19 (op#196)
and 06-21 (op#388), never answered, then dropped out of the record — after which every
status treated giro as a **build** problem behind a gate.

**Five weeks of treating a data-availability problem as an engineering one.** This is the
single most important lesson in this file.

**Action taken 03:5xZ:** asked Gazzabyte whether the bank can supply the **itemised GIRO
collection report** (per-donor breakdown behind each batch credit — often called a direct
debit collection / returns file). If it cannot be obtained, **(A) is not deliverable at any
effort**, and the client must hear that before their September audit, not during it.

---

## 5. OUTSTANDING SIGN-OFFS — 5, none chased for ~30h until today

| # | Who | What | State |
|---|---|---|---|
| 1 | Gazzabyte | statement retention period | re-asked 03:05Z |
| 2 | Gazzabyte | ±3 banking-day match tolerance | re-asked 03:05Z |
| 3 | Gazzabyte | they own each period's tie-out sign-off | re-asked 03:05Z |
| 4 | Gazzabyte | **(A) or (B)** — the load-bearing one | asked 07-24, never answered; deliberately NOT re-piled into the same message |
| 5 | Operator | retention decision | asked |

**Withdrawn ask:** I had told the operator he owed a *storage bucket key/secret*. goumlyne
already runs private buckets (`receipts`, `tabung-slips` — 3 objects). It is a decision,
not a secret he must place. Corrected to him rather than left on his plate.

---

## 6. UNRELATED FINDING (board item 11 — approval emails)

Verified, not asserted: `tabung_report_approvers` holds **2 active rows** (added 02:08:22Z
and 02:08:30Z, `added_by` = the Gazzabyte account — the client switched them on themselves).
`tabung_report_notifications` = **0 rows**. The `*/5` cron
`/api/cron/tabung-notifications-drain` **is** on deployed `main` (`dd72671`, confirmed
against `git ls-remote`, not a stale tracking ref).

So enqueue → drain → send is **still unexercised end to end**. Configured ≠ works.

🔴 **New: one report is stranded.** `971a84b3-…`, scope `keluarga`, period 2026-07-09,
`preparer_signed` since 2026-07-09 06:41Z, no endorser, no deposit reference. Notifications
enqueue **only** on the `draft→preparer_signed` and `preparer_signed→closed` transitions —
there is no backfill scan. It was signed 17 days before the approver table existed, so it
**can never trigger an approval email**. Recovery path would be reopen → re-sign (new
`preparer_signed_at` ⇒ new dedup key). Whether that report is genuinely awaiting approval
or simply abandoned is **not established** — it has no deposit reference.

---

## 7. NEXT

1. **Await Gazzabyte on (A)-vs-(B)** — everything else is scoping in the dark.
2. **Await cai on 121/122** (bus #11541) ahead of today's windows: CAI-576 11:24:32Z ·
   CAI-584 13:19:25Z · CAI-586 14:54:07Z. Do not press early.
3. **Await Nazim** (bus #11540) on giro sequencing + the prototype/spec divergence.
4. ~~Delegated parser feasibility test~~ — **DONE, §4.1/§4.2.** Feasible, with three live
   defects filed to cai (#11552) and one structural limit on (A) that must reach the client
   **before** anything is promised: these files cannot yield donor-attributed giving.
5. Do not merge, delete or renumber anything on the giro or audit-lock branches.
   `feat/audit-chain-version-integration` **MUST STAY AT `c440136`**.
6. **Do NOT raise the GIRO RETURN / overstatement finding with the client yet.** Nothing has
   been committed, so no figure they hold is wrong today. Reconcile both sides to source
   first — the tabung-figure lesson.

---

## 8. WHAT THIS SESSION ACTUALLY CHANGED

- Caught, and cai independently upheld (**CAI-RESP-605**, grant REFUSED), that mig 122 never
  implemented the index hold cai had been citing for days. cai's sharper reading: the guard
  is **inverted** — tolerant for the dirty silo, full enforcement for the clean one — and
  **the apply would have SUCCEEDED**, with breakage arriving later on a money-adjacent
  write. New binding rule from cai: locate every constraint **in the file, by line**, before
  any grant. That binds the hub as the body that *asks* for grants, not only cai.
- Established giro's blocker is a **client scoping answer**, not engineering.
- Found `bank_import` is live, wired, and has **never committed a row** — after telling the
  operator the opposite, then correcting it unprompted.

**Corrected/withdrawn this session:** "(A) is close to done because the rail is live" →
the rail has never run. "Operator owes a storage bucket key" → goumlyne already runs private
buckets; it is a decision, not a secret. The client's "this week" → came from a **drill**
message, withdrawn to them explicitly.
