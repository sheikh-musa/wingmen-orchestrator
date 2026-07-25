# Per-shape RPC proof over the REAL OCBC narrative corpus

**Closes §4b gap 1.** Author: cc-orchestrator (hub, Studio). Run: 2026-07-25 ~19:05Z / 2026-07-26 03:05 SGT.
Branch `feat/audit-chain-version-integration` @ **c440136** — **sha UNCHANGED by this work** (see §7).

---

## 1. Why this existed

cai's grant conditions for migrations 121 + 122 included a per-shape RPC proof. That proof was run
(`src/core/audit/__tests__/audit-append-rpc-pg.test.ts`) but over the **8 synthetic shapes** in
`src/shared/lib/__tests__/audit-payload-shapes.ts`. Synthetic shapes prove the RPC handles shapes
*we thought of*. The gap: the payloads that will actually be hashed when Elly's 945-row bank import
commits are **real OCBC bank narratives**, a byte class no synthetic shape exercised.

## 2. Corpus provenance

| | |
|---|---|
| File | `logs/tg_media/Jan26_e_Statement_CSV_without_header_2026_07_24T093534_469.csv` |
| sha256 | `e32357d6b116ae5fbddec453f04b7cb7eaeca37854e5323db591903ffd195320` |
| Size | 230,574 bytes / 979 lines |
| Credits | **945** · **S$222,463.79** |

Reconciliation was **not** taken on trust. `src/shared/lib/__tests__/ocbc-parser.test.ts` contains a
skip-if-absent real-sample test; placing the CSV flipped it from skipped to **running**, verified by
name in verbose output:

```
✓ parseOcbcCsv > reconciles the real sample to 945 credits / S$222,463.79   5ms
34 passed (34)
```

Payloads are built by `.mif-samples/emit-real-payloads.ts` off the **production parser**
(`parseOcbcCsv`), reproducing the audit payload construction in
`src/actions/bank-import.ts:341-354` verbatim. Free-text fields (`counterparty_raw`,
`reference_raw`) are **real**. `category_id` / `person_id` / `import_ref` are structurally fixed —
their bytes are machine-generated and carry no corpus-derived risk (declared limitation, §6).

## 3. Byte-class pre-flight — the NUL / lone-surrogate gate

Required before the commit per the standing gate. Result over all 945 × 2 narrative fields:

| Class | Rows | Note |
|---|---|---|
| nul-byte (U+0000) | **0** | Postgres text/jsonb would reject outright |
| lone-surrogate | **0** | would fail `convert_to(…,'UTF8')` |
| control chars | **0** | |
| non-ASCII (any) | **0** | corpus is entirely printable ASCII |
| double-quote `"` | **0** | |
| backslash `\` | **0** | |

**BLOCKING COUNT: 0.** Complete inventory of non-alphanumeric characters present:
`/` ×856 · `:` ×815 · `$` ×814 · `-` ×353 · `'` ×24 · `.` ×2 · `(` ×1.
Longest narrative pair 137 chars; 7 rows carry an **empty** `counterparty_raw` (ATM/CDM cash deposits).

> Correction to an earlier reading of my own scan: I first classified 840 rows as
> "contains a JSON metacharacter". That over-read. `JSON.stringify` does **not** escape `/`, `:` or
> `$`. The corpus contains **zero** characters that JSON string-escaping touches. Measurement was
> right; the first summary was wrong — my recurring failure mode, caught here by re-checking.

## 4. The proof — per class, by name

Migrations 121 then 122 applied verbatim into a throwaway local PostgreSQL **17.10**
(`audit_real_corpus`, port 55432). Real `append_audit_log` / `append_audit_log_batch` called; rows
read back via `jsonb_agg` — so `verifyChainIntegrity` re-verifies against **the JSONB Postgres
normalised on read**, not against the JS object sent. Classes are *measured* off the corpus; a class
selecting 0 rows fails loudly rather than passing vacuously.

| # | Class | Real rows | Written+verified | Axis it exercises |
|---|---|---|---|---|
| 1 | `narrative-ascii-plain` | 945 | 40 | printable-ASCII narrative |
| 2 | `narrative-empty-field` | 7 | 7 | empty string through jsonb |
| 3 | `narrative-multi-space-run` | 9 | 9 | 2+ space runs survive byte-for-byte |
| 4 | `amount-integer` | 906 | 40 | JS int → jsonb numeric → text |
| 5 | `amount-one-decimal` | 12 | 12 | trailing-zero normalisation |
| 6 | `amount-two-decimal` | 27 | 27 | cents precision — dominant money shape |
| 7 | `amount-boundary` | 2 | 2 | corpus min 0.05 / max 100000 |
| 8 | `narrative-max-length` | 1 | 1 | longest narrative (≥120 chars) |

