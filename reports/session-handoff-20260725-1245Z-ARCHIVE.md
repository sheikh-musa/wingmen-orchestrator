# Session handoff — 2026-07-25 ~20:45 SGT / 12:45Z (cc-orchestrator / hub, Studio)

**FRESH FILE at 77% context.** Supersedes `session-handoff-20260725-1145Z-ARCHIVE.md` (→ `…-1100Z…` → `…-0430Z…`). Narrative in those + git history.

## 1. WHO YOU ARE / MECHANICS
Body = **hub** (tmux `orch`, Mac Studio, `ORCH_BODY_ROLE=hub`, holds `orch_lease` → 5 singleton pens). Studio runs on the operator's own Claude token.

**EVERY TURN:** reconcile `operator_log.unprocessed()` (reply `scripts/tg_send.sh`; does NOT auto-stamp → `mark_handled_through(<max_id>)`) **AND** `agent_messages to_agent='cc-orchestrator'` (stamp `read_at` **and** `responded_at`).

- Substrate `DATABASE_URL` (psycopg v3); `set -a; source .env`. irsyad silo `goumlynecruxrlmzlntp` = `GOUMLYNE_DATABASE_URL`. ihsanos prod `ceayjeamtmcyzzvqflus` = `IHSANOS_PROD_DATABASE_URL`. **No Supabase API URL/service-key for goumlyne in `.env`** — fetch via mgmt API `GET /v1/projects/<ref>/api-keys` with `SUPABASE_ACCESS_TOKEN`.
- ihsanos worktree: `/private/tmp/claude-502/.../92a26e0a-.../scratchpad/ihsanos-merge` (node_modules, on `main`). Read code off `origin/main`.
- **macOS traps:** `timeout` doesn't exist (exit 127 — a "verification run" silently never ran) · `git ls-tree` needs `-r` · **capture exit codes directly, never after `| tail`** (I read failing controls as passing twice).

## 2. 🔴 LIVE CLIENT GATE — Elly's 945-row bank-import COMMIT is BLOCKED
**Verified not run:** donations 2,588 · audit chain **678**. Release needs THREE, in order:
```
1. hash-version discriminator   @60df6227 authored-unapplied → cai §6.6 grant  [BLOCKED, see §4]
2. recursive sorter (v2)        SAME change as 1 — never sorter-first
3. money/audit SHARED FATE      NOT STARTED — cai to rule the shape
```
- Blocked = **only the committing write**; tooling/parsing/review-UI/dry-runs fine.
- Override = explicit operator decision **with the consequence disclosed to the client in the same message**.
- **Elly's file is clean** (945 credits, S$222,463.79) — never imply otherwise.
- **Pre-flight:** scan narratives for **NUL / lone surrogates** — Postgres rejects them, audit batch is ONE atomic INSERT while donations commit best-effort ⇒ **945 money rows, ZERO audit rows**.

## 3. 🔴 THE AUDIT-CHAIN FINDINGS (two, independent)
**(a) COVERAGE.** `writeAuditLog` hashes a JS string but persists **jsonb**; `canonicalPayloadJson` passes an **ARRAY** to `JSON.stringify` = replacer **allowlist at every nesting level** ⇒ nested keys dropped from the hash, stored in full. Substrate `audit_chain_boundaries` row 1:
```
28 fully covered · 12 partially (ids 93,95,96,97,754,755,756,757,762,3140,3141,3142) · 636 not reproducible
the 12 = 9 modules + 2 tax_settings (default_rate 9%, tax_registration_no) + 1 org profile (legal name)
```
Discriminator = **WRITER CLASS** (app 40/40 reproduce; bulk-import/seed 0/636), **not id/date** — the "June boundary" was an artifact of when the import ran. Coverage is **arbitrary**: a nested key is covered only if its name coincidentally appears at top level. **No `donation` entity_type exists at all**; money-bearing tabung/sch_fee rows sit in the **fully-protected 28**.
**(b) ATTRIBUTION.** **660/676 (97.6%) name a TEST account** · 11 null actor · **5 a real person**. Distinct property: coverage = *was it edited*, attribution = *who did it*. cai ruled v4 must cover it.
**Invariants:** never re-hash history (CAI-503) · no id allowlists · **never hand-write audit rows** (breaks the chain) · `verifyChainIntegrity` **without** a boundary is STRICT by design → `{valid:false, brokenAt:4}` on this org is CORRECT legacy behaviour, **not a regression**; via `verifyChain()` it returns `UNVERIFIABLE_PRE_FIX 40/636, no BROKEN`.

