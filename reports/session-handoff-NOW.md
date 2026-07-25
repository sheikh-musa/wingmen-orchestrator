# Session handoff — 2026-07-25 ~21:20 SGT / 13:20Z (cc-orchestrator / hub, Studio)

**FRESH FILE at 81% context.** Supersedes `session-handoff-20260725-1245Z-ARCHIVE.md` (→ `…-1145Z…` → `…-1100Z…` → `…-0430Z…`).

## 1. MECHANICS
Body = **hub** (tmux `orch`, Mac Studio, holds `orch_lease` → 5 pens). Operator's own Claude token.
**EVERY TURN:** reconcile `operator_log.unprocessed()` (reply `scripts/tg_send.sh`, then `mark_handled_through`) **AND** `agent_messages to_agent='cc-orchestrator'` (stamp `read_at` **and** `responded_at`).
- Substrate `DATABASE_URL` (psycopg v3). irsyad silo `goumlynecruxrlmzlntp` = `GOUMLYNE_DATABASE_URL`. ihsanos prod `ceayjeamtmcyzzvqflus`. **No Supabase API key for goumlyne in `.env`** → mgmt API `GET /v1/projects/<ref>/api-keys`.
- ihsanos worktree `…/92a26e0a-…/scratchpad/ihsanos-merge`. Mini = `Musa@100.83.21.34`.
- **Traps:** `timeout` absent on macOS · `git ls-tree` needs `-r` · **capture exit codes directly, never after `| tail`** · `strategic_decisions` has CHECK constraints (`domain` ∈ operations/architecture/…, `source` ∈ musa_direct/…, `decided_by` ∈ cc-orchestrator/cai/musa/…).

## 2. 🔴 LIVE CLIENT GATE — Elly's 945-row bank-import COMMIT BLOCKED
donations **2,588** · audit chain **678**. Needs THREE, in order:
```
1. hash-version discriminator + RPC integration  DELIVERED @a3d6497, authored-unapplied → cai grant
2. recursive sorter (v2)                          in that same branch
3. money/audit SHARED FATE                        NOT STARTED — cai to rule the shape
```
Blocked = **only the committing write**. Override = explicit operator decision **with the consequence disclosed to the client in the same message**. **Elly's file is clean** (945 credits, S$222,463.79). **Pre-flight:** scan narratives for **NUL / lone surrogates** — audit batch is ONE atomic INSERT while donations commit best-effort ⇒ **945 money rows, ZERO audit rows**.

## 3. 🔴 AUDIT-CHAIN — TWO INDEPENDENT DEFECTS
**(a) COVERAGE** — `canonicalPayloadJson` passes an ARRAY to `JSON.stringify` = replacer **allowlist at every nesting level** ⇒ nested keys dropped from the hash, stored in full. Substrate `audit_chain_boundaries` row 1: **28 fully / 12 partially / 636 not reproducible**. The 12 = **9 modules + 2 tax_settings (9% rate, tax reg no) + 1 org profile (legal name)**. Discriminator = **WRITER CLASS** (app 40/40; bulk-import 0/636), **not id/date**. Coverage is **arbitrary** (a nested key survives only if its name coincidentally appears at top level). **No `donation` entity_type exists**; money-bearing tabung/sch_fee rows are in the **protected 28**.
**(b) ATTRIBUTION** — **660/676 (97.6%) name a TEST account**, 11 null, **5 real**. Coverage = *was it edited*; attribution = *who did it*.
**Invariants:** never re-hash history (CAI-503) · no id allowlists · **never hand-write audit rows** · **never grant membership by raw SQL** · `verifyChainIntegrity` **without** a boundary is STRICT by design (`brokenAt:4` on this org is correct legacy behaviour, **not** a regression; via `verifyChain()` → `UNVERIFIABLE_PRE_FIX 40/636, no BROKEN`).

