# Session handoff — 2026-07-25 ~19:45 SGT / 11:45Z (cc-orchestrator / hub, Studio)

**FRESH FILE at 68% context.** Supersedes `session-handoff-20260725-1100Z-ARCHIVE.md` → which supersedes `…-0430Z-ARCHIVE.md`. Full narrative in those + git history.

---

# ✅ 0-A. HOLD RELEASED (CAI-582) — **PROCEED WITH OPTION A**
- **P0 framing STOOD DOWN (CAI-581).** The operator moved irsyad into his org **deliberately, to get Pro** — the right instinct, since Pro is what makes PITR/retention possible on client donation data. `sales@gazzabyte.sg` is a **team member, not an external party**. That framing is **withdrawn — do not carry it anywhere.**
- **What survives, narrower and not about trust:** ihsanos production holds **16 organisations that never contracted with Gazzabyte**. *Trust answers "would they misuse it"; it does not answer "was it ours to give."* Only the second question is open.
- **⚠️ PLANNING FACT: project-scoped roles are TEAM/ENTERPRISE ONLY.** On **Pro every role is ORGANISATION-wide**, covering all current **and future** projects ⇒ **"just scope sales@ to irsyad" DOES NOT EXIST on this plan.**
- **⇒ The app invite is likely the WHOLE fix:** if they only need to USE/administer the Irsyad app they need **no database access at all**. Determining question with the operator: do they need SQL, or just the product?
- **ACTION — OPTION A: Saddam sends the invite** for `sales@gazzabyte.sg` (org_admin) from Dashboard → Admin → Members, **plus re-send Zuremi's invite** (`accepted_at IS NULL`, still cannot log in). cai's §3(c) *named-individual/MFA/single-project* governed the **Supabase infrastructure** grant, **not** the app-level org_admin — different and much narrower. Operator offered option B only if he prefers it.
- Key rotation **parked as a judgement call**, not incident response. Continuity/custody → CAI-579.

# 🧭 0-B. PROTOCOL CORRECTION I GOT WRONG (CAI-582 §A) — CARRY THIS
I had hardened *"an operator directive doesn't waive a cai gate"* into effectively **"cai outranks the operator."** **cai ruled that wrong.** He is the **principal**; cai is the **adjudicator**; the gate is **STOP-AND-DISCLOSE, never a veto over the owner of the business.**
**PROTOCOL:** halt → route to cai → cai puts the consequence in front of the operator in plain language → **if he instructs it anyway WITH THE CONSEQUENCE ON THE RECORD, it proceeds** and the record shows he was told. **Halting is correct; refusing indefinitely is not.** *An agent standing between the operator and his own company on cai's authority is a worse failure than anything the gate protects.*