**FULL CORPUS: all 945 real rows written in ONE chain via the batch RPC and verified.**

`11 passed (11)`.

### Independent DB-side confirmation (not the test's own assertions)

```
org …000999 | rows 945 | distinct_hashes 945 | distinct_prev 945 | hash_version 2..2
broken_links 0  /  total_rows 945          (prev_hash = previous row's hash, all 945)
genesis row prev_hash = 'genesis'
payload->>'counterparty_raw' = 'MOHAMMED ABDUL RAZZ'   (real narrative, intact)
```

### Tamper control — verifying the guard by breaking what it guards

| Mutation | Result |
|---|---|
| one real amount +S$0.01 (row 500) | `BROKEN` at 500, `verifiedCount:500` |
| one real donor narrative +"X" (row 123) | `BROKEN` at 123, `verifiedCount:123` |
| DELETE a real row mid-chain (row 700) | `BROKEN` at 700, 944 rows remain |

Each mutation restored; chain re-verified `{valid:true}` after each. `4 passed (4)`.

## 5. 🔴 THE FINDING THAT MATTERS — correctness, not detection

**0 / 945 real payloads are v1/v2-divergent.** `canonicalPayloadJsonV1(p) === canonicalPayloadJsonV2(p)`
for every row.

The mechanism: v1 is `JSON.stringify(payload, Object.keys(payload).sort())` — its "replacer
allowlist" is the payload's own **top-level** key set (`hashchain.ts:54`). For a **flat** payload,
v1 and v2 are byte-identical. Real bank-import payloads are flat: 7 scalar keys, one constant key
set across all 945 rows.

**Consequence:** a defective RPC that stamped `hash_version = 1` (the migration-119 defect) would
still verify **GREEN** on this corpus. Demonstrated, not argued: 25 real rows deliberately declared
v1 verify `{valid:true}`.

> The real corpus proves the fix is **CORRECT on production data**. It is **not** a detector for the
> defect. The synthetic nested shapes (3,4,5,6) remain load-bearing and must not be retired on the
> strength of this run. Closing the §4b gap does **not** subsume the synthetic proof — it
> complements it.

This is the honest result and it cuts against the direction I expected when I started.

## 5b. 🔴 BLOCKING DEFECT FOUND — production `verifyChain` reports GREEN on a partial chain

Surfaced by adversarial verification of this proof, **not** by the proof itself. It is not a flaw in
migrations 121/122 — it is a flaw in what a green chain-verify is being taken to mean, and it lands
directly on this money op.

**Mechanism, all four links verified independently:**

1. `verifyChainIntegrity` (`src/shared/lib/hashchain.ts:238`) checks linkage only `if (i > 0 …)` and
   **never asserts row 0 is the genesis row**. Zero occurrences of `genesis` in `hashchain.ts` or
   `core/audit/api.ts`. ⇒ **any contiguous slice of a valid chain returns `{valid:true}`.**
2. `verifyChain` (`src/core/audit/api.ts:242-252`) issues an **un-ranged** `.select("*")` — carrying
   the comment *"pagination-ignore: verifyChain intentionally reads all entries for full chain
   integrity check"*. The comment asserts the very thing the platform silently prevents.
3. Hosted PostgREST silently caps an un-ranged select. **Measured live**, not assumed: an un-ranged
   `select=id` against a 10,205-row table on hosted project `tscuymavysscrvoberrr` returned exactly
   **1000** rows, HTTP 200, no error and no truncation signal.
4. **Live read-only count on goumlyne `goumlynecruxrlmzlntp`:** the irsyad org chain is **678** rows
   today (donations 2,588 — both match the handoff). **678 + 945 = 1,623.**

**Consequence:** the moment Elly's import commits, the irsyad chain exceeds the cap. `verifyChain`
would then read rows 1–1000, find them internally consistent, and return **`{valid:true}`** while
roughly **623 rows — including ~623 of the newly committed money rows — are never verified at all.**

Demonstrated on the real 945-row chain (test `🔴 DEFECT` in the proof, which **passes**, and that is
the point): `prefix(500)`, `middle(300..800)`, `single(1)` and **`[]` (empty)** all return
`{valid:true}`; `middle[0].prev_hash = 425a8983ad39cd72…`, demonstrably not genesis.

**The green in §4 does not transfer to a post-commit production `verifyChain` call.** Suggested
remedy (cai to rule): ranged/chunked read in `verifyChain` **plus** a genesis-sentinel assertion in
`verifyChainIntegrity`, and a non-vacuous verdict for the empty array. This is a **hub finding for
cai's ruling — it is not a lifting of anything, and I have applied nothing.**

