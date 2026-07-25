# Session handoff — 2026-07-25 ~21:50 SGT / 13:50Z (cc-orchestrator / hub, Studio)

**FRESH FILE at 87% context.** Supersedes `session-handoff-20260725-1320Z-ARCHIVE.md` (→ 1245Z → 1145Z → 1100Z → 0430Z).

---

# 🚨 0. DO THIS FIRST — LIVE BREAKAGE RISK ON THE CLIENT SITE
**The operator rotated the goumlyne `service_role` key (13:4xZ). The live irsyad deployment still holds the OLD one.**
```
Vercel project ihsanos-irsyad (prj_AgYdB27PBf7tMLlySjFNPqdoCsKM)
  SUPABASE_SERVICE_ROLE_KEY   targets=preview,production   ← now INVALID
```
- Site currently answers (`irsyad.ihsanos.com` + `/login` = 200, zero error strings) **because `/login` does not touch that key.** Failure appears on the first real server-side action — sign-in, admin, tabung write.
- **FIX:** Vercel → ihsanos-irsyad → Env Vars → update `SUPABASE_SERVICE_ROLE_KEY` → **redeploy production** (no effect until redeploy). Check the `ihsanos` project for a goumlyne key too.
- **HUB IS UNAFFECTED — verified:** our keys are `tscuymavysscrvoberrr` (hub) and `ceayjeamtmcyzzvqflus` (ihsanos prod); ceayj key still authenticates (HTTP 200). No goumlyne service key in `.env`.
- **Told the operator to hold Gazzabyte's password until the redeploy** — after three dead links, a broken app is the wrong first impression.
- *A rotated key that something still holds isn't rotated, it's broken.*

---

## 1. MECHANICS
Body = **hub** (tmux `orch`, Mac Studio, holds `orch_lease` → 5 pens). Operator's own Claude token.
**EVERY TURN:** reconcile `operator_log.unprocessed()` (reply `scripts/tg_send.sh`, then `mark_handled_through`) **AND** `agent_messages` (stamp `read_at` **and** `responded_at`).
- Substrate `DATABASE_URL` (psycopg v3). irsyad `goumlynecruxrlmzlntp` = `GOUMLYNE_DATABASE_URL`. Mini = `Musa@100.83.21.34`.
- **My `SUPABASE_ACCESS_TOKEN` now gets 403 on goumlyne api-keys** — access narrowed when the project moved under the operator's account.
- **Traps:** `timeout` absent on macOS · `git ls-tree` needs `-r` · capture exit codes **directly**, never after `| tail` · **`UID` is reserved in zsh** (use `USERID`) · `strategic_decisions` CHECKs: `domain`∈operations/architecture/…, `source`∈musa_direct/…, `decided_by`∈cc-orchestrator/cai/musa/….

## 2. 🔴 LIVE GATE — Elly's 945-row bank-import COMMIT BLOCKED
donations **2,588** · audit chain **678**. Needs THREE in order: (1) hash-version discriminator + RPC integration **@a3d6497 authored-unapplied**, (2) recursive sorter (same branch), (3) **money/audit shared fate — NOT STARTED**.
Blocked = **only the committing write**. Override = explicit operator decision **with the consequence disclosed to the client in the same message**. Elly's file is clean (945 credits, S$222,463.79). **Pre-flight: scan narratives for NUL / lone surrogates** — audit batch is ONE atomic INSERT while donations commit best-effort ⇒ 945 money rows, ZERO audit rows.

## 3. 🔴 AUDIT CHAIN — TWO INDEPENDENT DEFECTS
**(a) COVERAGE** — `canonicalPayloadJson` passes an ARRAY to `JSON.stringify` = replacer allowlist **at every nesting level**. `audit_chain_boundaries` row 1: **28 fully / 12 partially / 636 not reproducible**. The 12 = **9 modules + 2 tax_settings (9% rate, tax reg no) + 1 org profile**. Discriminator = **WRITER CLASS**, not id/date. Coverage is **arbitrary**. No `donation` entity_type exists; money-bearing rows are in the protected 28.
**(b) ATTRIBUTION** — **660/676 (97.6%) name a TEST account**, 11 null, **5 real**.
**Invariants:** never re-hash history · no id allowlists · **never hand-write audit rows** · **never grant membership by raw SQL** · `verifyChainIntegrity` without a boundary is STRICT by design (`brokenAt:4` is correct legacy behaviour, not a regression).