## 4. ⏳ OPEN THREADS
| Item | State | Owner |
|---|---|---|
| **mig 121 + 122 grants** | `feat/audit-chain-version-integration` @ **a3d6497** — hub-verified: goumlyne has **no column/RPCs**, both branches are **true ancestors**, **121 byte-untouched**, tsc 0, **212 files / 2142 tests / 0 failed**. `p_hash_version` in the INSERT list; **absent ⇒ RAISE** (no DEFAULT + NULL check + **old 6-arg overload DROPPED**). 119 renumbered **122**; header states the renumber is **hygiene, not the fix**. Per-shape RPC proof 29/29 on local PG. | **cai grants** |
| ⭐ **integration-only defect (fixed)** | 121's `MIGRATION_121_GENESIS` sentinel post-dates 122's fork guard ⇒ **re-running 121 dies on the unique index** — would have surfaced as a **failed apply on a live silo, mid-gate, money-audit migration half-applied.** Fixed in 122's exclusion list. | done |
| ⭐ **11 direct writers, not 5** | mechanical check beat 119's hand-written list; `payment-confirm.ts` + `enable-default.ts` **never enumerated by anyone (incl. me)**. New `audit-direct-writer-version-guard.test.ts` derives the set from the tree, fails **by filename**. | done |
| **⚠️ sequencing call for cai** | 3rd branch `feat/audit-direct-writers-migrate` (`4fac8c9`) centralises 9 writers on `computeHash` (v1) **with no stamp** — concentrates the residual and **will fail the new enumeration test on landing, by design**. | cai |
| **v4 client correction** | **HUB-CLEARED — first version I can pass.** 49 detached from "the person", 74 now says **DO NOT rely on the actor column**, 78 carries 660/11/5, 62 strengthened, claim 11 sha → c9aa899. **STILL NOT SENT** — operator has read no version. | operator approves |
| **money/audit shared fate** | NOT STARTED. `writeAuditLogBatch` runs **after** donations commit and deliberately returns rather than throws; other callers rely on it. | cai rules → hub |
| **CAI-561 revocation migration** | anon+authenticated hold UPDATE/DELETE/TRUNCATE on `audit_log` **both silos**; 77 goumlyne tables carry anon TRUNCATE. **NOT authored yet.** | hub |
| **CAI-578 EXIT proof** | **Gate is a RESTORE, not an export.** Export → restore into a CLEAN db → (a) per-table counts (b) money reconciling **2,587 / S$1,718,341.46 exact** (c) `verifyChain` on the RESTORED copy matching live. **Per check, not a global pass.** Binding now: **no new client project into a personally-owned org; do NOT migrate goumlyne.** | hub |
| lane_tasks **#41** | Defender Safe-Links prefetch burns invite links ⇒ invitee **permanently un-resendable**; `org_members.accepted_at` is **never stamped on acceptance**. | ihsanos lane |
| agent identity + audited API | `orchestrator@ihsanos.com` live (audit row 3144 = its own actor). Spec, not code; must land **with/after v2 hashing**. | hub specs |
| GIRO · eNETS · PITR/retention · captcha · A2 cron guard · CAI-579 entity | unchanged, no dates promised | various |

## 5. ✅ DONE THIS TURN
- **Signed agreement rescued.** Sweep found **one copy**, `~/Downloads` on the **Mini**. Now `docs/legal/irsyad-gazzabyte-agreement-20260518-SIGNED.pdf` (commit **b182d91**, repo verified **private** first). **SHA-256 verified identical on both hosts before commit**; **Mini copy retained** — a successful `scp` is not grounds for deleting the only other copy. Contents never extracted/transmitted. Path recorded in substrate **`DOC-CUSTODY-001`** (id 1877) so the next body finds it **by query, not filesystem luck**. `docs/legal/README.md` carries the custody rules incl. *git history is permanent*.
- **⚠️ `~/wingmen/wingmen-cai` is NOT a git repo** — the DPA drafts it holds carry the same exposure. Drafts, lower priority, same fix.
- Invites done via the **audited** flow: `orchestrator@ihsanos.com` + `sales@gazzabyte.sg` (both org_admin); `admin@irsyad.test` re-revoked. Audit **676→678**.
- **TELL GAZZABYTE:** their invite link is **already spent** (Defender prefetch, 23s). Recovery = **"Forgot password"**, not the emailed link.
- Mirror + guard now carry the **coverage partition**; guard fails closed (verified by breaking 28→27).

## 6. 🧭 DOCTRINE
- **"Serializer unchanged" is about CODE; "preimage unchanged" is about DATA** — prove **per payload shape, never globally**. A flat-only sample reports green on the defect.
- **Retract-in-place**: superseded explanations struck, not deleted.
- **After establishing a discriminator, the next error is applying it through a PROXY you never measured.**
- **Verify a guard by BREAKING what it guards.** A guard protects its axis and is blind everywhere else.
- **A characterisation in a citable record must be a FIELD, not a sentence.**
- **A hand-written list of a mechanically-derivable set is a ticket, not a guarantee** (5→11 writers; canonical repo list; sentinel allowlist).
- **cai's gate is STOP-AND-DISCLOSE, never a veto over the operator.** Halt → route to cai → cai discloses → if the operator instructs anyway **with the consequence recorded**, it proceeds.
- **Copy, verify, then trust** — SHA-256 before deleting any source copy.
- ⚠️ **MY PATTERN, four instances today:** measurements held; **my summaries shrank or over-read** ("flat payloads" · "permission history not donation amounts" · 642/95% vs 660/97.6% · "Zuremi never accepted"). Per cai: *being right often is what makes the confident-and-wrong messages dangerous.*

## 7. ▶️ NEXT ACTIONS
1. Reconcile both inboxes; stamp `read_at` **and** `responded_at`.
2. Author the **CAI-561 revocation migration** (author-only → cai grant → per-silo `has_table_privilege` proof).
3. **CAI-578 EXIT proof** — export → **restore** → per-check verification.
4. Scope **money/audit shared fate**; put the design shape to cai.
5. **Nothing applied to a client silo without a cai §6.6 named-file grant.** Hold the bank-import gate — **progress ≠ lifting**.
