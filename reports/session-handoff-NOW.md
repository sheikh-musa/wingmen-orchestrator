# Session handoff — 2026-07-25 ~19:45 SGT / 11:45Z (cc-orchestrator / hub, Studio)

**FRESH FILE at 68% context.** Supersedes `session-handoff-20260725-1100Z-ARCHIVE.md` → which supersedes `…-0430Z-ARCHIVE.md`. Full narrative in those + git history.

---

# 🛑 0-A. **HOLD — DO NOT EXECUTE §0-B BELOW.** P0 landed after the operator's instruction.
**cai CAI-RESP-580 (P0), independently confirmed by hub:** `sales@gazzabyte.sg` holds **org-level Developer** on `lqbojdqwgzgxhioezfgb`, which contains **FIVE** projects — `tscuymavysscrvoberrr` (**orchestrator HUB**: 944 strategic_decisions · 7,030 operator_messages · 10,155 agent_messages · 767 session_digests), `ceayjeamtmcyzzvqflus` (**ihsanos PRODUCTION**, 16 orgs), `brrgastulcffamlbggyu`, `ywrpttpxwfcoodovxhsr`, and `goumlynecruxrlmzlntp` (the only arguably-theirs one).
**Developer can run arbitrary SQL AND READ service_role + anon keys + JWT secrets ⇒ REVOCATION IS NOT REMEDIATION** (keys may already be copied; no log exposes whether they were).
**⇒ §0-B is HELD.** Granting that shared mailbox a fresh app-level org_admin while it is under P0 review would be indefensible, and cai §(c) rules the opposite: **never a shared `sales@` mailbox — named individual, MFA, scoped to goumlyne ONLY.** An operator directive does not waive an active cai gate. **NOTHING STARTED — no partial state.**
**Sequencing (do NOT collapse):** (a) membership removal = instant, reversible, **operator-only** (Developers can't manage members); (b) rotate service_role + JWT secrets on all five = **PLANNED, with a window** — rotation logs client staff out and can break integrations **9 weeks before their audit** (captcha precedent); (c) re-grant only if deliberate, scoped + named + MFA.
**cai owns the operator message on this** (security posture) — hub stays OFF that thread (CAI-547).
**Still arguably safe and asked of cai:** re-sending **Zuremi's** invite (existing org_admin, `accepted_at IS NULL`, widens nothing).
**Offered to cai, not started:** read-only survey of which services hold `SUPABASE_SERVICE_KEY`/anon keys per project, so the (b) rotation window is planned against a list rather than discovered during it.

# ⚡ 0-B. (HELD by 0-A) — LIVE OPERATOR INSTRUCTION, NOT STARTED
**op#7161 follow-up, verbatim: "do B and resend zuremi s invite too"**

I deliberately did **NOT** start it — it is a multi-step mutation on a **live client silo** and starting it minutes before a context reset risks leaving a **test org_admin re-enabled on the client's donation system**. There is **no partial state**; nothing has been touched.

**Option B = drive the invite myself through the real dashboard UI, then clean up. ATOMIC — do not leave half-done:**
1. Temporarily restore membership for **one** controlled admin (`admin@irsyad.test`) on org `73339164-7c1f-40ba-a093-33f1f292dd4c` — set `org_members.deleted_at = NULL`.
2. Get a session for it: `serviceClient.auth.admin.generateLink({type:'magiclink', email:'admin@irsyad.test'})` against goumlyne (no password needed).
3. In the dashboard → **Admin → Members → Invite**, invite **`sales@gazzabyte.sg`** as **`org_admin`**. Operator approved this address (op#7161: *"sales is fine because only the 2 of them have access"* — a 2-person mailbox, which answered my shared-mailbox objection).
4. **Also re-send Zuremi's invite** — `zuremi@irsyad.edu.sg` is `org_admin` with **`accepted_at IS NULL`**, so she has never completed signup and cannot log in. She is one of the **two intended approvers** for the approval-email workflow, so this is a prerequisite there too.
5. **Re-revoke `admin@irsyad.test`** (`deleted_at = now()`). **Verify active members are exactly: elly, saddam, zuremi, sales@gazzabyte.sg.**
6. Verify the audit chain grew correctly (`org_member` / `action=create` rows) and still verifies.

**WHY THE UI AND NOT SQL:** `inviteMemberAction` (`src/actions/invite.ts`) is a **Next server action with no API route**. It does `auth.admin.inviteUserByEmail` → `org_members` insert → **`writeAuditLog`**. A raw DB insert would grant access **with no audit row and no working login** — and cai ruled today that *a missing audit record is worse than a weak one*. **That is the exact rule Elly's import is blocked on; do not breach it for convenience.** (I refused option C for this reason and told the operator so.)
**Known cosmetic cost of B, already accepted by the operator:** the grant is attributed to `admin@irsyad.test`.

---

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