## 4. ⏳ OPEN THREADS
| Item | State | Owner |
|---|---|---|
| **Vercel key update + redeploy** | **§0 — do first** | operator |
| **mig 121 + 122** | `feat/audit-chain-version-integration` @ **a3d6497**, hub-verified (goumlyne has no column/RPCs; both branches true ancestors; 121 byte-untouched; tsc 0; 212 files/2142 tests/0 failed). **NO GRANT YET.** cai's conditions: **121 re-runnable** (sentinel guard — this *releases* his earlier byte-untouched bind; **I have not touched 121, awaiting his confirm**) · reconciled enumeration **(DONE: 9 files / 18 call sites, command recorded)** · per-shape RPC proof (done 29/29) · confirm-match at file+number+sha. | cai grants |
| **onboarding flow fix** | **AGENT RUNNING.** Four defects: links complete auth on bare GET (scanner prefetch destroys them) · acceptance inferred from `last_sign_in_at` · **no resend path at all** · `accepted_at` never stamped. Bind: **tests must simulate prefetch-then-human-click**. | agent → hub verifies |
| **v4 client correction** | **HUB-CLEARED**, cai batching it to the operator with the entity ask. **NOT SENT.** | operator approves |
| **agreement in git — CONFLICT** | I committed it (`b182d91`, op-directed); cai's §E says **NOT into git** ("a tree every agent can read"). **Cannot fully undo** — pushed. Options: (a) remove from HEAD + access-controlled copy [my lean] (b) history rewrite (c) leave. **Operator's call; cai to raise it in his batch.** | operator/cai |
| **money/audit shared fate** | NOT STARTED — cai to rule the shape | cai → hub |
| **CAI-561 revocation migration** | anon+authenticated hold UPDATE/DELETE/TRUNCATE on `audit_log` both silos. **NOT authored.** | hub |
| **CAI-578 EXIT proof** | **Gate is a RESTORE, not an export**: restore into a clean db → per-table counts · money **2,587 / S$1,718,341.46 exact** · `verifyChain` on the restored copy. **Per check, not global.** Binding: no new client project into a personally-owned org; **do NOT migrate goumlyne**. | hub |
| lane_tasks **#41** | the invite/Safe-Links defect (superseded by the running fix, keep for the record) | ihsanos |
| GIRO · eNETS · PITR · captcha · A2 guard · CAI-579 entity | unchanged, no dates promised | various |

## 5. ✅ DONE THIS TURN
- **Gazzabyte unblocked.** Their invite AND their reset link were both burned by a Microsoft Safe-Links prefetch (23s). Set a password via the admin API + **cleared `requires_password_setup`** (without that the middleware traps them on `/set-password` — verified in the DB, not from the API's 200). Credentials sent to the operator for WhatsApp delivery.
- **Service-role key was pasted into the chat.** I had advised against it; the operator chose to. Used it, then told him plainly it must be rotated (**he has**), and **scrubbed the plaintext from `operator_messages` id 7197** — noting scrubbing only removes the copy I know about; rotation is the remedy.
- **Enumeration reconciled with cai: 9 files / 18 call sites.** My "11" wrongly included a test file and `core/audit/api.ts` (**the canonical writer — counting it as a bypass was a category error**). Command recorded so the next body re-runs rather than trusts.

## 6. 🧭 DOCTRINE
- **"Serializer unchanged" is CODE; "preimage unchanged" is DATA** — prove **per payload shape, never globally**.
- **Retract-in-place**: superseded explanations struck, not deleted.
- **After establishing a discriminator, the next error is applying it through a PROXY you never measured.**
- **Verify a guard by BREAKING what it guards.** A guard protects its axis, blind everywhere else.
- **A characterisation in a citable record must be a FIELD, not a sentence.**
- **A hand-written list of a mechanically-derivable set is a ticket, not a guarantee.**
- **"Individually correct, wrong together"** is a named category — two instances today (api.ts write-path collision; the 121/122 sentinel). Per-artifact review structurally cannot find them.
- **cai's gate is STOP-AND-DISCLOSE, never a veto over the operator.** He is the principal.
- **Copy, verify, then trust** — SHA-256 before deleting any source copy.
- **A rotated key that something still holds isn't rotated, it's broken.**
- ⚠️ **MY PATTERN — five instances today:** the measurements held every time; **my summaries shrank or over-read** ("flat payloads" · "permission history not donation amounts" · 642/95% vs 660/97.6% · "Zuremi never accepted" · "11 direct writers"). **Watch the summary, not the measurement.**

## 7. ▶️ NEXT ACTIONS
1. **§0 Vercel key + redeploy** (operator), then release Gazzabyte's password.
2. Reconcile both inboxes; stamp `read_at` **and** `responded_at`.
3. Verify the onboarding-fix agent's output — especially that the **prefetch-then-click test genuinely fails without the fix**.
4. Await cai's confirm on touching 121 (re-runnability vs the byte-untouched bind), then his grants.
5. Author **CAI-561 revocation migration**; run the **CAI-578 restore proof**; scope **money/audit shared fate**.
6. **Nothing applied to a client silo without a cai §6.6 named-file grant.** Hold the bank-import gate — **progress ≠ lifting**.
