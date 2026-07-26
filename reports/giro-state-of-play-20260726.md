# GIRO — verified state of play, 2026-07-26 ~03:20Z

Author: cc-orchestrator (hub, Mac-Studio), fresh body booted 02:51Z.
Trigger: the operator's `now do giro`, typed 02:13Z, left UNSENT in the hub composer and
never carried out. Nazim rescued it from the reset script and handed it over.

**Everything below was verified this session. Where I could not verify, it says so.**

---

## 0. The one-line answer

GIRO has not shipped in 10 days for three reasons, and only one of them is technical:
we never established WHICH giro, we built it twice without the second build knowing about
the first, and the live cutover is gated on an audit-lock that is not on the client's
database and whose central safety bind is not implemented in the artefact.

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
4. In flight: delegated test of whether the **existing** parsers accept these statements
   and how each GIRO variant is classified (credit vs debit, kept vs silently dropped).
   Rows dropped without a count are the specific worry.
5. Do not merge, delete or renumber anything on the giro or audit-lock branches.
   `feat/audit-chain-version-integration` **MUST STAY AT `c440136`**.
