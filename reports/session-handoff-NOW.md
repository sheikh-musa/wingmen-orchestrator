# Session handoff — 2026-07-25 ~19:00 SGT / 11:00Z (cc-orchestrator / hub, Studio)

**FRESH FILE written at 61% context.** Supersedes `reports/session-handoff-20260725-0430Z-ARCHIVE.md` (full narrative there + git history).

## 0. WHO YOU ARE / MECHANICS
Body = **hub** (tmux `orch`, Mac Studio, `ORCH_BODY_ROLE=hub`, holds substrate `orch_lease` → all 5 singleton pens). Studio runs on the **operator's own Claude token** (Nazim swapped it; the cap crunch is over).

**EVERY TURN:** reconcile `operator_log.unprocessed()` (reply via `scripts/tg_send.sh`; replies do NOT auto-stamp → `mark_handled_through(<max_id>)`) **AND** `agent_messages to_agent='cc-orchestrator'` (stamp `read_at` **and** `responded_at` — the SLA watchdog tracks `responded_at`; unread ≠ unanswered).

- Substrate = `DATABASE_URL` via **psycopg v3**; `set -a; source .env`.
- irsyad silo (goumlyne) `goumlynecruxrlmzlntp` = `GOUMLYNE_DATABASE_URL`. ihsanos prod `ceayjeamtmcyzzvqflus` = `IHSANOS_PROD_DATABASE_URL`.
- Vercel team **wingmen** `team_mYxOkemmlg8a3HnKFAE9di7N`; `ihsanos` prj_nhbqgsXedBmatsmJD12rXdBqaS52 + `ihsanos-irsyad` prj_AgYdB27PBf7tMLlySjFNPqdoCsKM (both deploy from main). Parse deploy JSON `strict=False`.
- **ihsanos worktree:** `/private/tmp/claude-502/.../92a26e0a-.../scratchpad/ihsanos-merge` (node_modules present, on `main`). Read code off `origin/main`, never `projects/ihsanos` (parked/stale).
- **macOS gotchas that bit me:** `timeout` does NOT exist (exit 127 — a "verification run" silently never ran) · `git ls-tree` needs `-r` · **capture exit codes directly, never after piping into `tail`** (I read a *failing* control as passing, twice).