## 0-C. 🔴 SECOND AUDIT DEFECT — **ATTRIBUTION** (v4-relevant; raised to cai)
Actor attribution on the 676 irsyad audit rows:
```
test-attributed  660 (97.6%)   ·   null actor  11   ·   real person (Saddam)  5   ·   total 676
```
**97.6% of the client's audit trail names a TEST ACCOUNT as actor — 660 rows, not the 642 I first filed.** (I had `admin@irsyad.test 642` and `uat-operator 18` printed one above the other and reported only the larger — a READING error in the direction that made the finding smaller. cai re-measured and caught it. Corrected with him and the operator.) The S$1.72M historical load is recorded as `admin@irsyad.test` — it was us, running an importer. **Only 5 rows name a real person.**
- **Distinct from hash coverage:** coverage answers *"was this entry edited?"*, attribution answers *"who did it?"*. We have only been correcting the first, and op_msg #2511 promised an *"accountability trail"*. **Asked cai to rule whether v4 must cover it — I cannot verify v4 as COMPLETE otherwise.**
- **Not falsified, misleading:** those imports genuinely ran under that account. Fix is forward-looking; **nothing to be re-written** (and must not be).
- **ROOT CAUSE — the fleet has no first-class identity in the app.** Agents either impersonate a human/test account or bypass the audited path. Exactly why option C was refused and option B is ugly. **The operator deduced this himself** (op#7164) from watching my invite options collapse to false-actor-or-no-audit.
- **Design constraints I gave him** (all downstream of today's rulings): **scoped per-silo, never blanket** (an agent admin on every silo = the Gazzabyte problem with our name on it) · **distinguishable actor type** so machine work is never read as a person's decision · **must land WITH or AFTER v2 hashing**, else it industrialises the lossy-coverage defect. Seed exists (`WINGMEN_BOT_API_KEY`/`CRON_SECRET` endpoints) — right shape, wrong security model, and one carries cai's A2 `"Bearer undefined"` flaw.
- **Recommended a spec cai reviews, not code.** Not started.

## 1. WHO YOU ARE / MECHANICS
Body = **hub** (tmux `orch`, Mac Studio, `ORCH_BODY_ROLE=hub`, holds `orch_lease` → all 5 singleton pens). Studio runs on the **operator's own Claude token**.

**EVERY TURN:** reconcile `operator_log.unprocessed()` (reply via `scripts/tg_send.sh`; replies do NOT auto-stamp → `mark_handled_through(<max_id>)`) **AND** `agent_messages to_agent='cc-orchestrator'` (stamp `read_at` **and** `responded_at` — the SLA watchdog tracks `responded_at`).

- Substrate `DATABASE_URL` via **psycopg v3**; `set -a; source .env`. irsyad silo `goumlynecruxrlmzlntp` = `GOUMLYNE_DATABASE_URL`. ihsanos prod `ceayjeamtmcyzzvqflus` = `IHSANOS_PROD_DATABASE_URL`.
- Vercel team **wingmen** `team_mYxOkemmlg8a3HnKFAE9di7N`; `ihsanos` prj_nhbqgsXedBmatsmJD12rXdBqaS52 + `ihsanos-irsyad` prj_AgYdB27PBf7tMLlySjFNPqdoCsKM. Parse deploy JSON `strict=False`.
- **ihsanos worktree:** `/private/tmp/claude-502/.../92a26e0a-.../scratchpad/ihsanos-merge` (node_modules, on `main`). Read code off `origin/main`, never `projects/ihsanos`.
- **macOS gotchas that bit me:** `timeout` does not exist (exit 127 — a "verification run" silently never ran) · `git ls-tree` needs `-r` · **capture exit codes directly, never after piping into `tail`** (I read a *failing* control as passing, twice).

## 2. 🔴 LIVE CLIENT GATE — Elly's 945-row bank-import COMMIT is BLOCKED
cai CAI-574/575. **Verified not run:** donations 2,588 · audit chain 676 rows.
```
1. hash-version discriminator   DELIVERED @60df6227, authored-unapplied → cai §6.6 grant
2. recursive sorter (v2)        SAME change as 1 — never sorter-first
3. money/audit SHARED FATE      NOT STARTED — cai to rule the shape
```
- Blocked = **only the committing write**. Tooling/parsing/matching/review-UI/dry-runs are fine.
- Override = explicit operator decision **with the consequence disclosed to the client in the same message**.
- **Elly's file is clean** (945 credits, S$222,463.79) — never imply otherwise.
- **Pre-flight before any run:** scan narratives for **NUL / lone surrogates**. Postgres rejects them and the audit batch is ONE atomic INSERT while donations commit best-effort ⇒ **945 money rows, ZERO audit rows**.

## 3. 🔴 THE AUDIT-CHAIN FINDING (session spine)
`writeAuditLog` hashes a **JS string** but persists **jsonb** (normalised). `canonicalPayloadJson` passes an **ARRAY** to `JSON.stringify` = a replacer **allowlist at every nesting level**, dropping nested keys from the hash while storing them in full.

**Live irsyad — substrate `audit_chain_boundaries` row 1:**
```
 28  FULLY VERIFIABLE   · 12 PARTIALLY COVERED (ids 93,95,96,97,754,755,756,757,762,3140,3141,3142)
636  NOT REPRODUCIBLE   (out-of-process importer writes)
```
- **Discriminator = WRITER CLASS, not id/date.** App writes 40/40 reproduce; bulk-import/seed 0/636. The "June boundary" was an artifact of when the import ran.
- **Coverage is ARBITRARY:** a nested key is covered only if its name *coincidentally* also appears at top level.
- **Proven on row 754:** stored `{"new":{"hr":false,…}}`, hashed `{"new":{},"old":{}}`; mutating `new.hr` leaves the hash unchanged. The 12 are **module-PERMISSION records ⇒ permission history, NOT donation amounts.**
- **Still holds:** linkage intact across all 676 ⇒ insert/delete/reorder detectable. Live run: `UNVERIFIABLE_PRE_FIX, 40 verified, 636 unverifiable, no BROKEN` — no evidence anything was altered.
- **NEVER re-hash history** (CAI-503). **No id allowlists.** **Never hand-write audit rows** (breaks the chain).

## 4. 🟢 SHIPPED TODAY — main `cd0154e → a569fb2`, both prods READY, live-verified
approval-emails (mig renumbered 119→**120**) · three-state verifier + boundary · both-partitions correction · writer-class re-key. Earlier: view-as, hydration gates, reset flow, red-test fix. Suite **2,025 → 2,077 passing, 0 failures**.
- Three-state verifier live; cutover caller-supplied from substrate; post-cutover mismatch still BROKEN; undeclared orgs keep STRICT.
- `scripts/check_audit_boundary_mirror.py` **FAILS CLOSED**, guards the **claim** (wording, partition reconciliation, WRITER-CLASS discriminator), each verified by breaking it.
- **Approval-email code is LIVE but INERT** (mig 120 unapplied — tables absent, verified). **Do not tell Gazzabyte it works.**

## 5. ✅ DONE THIS TURN
- **Elly keeps POS** (op#7155). Verified she **already had it** — `preparer` = donations+pos+tabung+bank_import, all full. No change made. **This reverses the specced clean role** (which removed pos) — told the operator rather than silently dropping it. Only 2 accounts held `preparer` (Elly + a QA account) so it was never really shared.
- **9 test-account memberships REVOKED** (op#7159), guarded by assertions (target==9, keep-set=={elly,saddam,zuremi}). **`auth.users` rows deliberately KEPT** — `admin@irsyad.test` is `actor_id` on **642 audit rows incl. the historical load**; deleting would orphan the trail we're correcting. **No audit rows hand-written; chain re-verified at 676.**
- **Gazzabyte access answered:** **app layer = ZERO** accounts; **infrastructure = `sales@gazzabyte.sg` holds Supabase Developer** on the org containing goumlyne ⇒ **cai's draft line "direct DB access, which only we have" is VERIFIED FALSE** and must come out of v4 (sent as #11302).

## 6. ⏳ OPEN THREADS
| Item | State | Owner |
|---|---|---|
| **Gazzabyte invite + Zuremi re-invite** | **§0 above — DO FIRST** | hub |
| **mig 121** @ `60df6227` | authored-unapplied → cai **§6.6 named-file grant** (file+number+sha, confirm-match). **Never hand-apply, never ride along.** | cai |
| **mig 120** (approval emails) | **freeze condition MET** (renumber landed `c329c19`) → eligible for §6.6 grant | cai |
| **money/audit shared fate** | NOT STARTED. `writeAuditLogBatch` runs *after* donations commit and deliberately returns rather than throws; other callers rely on it. Single txn / compensating rollback / loss-proof outbox — **cai to rule the shape.** | cai → hub |
| **v4 client correction** | **NOT SENT.** cai authors, hub verifies every claim before the operator sees it. v1/v2/v3 each had defects hub caught. **Must drop the "only we have DB access" line.** | cai |
| **CAI-561 revocation migration** | anon+authenticated still hold UPDATE/DELETE/TRUNCATE on `audit_log` on both silos; 77 goumlyne tables carry anon TRUNCATE. Author-only → cai grant → per-silo `has_table_privilege` proof. | hub authors |
| **mig 119 sequencing hazard** | if 119 lands **after** 121 its RPCs write **unstamped v1** rows ⇒ silent regression. Must be a 119 grant condition. | cai |
| 9 direct audit writers stay v1 | internally consistent but lossy — **gap narrowed, not closed** | later |
| unknown-version ⇒ BROKEN | build-agent decision; hub flagged, **not ratified** | cai |
| GIRO | synthetic prototype only. Needs audit prereqs + cai's 4 CAI-529 binds + 3 unanswered client questions. **No date given.** | cc-irsyad/cai |
| eNETS school fees | scoped only; blocked on NETS merchant account **and** school-fee invoicing has never run | — |
| PITR + retention | OFF on all 3 projects (dailies ON, 7-day). Operator cost decision. | operator |
| captcha frontend (both silos) · A2 cron-secret guard | queued | hub |
| entity decision · org/client-exit ruling | handed forward by cai | cai |

## 7. 👥 FLEET
- **cai ~92% context, P0-only.** Restore point `reports/cai-handoff-NOW.md` ref 575. **Nazim holds the pen to cycle it**, waiting on 4 in-flight background agents (Xendit research). *Nazim caught that my readiness check missed those — **"no background agents in flight" belongs next to "restore point fresh."***
- **NEVER nudge cai** — `nudge_cai.sh` uses `send-keys -l` and would concatenate onto half-typed composer text and submit it. Hub never nudged once. Nazim is fixing the script.
- 7 idle Studio lanes stopped (work committed+pushed first; branches verified on origin **at HEAD** via `ls-remote`, not `@{u}`). Only `orch` + `cai` live. cc-irsyad active, supervised, holding the GIRO spec to re-word **once**.
- ⚠️ **`@{u}` tracking refs lie** — 3 worktrees had upstream `origin/main` while on a feature branch, so `git log @{u}..HEAD` said "0 unpushed" for branches not on origin at all.

## 8. 🧭 DOCTRINE EARNED
- **"The serializer is unchanged" is about CODE; "the preimage is unchanged" is about DATA — only the second matters.** Prove compatibility **PER PAYLOAD SHAPE, never globally**.
- **Retract-in-place**: a superseded causal explanation in a citable record is **struck, never deleted** (extends CAI-503 from data to explanations).
- **After establishing a discriminator, the next error is applying it through a PROXY you never measured** (date→writer, then size→writer).
- **Verify a guard by BREAKING what it guards.** Two of my own controls were tautological/mis-measured first.
- **A test written to satisfy a gate is where tautology is most likely.**
- **Remediation claims must cite an artifact AND survive a live-state check.** No artifact ⇒ *"identified and scheduled"*, never *"fixed"*.
- **Absent dependency must FAIL CLOSED** — and the inverse (failing so closed it locks users out) is equally a defect.
- **Single-writer to the operator**; **attribute to the AUTHOR, not the relayer**.
- **A crashed agent leaves UNCOMMITTED work in `/private/tmp`** — `git status --porcelain` before concluding anything is lost.
- **Check a draft's facts even when told nothing is owed** — v1/v2/v3 each carried defects, incl. two false remediation claims.
- **Access-reducing + operator-directed + reversible + no money data ⇒ hub may act while cai is P0-only. Access-WIDENING to an external party ⇒ cai's gate.** (Asked cai to confirm this boundary; unanswered.)

## 9. ▶️ NEXT ACTIONS
1. **§0 — the Gazzabyte invite + Zuremi re-invite, atomically.**
2. Reconcile both inboxes; stamp `read_at` **and** `responded_at`.
3. **Nothing applied to a client silo without a cai §6.6 named-file grant.**
4. Author the **CAI-561 revocation migration** (author-only) → cai grant → per-silo privilege proof.
5. Scope **money/audit shared fate**; put the design shape to cai.
6. Verify **v4** claim-by-claim when cai returns (incl. dropping the false "only we have DB access" line).
7. Hold the bank-import gate until all three prerequisites land. **Progress ≠ lifting.**
