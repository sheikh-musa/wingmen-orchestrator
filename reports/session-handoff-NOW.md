# Session handoff — 2026-07-26 02:15Z / 10:15 SGT (cc-orchestrator / hub, Mac-Studio)

**FRESH FILE at ~76% context.** Supersedes `session-handoff-20260726-0105Z-ARCHIVE.md`.
**Read order: §0 → §1 → §2 → §9.** No grant assumed. Everything applied tonight was granted or explicitly authorised, and §1 records which.

---

## 0. 🛑 STANDING HOLDS — each with the timestamp that set it

> Holds rot fast. Two in the 00:20Z file were false by 01:05Z. **Re-verify against live state before acting on any of these.**

1. **TOKEN REMEDIATION NOT RUN** — verified 02:13Z: Studio still `sbp_2180…b069` (burned), Mini still the PARTNER's `sbp_f670…4e34`, **0 `.env.bak-17*` on the Mini**. The operator has the commands (Nazim sent them, #7309) — **DO NOT RE-SEND**. Absence of the backup file is the evidence, not absence of a reply.
2. **`feat/audit-chain-version-integration` MUST STAY AT `c440136`** — cai's confirm-match is pinned there.
3. **NO GRANTS** for 119 / 121 / 122 / 124. Windows: CAI-576 `11:24:32Z` · CAI-584 `13:19:25Z` · CAI-586 `14:54:07Z` **today**. Do not press early.
4. **Elly's bank-import commit is HELD** — four prerequisites (§4).
5. **AGENT-AS-SIGNATORY GATE (CAI-601):** no approver row beyond `saddam@`/`zuremi@` until the durable service-account property ships. cai's ruling: *a two-person control with a non-human half is a one-person control with extra steps.*
6. **My unsent composer text: `now do giro`** — §2 item 4. Mine to re-decide.

---

## 1. ✅ SHIPPED THIS BLOCK — Gazzabyte unblocked end to end

**`main` `231714a` → `dd72671`.** Both prod deploys READY, `database:ok` on both, `/login` + `/set-password` 200.
Three client-facing fixes, all found by the client or by verifying their report:
- **View-as unreachable on a phone** — `view-as-controls.tsx:85` was `relative hidden sm:block`; the ONLY entry point didn't render below 640px while the exit banner was already mobile-aware.
- **"— read only" label mobile-hidden** (`:41`) — a preview never told the user it was read-only. Server always enforced it; clarity gap.
- **"Please try again" on an unsatisfiable failure** — `tabung-report-approvers.ts` now distinguishes Postgres **42P01** and says the feature isn't enabled and retrying won't help. cai ruled this separately mandatory.
- Plus a measured `gap-2 sm:gap-3` on `dashboard-shell.tsx:1205` — the newly visible pill was eating the truncated mobile title (320px showed `Repo…`).

**MIGRATION 120 APPLIED to goumlyne, 01:47Z, under CAI-601** (granted 01:43:46Z).
⚠️ **My first disclosure said it went on the operator's override ahead of the window. That was WRONG** — cai's grant landed 3 min BEFORE the write. Corrected in #11513. The error direction is worth remembering: it made me look *more* constrained than I was.
Apply script `scripts/apply_120_tabung_report_approvers.py` — gates enforced at run time: blob sha re-verified `== 45a168d0`, DSN must name the ref, and **residency asserted against the DATA** (refuses unless the irsyad org is present and the org count is single-tenant shaped). *A DSN can be edited; a tenant roster cannot.*
Raw proof: `silo=goumlynecruxrlmzlntp host=Mac-Studio`, table present, **anon INSERT/SELECT = False/False**.

**cai's condition 2 satisfied BY THE CLIENT, not by us:**
```
saddam@irsyad.edu.sg  active=true  added_by=sales@gazzabyte.sg  02:08:22Z
zuremi@irsyad.edu.sg  active=true  added_by=sales@gazzabyte.sg  02:08:30Z
```
I asked them to switch their own approvers on rather than seeding it — the money-control config carries their name, and it discharges the agent-as-signatory gate by construction.

🔴 **NOT CLAIMED: the approval emails have NOT been exercised.** Configuration is complete; the send path fires on **Elly's next weekly-report submission**. The client was told this explicitly and asked to report whether both emails arrive. **Do not let "it's set up" become "it works".**

---

## 2. ▶️ OPEN THREADS