## 1. 🔴 LIVE CLIENT GATE — Elly's 945-row OCBC bank-import COMMIT is BLOCKED
cai CAI-574/575; relayed to cc-irsyad (#11241/#11256/#11263). **Verified not yet run:** goumlyne donations = 2,588, irsyad audit chain = 676 rows.
**Release needs ALL THREE, in this order:**
```
1. hash-version discriminator   DELIVERED, authored-unapplied → awaiting cai §6.6 grant
2. recursive sorter (v2)        ships in the SAME change as 1 — NEVER sorter-first
3. money/audit SHARED FATE      NOT STARTED — cai to rule the shape first
```
- **Scope is deliberately narrow:** only the *committing write*. Tooling, parsing, matching, review UI and write-nothing dry-runs are NOT blocked. *A gate broader than its justification gets worked around.*
- **Override** = explicit operator decision **with the consequence disclosed to the client in the same message** — never silently.
- **Do NOT tell Elly her upload is faulty** — it verified clean (945 credits, S$222,463.79 matching the embedded total). Client wording routes through cai.
- **Pre-flight before any run:** scan narratives for **NUL (U+0000) / lone surrogates**. Postgres rejects them; the audit batch is ONE atomic INSERT while donations commit separately best-effort ⇒ **945 money rows with ZERO audit rows** — worse than what the gate prevents.

## 2. 🔴 THE AUDIT-CHAIN FINDING (the session's spine)
`writeAuditLog` hashes a **JS-serialised string** but persists **jsonb** (normalised — reorders nested keys, normalises numerics). Worse: `canonicalPayloadJson` passes an **ARRAY** to `JSON.stringify`, which JS applies as a replacer **allowlist at every nesting level**, silently dropping nested keys from the hash while storing them in full.

**Live irsyad (676 rows) — recorded in substrate `audit_chain_boundaries` row 1:**
```
 28  FULLY VERIFIABLE     reproduces AND the hash covers everything stored
 12  PARTIALLY COVERED    reproduces, but nested content sits OUTSIDE the preimage
                          ids 93,95,96,97,754,755,756,757,762,3140,3141,3142
636  NOT REPRODUCIBLE     out-of-process importer writes
```
- **Discriminator = WRITER CLASS, not id/date.** App writes reproduce 40/40 (8 entity types); bulk import/seed writes 0/636 (4 types). `organization` spans ids 93→3142 reproducing throughout. **The "June boundary" was an artifact of when the import ran** — no commit touched audit/hashchain 14–26 Jun.
- **Coverage is ARBITRARY, not merely partial:** a nested key is covered only if its name *coincidentally* also appears at top level. Shapes "keys only nested" and "nested keys shadowing top-level" are structurally identical, differ only in a key's **name**, and only one is hashed.
- **PROVEN on live row 754:** stored `{"new":{"hr":false,…}}`, hashed `{"new":{},"old":{}}`; mutating `new.hr` leaves the hash **unchanged**. The 12 are **module-PERMISSION records ⇒ exposure is permission history, NOT donation amounts.**
- **Still holds:** linkage intact across all 676 (0 breaks, per-org) ⇒ insertion/deletion/reordering detectable everywhere. Only in-place edits of uncovered fields are invisible.
- **Live verifier run (cai-granted, read-only):** `UNVERIFIABLE_PRE_FIX, verified 40, unverifiable 636, no BROKEN` — no evidence any entry was altered.
- **NEVER re-hash history** (REPORT-IMMUT-1 / CAI-503). **No id allowlists** — cai: *a maintained allowlist converts a structural guarantee into an act of remembering, and the remembering fails silently.*

## 3. 🟢 SHIPPED TO PROD TODAY — main `cd0154e → a569fb2`, both prods READY, live-verified
`c329c19` approval-emails (mig renumbered 119→**120**) · `bc62f36` three-state verifier + boundary · `90bdde9` both-partitions correction · `a569fb2` re-key to writer class. Earlier waves: view-as, hydration gates, reset flow, red-test fix.
- **Three-state verifier live:** `VERIFIED` / `UNVERIFIABLE_PRE_FIX` / `BROKEN`. Linkage checked first, always authoritative. Cutover is **caller-supplied from the substrate**, never hardcoded. **A post-cutover content mismatch is still BROKEN** (pinned by test) so the middle state can't become a blanket excuse. Undeclared orgs keep STRICT.
- **Guard:** `scripts/check_audit_boundary_mirror.py` (orch-side) — **FAILS CLOSED, no skip path.** Guards the **claim** as well as the numbers: fails if `below_status` regresses to overstatement, if the two partitions stop reconciling, or if the discriminator stops being WRITER CLASS. Each verified by breaking it.
- **Placement rule:** *fail-closed placement follows the credentials.* A fail-closed check where a dependency is structurally absent goes permanently red, and permanently-red gates get disabled.
- Approval-email code is LIVE but **INERT** — mig 120 unapplied. **Do not tell Gazzabyte approval emails work.**

## 4. ⏳ OPEN THREADS
| Item | State | Owner |
|---|---|---|
| **mig 121** `121_audit_log_hash_version.sql` @ `60df6227` (`feat/audit-hash-version-discriminator`) | **AUTHORED-UNAPPLIED.** Returns to cai for **§6.6 named-file grant** (file+number+sha, confirm-match first). **NEVER hand-apply; NEVER ride along inside another apply.** | cai grants |
| **recursive sorter (v2)** | in that branch; ships **with** the discriminator, never before | hub |
| **money/audit shared fate** | NOT STARTED. `writeAuditLogBatch` runs **after** donations commit and deliberately returns rather than throws; other callers rely on that contract. Needs single txn / compensating rollback / loss-proof outbox. **cai to rule the shape.** | cai → hub |
| **v4 client correction** (Gazzabyte) | **NOT SENT, must not be.** cai authors, hub verifies every factual claim **before the operator sees it** (claim→query verification block). v1/v2/v3 each had defects hub caught. Measurements citable from `audit_chain_boundaries` row 1. | cai |
| **CAI-561 revocation migration** | anon+authenticated still hold UPDATE/DELETE/TRUNCATE on `audit_log` on **both** silos; 77 goumlyne tables carry anon TRUNCATE. Author-only → cai grant → per-silo `has_table_privilege` proof. P1 (RLS blocks reachable verbs; PostgREST exposes no TRUNCATE). | hub authors |
| **mig 119 sequencing hazard** | if 119 lands **after** 121, its `append_audit_log*` RPCs write **unstamped v1** rows ⇒ silent regression to v1. **Must be a condition of the 119 grant.** | cai |
| **9 direct audit writers stay v1** | internally consistent (declare v1, hash v1) but keep lossy coverage. **Gap narrowed, not closed** — 121 is not a total fix. | later |
| **unknown-version ⇒ BROKEN** | decision taken by the build agent; hub flagged, **not ratified**. Cost: a stale verifier vs a newer writer reports BROKEN on a client log. | cai rules |
| **entity decision · org/client-exit ruling** | handed forward by cai | cai |
| **GIRO spec** | cc-irsyad holding; re-words **once** against the settled basis. Inherits **UNTESTED**, not known-bad. | cc-irsyad |
| PITR + retention | OFF on all 3 projects (daily backups ON, 7-day retention). Cost decision, operator via cai. | operator |
| captcha frontend (BOTH silos) · A2 shared cron-secret guard | queued | hub |
| 2 cosem DEPLOY-GAPs (cosem-adcda, cosem-tdu) | owner-gated, deliberately left | other lanes |

## 4b. 🟢 OPERATOR DECISION — ELLY KEEPS POS (op#7155, 2026-07-25 11:22Z)
Operator: *"for elly grant her pos access as well."* **VERIFIED LIVE BEFORE ACTING — no change was needed.**
`org_role_permissions` for org 73339164 role `preparer` already grants, all `full`: **donations · pos · tabung · bank_import**. POS was already there.
- **⚠️ THIS REVERSES THE SPECCED CLEAN ROLE**, which was going to REMOVE pos (`reports/irsyad-access-model-roles-overrides-design-20260725.md`, slim preparer = tabung + bank_import only). **POS now STAYS** — told the operator explicitly rather than silently dropping the spec change.
- **CLEAN ROLE FOR ELLY = what she has today** (tabung + bank_import + pos + donations). The work left is to define it deliberately, not to change the grants.
- **Mitigating fact found on inspection: only TWO accounts hold `preparer`** — `elly@irsyad.edu.sg` and `zz-verify-preparer@qa-madrasah.test` (QA). So it is not genuinely shared and defining it around Elly's duties widens nobody. The grab-bag risk was smaller than the spec assumed.
- **NO client-silo mutation was made ⇒ no cai gate consumed.** Standing rule restated to the operator: **new need = new role, never widen an existing one.**
- ⚠️ Note for whoever does eventually edit these: module-permission changes write `organization` audit rows, and those are **exactly the 12 rows whose nested before/after values are NOT hash-covered**. Don't churn permissions needlessly until v2 hashing lands.

## 5. 👥 FLEET
- **cai: ~92% context, P0/live-client-harm only.** Restore point `reports/cai-handoff-NOW.md` ref 575. **Nazim has taken the pen to cycle it**, waiting on **4 in-flight background agents** (Xendit research) to land. *Nazim correctly caught that my readiness check missed those — **"no background agents in flight" belongs next to "restore point fresh"**.*
- **NEVER nudge cai.** `nudge_cai.sh` uses `send-keys -l` and would concatenate onto half-typed composer text and submit it. Hub never nudged once all session. **Nazim is fixing the script** (C-u first, or refuse when the composer is non-empty) — he called it *"not safe — unexploded"*, and treating it as a defect rather than a caution is right.
- Lanes: 7 idle Studio lanes **stopped** (work committed+pushed first; every branch verified on origin **at HEAD** via `ls-remote`, not `@{u}`). Only `orch` + `cai` remain. cc-irsyad active, supervised phase.
- **Watchdog fix live** (`lane_watchdog.py` e539e3f): governance consoles escalate once per `(state, composer-text)`, carried across WORKING scans. cai IDLE_UNSENT re-fires are **legitimate** — its composer text genuinely changes.
- ⚠️ **`@{u}` tracking refs lie:** 3 worktrees had upstream = `origin/main` while on a feature branch, so `git log @{u}..HEAD` reported "0 unpushed" for branches not on origin at all. Use `git ls-remote origin refs/heads/<br>` vs local HEAD before any destructive action.

## 6. 🧭 DOCTRINE EARNED TODAY
- **"The serializer is unchanged" is a claim about CODE; "the preimage is unchanged" is a claim about DATA — only the second matters.** Prove hash/serialisation compatibility **PER PAYLOAD SHAPE, never globally**: a global pass on an unrepresentative sample reports "compatible" and clears a breaking change.
- **Retract-in-place** (doctrine; extends CAI-503 from data to explanations): a superseded causal explanation in a citable record is **struck, never deleted**. A record that quietly held three successive causes collapses an auditor's confidence when they find an earlier version.
- **Immediately after establishing a discriminator, the next error is applying it through a PROXY you never measured** (date→writer, then size→writer, one ruling apart).
- **Verify a guard by BREAKING what it guards.** Done 4× today; two of my own controls were tautological or mis-measured first.
- **A test written to satisfy a gate is exactly where tautology is most likely.** "The gate is satisfied" should raise interest in *how*, not lower it.
- **Claims of remediation must cite the artifact** (migration+applied-sha, or commit sha) **and survive a live-state check.** No artifact ⇒ *"identified and scheduled"*, never *"fixed"*.
- **Absent dependency must FAIL CLOSED, never degrade open** — and the inverse is equally a defect: a control that fails *so* closed it locks every user out (captcha).
- **Single-writer to the operator:** never restate a cai ruling to him; send deltas to cai. A freshly-reset hub is the danger window.
- **Attribute to the AUTHOR, not the relayer** — the relayer is over-credited by construction.
- **A crashed/cap-killed agent leaves UNCOMMITTED work in an ephemeral `/private/tmp` worktree** — `git status --porcelain` its worktree before concluding anything is lost.
- **Check a draft's facts even when told nothing is owed** — v1/v2/v3 of the client correction each carried defects, incl. two claims of remediation that had not happened.

## 7. ▶️ NEXT ACTIONS (in order)
1. Reconcile both inboxes; stamp `read_at` **and** `responded_at`.
2. **Nothing may be applied to a client silo without a cai §6.6 named-file grant.** mig 121 and the CAI-561 revocation both wait on it.
3. Author the **CAI-561 revocation migration** (anon/authenticated U/D/TRUNCATE on both silos, preserving legitimate authenticated INSERT/SELECT) → author-only → cai grant → per-silo `has_table_privilege` proof after apply.
4. Scope **money/audit shared fate**; put the design shape to cai rather than bolting it on.
5. On cai's return: verify **v4** claim-by-claim before the operator sees it.
6. Hold the bank-import gate until all three prerequisites land. Do not let "progress" read as "lifting".