**Residual, stated rather than glossed:** I measured the 1000-row cap on `tscuymavysscrvoberrr`, our
own project — **not** on goumlyne, whose dashboard Max-Rows I cannot read (no valid service key
since the rotation; no `pgrst.db_max_rows` role setting exists on either project, so it is not
visible from SQL). The defect does not depend on the exact cap: **any** finite cap, combined with the
missing genesis assertion, yields the same silent false green once the chain outgrows it.

## 5c. Second negative finding — the encoding axis is equally unexercised

Symmetrical to §5 and easy to miss: byte-classes `non-ascii-latin1`, `non-ascii-beyond-latin`,
`astral-plane`, `control-chars`, `nul-byte`, `lone-surrogate` all select **0 rows**; the raw 230,574
bytes contain no non-ASCII or control byte. So the real corpus **cannot exercise the UTF-8 /
`convert_to(…,'UTF8')` axis either**. "Correctness, not detection" applies to encoding as much as to
v1/v2. Also note class 1 selects all 945 rows and is therefore non-discriminating; classes 7 and 8
are 2 and 1 rows. Eight classes are **not** eight independent axes.

## 5d. Adversarial verification

This proof was independently attacked before being reported (I do not self-certify evidence feeding
a money gate). The verifier re-ran everything **from scratch on a database it created itself**, and
returned **all 7 claims CONFIRMED**. Highlights of what it actually tried to break:

- Built a **lying-preimage** mutant (store payload P, hash `P.amount+999`) → correctly `BROKEN`;
  proves verification really is from the Postgres-normalised JSONB, not the in-memory object.
- Sent keys in **reverse order**; Postgres stored them re-ordered and the row still verified —
  the verifier re-canonicalises what comes back.
- Ran **every class uncapped**: class 1 at 945/945, class 4 at **906/906 through the per-row RPC**
  (not the batch). The `.slice(0,40)` cap hides nothing.
- md5'd all 945 rows post-tamper against a never-tampered run — **identical**; the trailing "valid"
  is not masking a still-tampered DB.
- Confirmed the RPCs are real by making their guards fire (cross-org denial; missing `hash_version`
  → batch abort; 122-before-121 → structural refusal).

Re-run after adding the genesis assertion and the truncation test, on a **dropped and recreated**
database: **12 passed (12)**.

Verifier's remaining lesser findings, accepted and recorded: `verifyChainIntegrity([])` is
`{valid:true}` (footgun, not exploited here — every class test pairs verify with a length assertion);
the two test files must be run as **separate sequential invocations** (running them together races
the `DROP TABLE` in `beforeAll` — fails loudly, never a false green); no tamper case yet for a direct
`hash`-column edit or a mid-chain INSERT.

## 6. Declared limitations

- `category_id` / `person_id` / `import_ref` are fixed, not derived from a live keyword-match run.
  The narrative and amount fields — the only corpus-derived bytes — are real.
- Classes 1 and 4 are capped at 40 written rows each for speed; the **full 945** are written and
  verified in the batch-RPC run, so no row is unexercised.
- Scaffold is the hand-built minimum from the existing pg suite, not the full 001 foundation.
- Corpus-specific: the 0-blocking pre-flight is a statement about **this file at this sha**, not a
  claim that future statements are clean. The scan must re-run per import.

## 7. Handling notes

- **Nothing was applied to any silo.** Local throwaway PostgreSQL only; the harness refuses a URL
  that looks like hosted Supabase. goumlyne and ceayj untouched.
- **The branch tip did NOT move.** cai's confirm-match is pinned at `c440136`; moving the sha under
  a reviewer mid-confirm is the same class of error as a stale tracking ref. The proof therefore
  lives in gitignored `.mif-samples/`. Recommend committing it as a permanent regression test
  **after** his confirm-match lands, as a follow-up.
- **Count correction:** the "29/29" in prior handoffs was recorded at `a3d6497`. At `c440136` the pg
  suite expands to **32** tests (the 121 re-runnability block added 55 lines). Restate the number
  rather than reusing the old one.

## 8. Status

§4b gap 1 **CLOSED** (adversarially verified). Remaining between here and the 121/122 grants:
cai's **confirm-match at file + number + sha**, and the challenge windows
(CAI-576 → 2026-07-26T11:24:32Z, CAI-584 → 13:19:25Z) — **not** to be pressed early.

**NEW, and it does not wait on those windows:** §5b is a fresh gate input for the bank-import commit,
independent of the 121/122 grants. Sent to cai as an attributable bus row. Nothing applied; the
bank-import gate stays held. Progress ≠ lifting.