| # | Item | State | Owner |
|---|---|---|---|
| 1 | **Token sequence** (§0.1) | commands delivered, **NOT run**; both hosts unchanged | operator → hub gates step 3 **on BOTH hosts** |
| 2 | **mig 121+122 grants** | ⛔ no grant; windows close today; awaiting cai confirm-match at file+number+sha | cai |
| 3 | **mig 124** (CAI-561 rest) | authored-unapplied @ `9e484f3`, own §6.6 grant required | cai |
| 4 | **GIRO access for Elly** | **NEXT UP** — client raised 2026-07-24 (#6911), also "giro reconciliation" (#7157). **Not scoped, nothing promised.** | hub |
| 5 | **Elly's bank import** | **HELD** — 4 prerequisites (§4) | cai |
| 6 | **Agent-as-signatory fix** (task #10) | cai ruled: exclude by **durable service-account property, NEVER a hardcoded address** | hub |
| 7 | **CAI-586** pre-push smoke → structurally read-only | queued | hub |
| 8 | **CAI-578 EXIT proof** (a RESTORE, not an export) | not started | hub |
| 9 | **money/audit shared fate** | cai to rule shape | cai |
| 10 | **Fleet identity as first-class** (CAI-591 §D) | surfaced at 5 layers now — scope as its own work | hub |
| 11 | Verify approval emails actually send | on Elly's next submission | hub + client |

**Bus at handoff: 1 unread** — #11515 (Nazim, P1: our context gauge reads 73% while the TUI says 100%; ours measures nominal not usable).

---

## 3. 🧭 THE HOUSE DEFECT — SIX INSTANCES, AND A NEW LAYER

**A check that silently operates on a SUBSET and reports on the WHOLE.**
1. `verifyChain` read 1000 of 1623 rows → `valid` · 2. `grep`/`find` wrapped + gitignore-aware → "clean" from **0 files scanned** · 3. token gate on 1 of 2 hosts · 4. message log asserted delivery it never observed · 5. `canonicalPayloadJson` hashed only keys it could see · 6. **"Please try again" asserted a transient failure it never checked.**

**cai:** *"None of them lied. Each reported truthfully on a scope narrower than the claim it supported, and the output was INDISTINGUISHABLE from a correct check. Vigilance reads the same green."*
**Corollary, evidenced 5×: the cheapest detector is a SECOND VANTAGE POINT, not a harder look.** cai found my Mini gap · Nazim found the log defect · **the CLIENT** found the mobile defect *and* closed the session ambiguity · a **delegate** found my render harness was unfaithful and Playwright's pinned chromium corrupt.

### 🆕 NEW: PROXY vs PROPERTY (from cai's "one script too narrow" thread)
The send-path count went **3 → 4 → 6 → 9**, each step someone refusing to inherit the previous number. Then I checked the last "unguarded" one, `nazim_say.sh` — **it doesn't have the defect**: it `exit 1`s on a non-200 *before* reaching the log call. Safe by control flow, not by flag.
**All those counts measure a PROXY (`grep --undelivered`), not the property (`can a failed send REACH the log call?`).** Two correct designs satisfy it; the proxy flags the exit-first one as broken. My first sweep also *missed* that file because its call spans a line continuation and I grepped single-line — the same proxy produced a false negative and a false positive on one file. **Stop quoting a count; audit reachability.**

---

## 4. 🔴 ELLY'S BANK IMPORT — FOUR PREREQUISITES

donations **2,588** · chain **678**.
(a) hash-version+RPC @ `c440136` authored-unapplied · (b) recursive sorter · (c) **money/audit shared fate — NOT STARTED** · (d) **CAI-587 completeness fix — in CODE, not in PRODUCTION on the silo.**
Byte pre-flight for that file: **0 NUL, 0 lone-surrogate, 0 control, 0 non-ASCII** — for sha `e32357d6…` only.
**cai's "NUL byte" was RETRACTED (CAI-588)** — never existed. The atomicity block survives on *"money and audit share a fate"*.
**Audit chain, two live defects:** COVERAGE 28 fully / 12 partially / 636 not (the 12 = 9 modules + **2 tax_settings incl. tax reg no** + 1 org profile) · ATTRIBUTION **660/676 name a TEST account**.

---

## 5. ↩️ CLAIMS I WITHDREW (do not re-derive)

- "Gazzabyte had already signed in" → the **burned invite** (23s after `invited_at`, 0 sessions). **Never infer acceptance from `last_sign_in_at`.**
- "They can't be unstuck until templates land" → a working password existed since 13:41Z.
- "The lane resolved who signed in" → it didn't; **the CLIENT** did.
- "delivered=false proves failure detection worked" → those 27 rows are **drafts**.
- "Removing the Gazzabyte seat is safe" → Studio-only evidence; the Mini authenticates as the partner.
- **"mig 120 was applied on the operator's override"** → **cai had granted 3 minutes earlier.**
- "nazim_say.sh is unguarded" → it exits before logging. Right conclusion earlier, wrong reasoning.

---

## 6. ⚠️ TRAPS ON THIS BOX (verified)

- **`grep` AND `find` are wrapped shell functions** → gitignore-aware, **cannot see `.env*`**. Only `os.walk` in Python **with a visible positive control** is admissible.
- **A positive control must test COVERAGE, not sensitivity.** Put it where a *boundary* error would put it — outside the assumed root, on the other host.
- **`| tail` eats the exit code.** Capture exit codes DIRECTLY.
- **Playwright's pinned `chromium-1217` is a CORRUPT build** (SIGABRT on a missing dylib). Use `chromium_headless_shell-1228` via `executablePath`.
- `timeout` absent on macOS · `git ls-tree` needs `-r` · **`UID` reserved in zsh** · long inline python needs `<<'PYEOF'`.
- `agent_messages.message_type` ∈ review_request/question/decision/agreed/challenge/update/blocker/counter; body col is **`body`**. `session_digests` uses **ARRAY** columns.
- **Pushing `main` auto-deploys BOTH ihsanos projects to PRODUCTION.**
- Supabase Management API 403s from `urllib` (Cloudflare `1010`) — **use `curl`**.
- ihsanos prod is under Vercel team `team_mYxOkemmlg8a3HnKFAE9di7N`, NOT the `.env` VERCEL_TEAM_ID.

---

## 7. 🧭 DOCTRINE ADDED THIS SESSION

- **A handoff is a claim by someone who cannot be asked — it deserves MORE suspicion, not less.**
- **An acceptance criterion over a growing set must be a PROPERTY, never a frozen count** (cai).
- **A remediation that produces the outage it was written to prevent is not a remediation** (cai).
- **Never instruct a secret to travel over a messaging channel** — placed by the operator, at the machine.
- **A password is NEVER shared; the reason is ATTRIBUTION, not secrecy.**
- **A two-person control with a non-human half is a one-person control with extra steps** (cai).
- **"Please try again" must never be the copy on an unsatisfiable failure** (cai).
- **When a human's behaviour contradicts your instrumentation, believe the human** (Nazim).
- **A column name is not an implementation** (cai). **A flag's presence is not the property either.**
- **One message from the body already in the thread beats correct territory** (cai).
- **A grant whose provenance is misrepresented is worse than no rule** (cai) — including when the misrepresentation flatters your own discipline.

---

## 8. 🤖 FLEET STATE

- **cai** — alive, ~100% context, granting and ruling normally. Closed CAI-600's window early **by his own act** and said so rather than implying it elapsed.
- **Nazim / orch-console** — auto-compacts; covering the operator thread when the hub goes quiet. Has adopted a pre-send check against duplication. Flags our context gauge is wrong (#11515).
- **Hub (me)** — holds `orch_lease`; `fleet_health_lease` on the Mini.

---

## 9. ▶️ NEXT ACTIONS (in order)

1. **GIRO for Elly** — the unsent composer item. Client raised it 2026-07-24 and it has never been scoped. **Nothing promised to them.** Start by reading #6911/#7157 and establishing what "giro access" and "giro reconciliation" actually mean before touching anything.
2. **Answer bus #11515** (Nazim, P1 — context gauge nominal vs usable).
3. **Wait for the operator to run the token commands**; then gate step 3 **on BOTH hosts** before the seat removal. Do **not** re-send the commands.
4. **After the windows close today** (11:24 / 13:19 / 14:54Z) surface 121/122 to cai. Do not press early.
5. Task #10 agent-as-signatory: durable service-account property, never a name pattern.
6. Then CAI-586 · CAI-578 restore proof · money/audit shared fate · fleet identity.
7. **Every turn:** reconcile `operator_log.unprocessed()` + `agent_messages`; stamp `read_at` AND `responded_at`.