## 4. ⏳ OPEN THREADS
| Item | State | Owner |
|---|---|---|
| **mig 119 + 121 grants — BOTH BLOCKED (CAI-576)** | RPCs have **no version param** ⇒ rows **MIS-STAMPED v1** (a confident false declaration) ⇒ **false BROKEN** on nested payloads. **NOT an ordering defect — no ordering makes a missing parameter present.** Fix dispatched authored-unapplied: `p_hash_version` on both RPCs **in the INSERT column list**; **absent ⇒ RAISE** (never default); 121's `DEFAULT 1` **unchanged** (describes pre-column rows only); per-shape RPC proof; **rebase with per-call-site assertion** (both branches rewrite `api.ts` with opposite write paths — whichever lands second silently drops the stamp). | agent running → hub verifies → cai grants |
| **v4 client correction** | **VERIFIED BY HUB — DO NOT SEND.** Blocker: lines 49 & 74 assert attribution (*"with the person attached"*, *"a complete, ordered, **attributed** record… what it is most often asked for"*) while 97.6% name a test account — repeats the original overstatement. Also stale: claim 11 says main=`a569fb2`, actual `c9aa899`. Other 11 claims verified. Agreed with both cai judgement calls (include §5 grants; do NOT enumerate accounts). | cai re-words → hub verifies delta |
| **money/audit shared fate** | NOT STARTED. `writeAuditLogBatch` runs **after** donations commit and deliberately returns rather than throws; other callers rely on it. cai to rule: single txn / compensating rollback / loss-proof outbox. | cai → hub |
| **CAI-561 revocation migration** | anon+authenticated hold UPDATE/DELETE/TRUNCATE on `audit_log` **both silos**; 77 goumlyne tables carry anon TRUNCATE. Author-only → cai grant → per-silo `has_table_privilege` proof. **NOT authored yet.** | hub |
| **lane_tasks #41** (filed this turn) | Defender Safe-Links prefetch burns invite links → invitee **permanently un-resendable** (no resend path). Affects `sales@gazzabyte.sg` + `zuremi@`. Also: `org_members.accepted_at` is **never stamped on acceptance** — only provisioning writes it. | ihsanos lane |
| CAI-578/579 org-exit vs custody | **UNREAD by hub** — last item in queue. EXIT = tested export+restore, ordered now. CUSTODY = entity-gated, **do NOT migrate**. | cai / hub |
| 9 direct audit writers stay v1 | internally consistent but lossy — **gap narrowed, not closed** | later |
| agent identity + audited API | operator's idea; `orchestrator@ihsanos.com` now exists as step 1. Spec (not code) recommended; must land **with/after v2 hashing** or it industrialises the defect. | hub specs → cai |
| GIRO · eNETS · PITR/retention · captcha frontend · A2 cron-secret guard | unchanged; no dates promised | various |

## 5. ✅ DONE THIS TURN
- **Test-account cleanup:** 9 memberships revoked (guarded by assertions). **`auth.users` rows KEPT** — `admin@irsyad.test` is actor on 642 rows incl. the S$1.72M load.
- **Agent identity + invites, via the app's AUDITED flow** (never raw SQL — same rule blocking Elly's import): `orchestrator@ihsanos.com` (org_admin, "Wingmen Orchestrator (agent)") and `sales@gazzabyte.sg` (org_admin). `admin@irsyad.test` restored ~11 min then **re-revoked 12:29:58Z**. Audit **676→678**; row **3144 actor = `orchestrator@ihsanos.com`** — the agent now records its own actions.
- **Active members (5):** elly · saddam · zuremi · orchestrator@ihsanos.com · sales@gazzabyte.sg.
- **⚠️ TELL GAZZABYTE: their invite link is already spent** (Defender prefetch, 23s). Recovery = **"Forgot password" at /login**, not the emailed link.
- Mirror + guard now carry the **coverage partition** (`fullyCovered/partiallyCovered/ids/breakdown`), guard fails closed — verified by breaking 28→27. main `a569fb2 → c9aa899`.

## 6. 🧭 DOCTRINE
- **"The serializer is unchanged" is about CODE; "the preimage is unchanged" is about DATA** — prove compatibility **per payload shape, never globally**.
- **Retract-in-place**: superseded explanations are **struck, not deleted** (extends CAI-503 to explanations).
- **After establishing a discriminator, the next error is applying it through a PROXY you never measured** (date→writer, size→writer).
- **Verify a guard by BREAKING what it guards.** A guard protects its axis and is **blind everywhere else**.
- **A characterisation in a citable record must be a FIELD, not a sentence** — sentences can't be guarded.
- **cai's gate is STOP-AND-DISCLOSE, never a veto over the operator.** He is the principal. Halt → route to cai → cai discloses → if he instructs anyway **with the consequence recorded**, it proceeds. *An agent standing between the operator and his own company is a worse failure than what the gate protects.*
- **Never hand-write audit rows / never grant membership by raw SQL** — a grant with no audit row is the rule currently blocking a client's import.
- **Absent dependency must FAIL CLOSED** — and failing *so* closed it locks users out is equally a defect.
- ⚠️ **MY OWN PATTERN, four instances today:** measurements held every time; **my summaries of them shrank or over-read** ("flat payloads" · "permission history not donation amounts" · 642/95% vs 660/97.6% · "Zuremi never accepted"). **Watch the summary, not the measurement.** And per cai: *being right often makes the confident-and-wrong messages the dangerous ones.*

## 7. ▶️ NEXT ACTIONS
1. Reconcile both inboxes; stamp `read_at` **and** `responded_at`.
2. **Read CAI-578/579** (org-exit vs custody) — only unread item.
3. Verify the RPC hash-version agent's output (per-shape table + call-site enumeration), then hand to cai for the grants.
4. Author the **CAI-561 revocation migration** (author-only).
5. Verify **v4 delta** when cai re-words the attribution paragraph.
6. **Nothing applied to a client silo without a cai §6.6 named-file grant.** Hold the bank-import gate — progress ≠ lifting.
