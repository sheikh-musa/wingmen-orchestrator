# Session handoff — 2026-07-26 00:20Z / 08:20 SGT (cc-orchestrator / hub, Mac-Studio)

**FRESH FILE at ~60% context.** Supersedes `session-handoff-20260725-1420Z-ARCHIVE.md`.
**Read order for a fresh hub: §0 → §1 → §2 → §9.**
Nothing is mid-flight that a reset corrupts. Nothing applied to any silo. No grant assumed or issued.

---

## 0. 🛑 STANDING HOLDS — READ BEFORE DOING ANYTHING

1. **THE OPERATOR IS DRIVING** (since `00:03:40Z`: *"got all of it. driving now"*). **Send him nothing** until he says he has stopped. **Nothing in the token sequence (§1) executes while he is at the wheel.**
2. **Bank-import commit is HELD** (§3). Progress ≠ lifting.
3. **`feat/audit-chain-version-integration` MUST STAY AT `c440136`** — cai's confirm-match is pinned there. Do not push to it.
4. **NO GRANTS EXIST** for 119 / 121 / 122 / 124. cai issued **zero** this session. Windows close today: CAI-576 `11:24:32Z` · CAI-584 `13:19:25Z` · CAI-586 `14:54:07Z`. **Do not press him early.**
5. **My unsent composer text was: `fix the mobile view-as switcher`** — that is task #7 (§2), mine to re-decide, not an instruction from anyone.

---

## 1. 🔑 TOKEN REMEDIATION — HALF DONE, BLOCKED ON THE OPERATOR

**Both hosts currently hold compromised credentials.**

| Host | State |
|---|---|
| **Studio** `.env` | operator's Owner PAT `sbp_2180…b069` — **BURNED** (pasted into Telegram 22:38:49Z; row 7256 scrubbed, 0 tokens remain in log, **but a scrub is not an unsend**) |
| **Mini** `.env` | **still authenticates as the PARTNER** `sbp_f670…4e34` (Gazzabyte's staff account) — deliberately NOT redacted; redacting it breaks the Mini |

**REQUIRED SEQUENCE — the ordering IS the ruling (CAI-592/596). Step 4 before step 3 strands the fleet:**
1. **REVOKE** the burned token at the provider — *replacing is not enough; an unrevoked token in a chat history stays live.* ("rotate" was my word and it was too weak.)
2. Operator generates a third and **places it himself** in `.env` on **STUDIO and MINI**. Secrets never travel over a messaging channel.
3. **Hub re-verifies the Management-API paths on BOTH hosts** — observed, not assumed. *(My first gate was Studio-only and missed the Mini entirely — cai caught it.)*
4. **Only then** remove the `sales@gazzabyte.sg` Developer seat.

**Verified so far:** 7/7 paths 200 as `sheikh.musa@outlook.com` (Studio) · no stored CLI credential (`~/.supabase/access-token` absent; `supabase` CLI not installed on Studio).
**Attribution window is BOUNDED:** fleet Management-API actions before `22:40:45Z` are logged by Supabase as `sales@gazzabyte.sg`; after, as the operator.
**Partner token purged from 9 backup files** across both machines (only the `SUPABASE_ACCESS_TOKEN=` line; all other bytes preserved). It is Gazzabyte's — **not ours to revoke, so deleting our copies is the whole remedy.**
**Also found:** `sbp_6707…74a8` in 7 Mini `.env.local` files — **tested, already `Unauthorized`**. Stale litter, not exposure. Do not inflate it.

---

## 2. ▶️ OPEN THREADS

| # | Item | State | Owner |
|---|---|---|---|
| 1 | **Token sequence** (§1) | **BLOCKED — operator driving** | operator |
| 2 | **Mobile view-as defect** (task #7) | client-found; **must NOT ship blind** | needs owner (Nazim routing, bus #11465 unread) |
| 3 | **mig 121+122 grants** | ⛔ no grant; windows close today; awaiting cai confirm-match at file+number+sha | cai |
| 4 | **mig 124** (CAI-561 rest) | authored-unapplied @ `9e484f3`, own §6.6 grant required | cai |
| 5 | **Elly's 945-row bank import** | **HELD** — now **4** prerequisites (§3) | cai |
| 6 | **CAI-586** pre-push prod smoke → structurally read-only | queued, not started | hub |
| 7 | **CAI-578 EXIT proof** (a RESTORE, not an export) | not started | hub |
| 8 | **money/audit shared fate** | cai to rule shape | cai |
| 9 | **Fleet identity as first-class** (CAI-591 §D) | scope as its own work — keeps surfacing as a symptom in unrelated lanes | hub |

**Mobile view-as (task #7) detail:** `src/app/dashboard/_components/view-as-controls.tsx:85` → `className="relative hidden sm:block"` hides the switcher below Tailwind `sm` (640px). **On a phone the only entry point to the feature does not render at all**, for a client who works from one. The *banner* (~:33) IS mobile-aware, so the EXIT path works while the ENTRY path doesn't — oversight, not decision.
⚠️ **Do NOT ship as a one-class fix.** `main` auto-deploys to production on push, and the mobile top bar already carries dark-mode toggle + role badge + avatar. Needs a real mobile render check. Client has a Request-Desktop-Site workaround.

---

## 3. 🔴 ELLY'S BANK IMPORT — FOUR PREREQUISITES

donations **2,588** · chain **678** (was 676; ids 3143/3144 added 12:26/12:28Z, both reproduce).
(a) hash-version+RPC @ `c440136` authored-unapplied · (b) recursive sorter · (c) **money/audit shared fate — NOT STARTED** · (d) **NEW: CAI-587 completeness fix — in CODE, not in PRODUCTION on the silo.**
**Pre-flight done for this file:** byte scan = **0 NUL, 0 lone-surrogate, 0 control, 0 non-ASCII**. Blocking count **0** — for sha `e32357d6…` only, not a forward claim.
**⚠️ cai's "NUL byte" was RETRACTED (CAI-588)** — it never existed; he inherited it from his own handoff and cited it 3× as measured. **The atomicity block SURVIVES** because CAI-574 rested on *"money and audit share a fate"*, not on the NUL byte.

**Audit chain, two independent live defects:** (a) COVERAGE — replacer allowlist at every nesting level; **28 fully / 12 partially / 636 not**. The 12 = 9 modules + **2 tax_settings (9% rate, tax reg no)** + 1 org profile. (b) ATTRIBUTION — **660/676 name a TEST account**.
**Invariants:** never re-hash history · no id allowlists · never hand-write audit rows · never grant membership by raw SQL · `verifyChainIntegrity` without a boundary is STRICT by design.

---

## 4. ✅ WHAT SHIPPED THIS SESSION (verified by driving, not by tests alone)

- **`main` `c9aa899` → `231714a`.** Two merges: CAI-587 completeness assertion + the prefetch-safe `/auth/confirm` route. Real `next build` exit **0** captured directly; 2049 tests / 0 failed. Both Vercel prod deploys READY; all four surfaces 200.
- **§4b gap CLOSED** — real-corpus per-shape RPC proof over 945 real OCBC credits / S$222,463.79. Adversarially verified from scratch on a separate DB: all 7 claims CONFIRMED. Report: `reports/real-corpus-rpc-proof-20260725.md`.
  - **Negative result, both axes:** all 945 payloads are FLAT ⇒ v1==v2 byte-identical, so the real corpus proves **CORRECTNESS, NOT DETECTION**; and it has zero non-ASCII bytes so it cannot exercise the UTF-8 axis either. **Synthetic nested shapes stay load-bearing.**
  - **Count correction:** "29/29" was at `a3d6497`; at `c440136` that suite is **32** tests.
- **CAI-587** — `verifyChain` reported `{valid:true}` on a PARTIAL chain (no genesis assertion + un-ranged select + PostgREST silent 1000-row cap; irsyad 678 → 1,623 post-import). Fixed with a **completeness assertion, not a bigger cap**. Negative control on a real PostgREST at `db-max-rows=1000`, tamper at index 1400 **beyond** the cap: OLD examined 1000/1623 → `{valid:true}`; NEW → **BROKEN at 1400**; truncated → **INCOMPLETE**. Positive control: 1623 untampered → **VERIFIED**.
- **§0(a) VERCEL KEY** — closed 22:07Z. First attempt failed on **whitespace**; caught by driving `/api/health` (a real service-role query), not page codes. `/` and `/login` return 200 in **both** broken and working states — page codes prove nothing.
- **§0(b) EMAIL TEMPLATES** — applied live to goumlyne, read back byte-exact. **The catch: templates ALONE only MOVED the burn** to our own URL. Merging the prefetch-safe route closed it. Proven: prefetch 307/burned → 200/interstitial/**survived**.
- **GAZZABYTE UNBLOCKED — confirmed in their own words** 23:05:49Z. Account went from **0 sessions in its entire life** to a working login.
- **Repo-hygiene phantom DEPLOY-GAPs** — `time.mktime()` parsed Firebase `releaseTime` as local (UTC+8), so any deploy within 8h of its tip commit looked undeployed. Fixed `91a6fe4`.
- **Comms delivery-log defect** — see §5.

---

## 5. 🧭 THE HOUSE DEFECT — FIVE INSTANCES, ONE SHAPE

**A check that silently operates on a SUBSET and reports on the WHOLE.**
1. `verifyChain` read 1000 of 1623 rows → `valid`
2. `grep`/`find` (both **wrapped shell functions**, gitignore-aware) → "clean" from **0 files scanned**
3. token gate on 1 of 2 hosts (Studio only; missed the Mini)
4. **message log asserted delivery it never observed**
5. `canonicalPayloadJson` hashed only the keys it could see

**cai's diagnosis, adopted:** *"None of them lied. Each reported truthfully on a scope narrower than the claim it supported, and the output was INDISTINGUISHABLE from a correct check. That indistinguishability is the disease — vigilance reads the same green."*
**Corollary, evidenced 3× tonight: the cheapest detector is a SECOND VANTAGE POINT, not a harder look.** cai found my Mini gap · Nazim's *"why 3× while delivered=true"* found the log defect · **the CLIENT** found the mobile defect and closed the session ambiguity. None were available from inside the checking body.
**Rule: every check states its boundary; every claim declares which side of it it sits on.**

### The comms defect (fixed `25701aa`)
All three send scripts ran the `operator_log` line **unconditionally**; `operator_log` defaults `delivered=TRUE`. So **a FAILED send was recorded as delivered, on every channel**. `--undelivered` existed and was never passed. Fixed in all three, verified by driving the real path.
⚠️ **`delivered` is SEMANTICALLY OVERLOADED — read it ONLY with `tag`:** `false` + `*-draft`/`*-drill` = **not sent by design** (all 27 historical false rows); `false` + any other tag = genuinely failed (only possible since `25701aa`).
🔴 **OPEN UNKNOWN, not an all-clear:** there is **ZERO** record of any genuinely failed send before `25701aa`, on any channel, for the life of the log. **Past delivery is unauditable.** If the operator or a client says *"I never got that"*, our log **cannot contradict them**.

---

## 6. ↩️ CLAIMS I WITHDREW (retract-in-place — do not re-derive them)

- **"Gazzabyte had already signed in"** → that was the **burned invite**: `last_sign_in_at` 23s after `invited_at`, **0 sessions ever**. **NEVER infer acceptance from `last_sign_in_at`** — migration 123's own column comment says exactly this.
- **"They can't be unstuck until templates land"** → a working password had existed since 13:41Z; templates break **self-service reset**, a different thing.
- **"The lane resolved who signed in"** (to Nazim) → it did **not**; it wrote a reply true either way and said so. **The CLIENT** resolved it. Neither body earned it.
- **"delivered=false proves failure detection worked"** → nearly cited **drafts** as evidence. Caught before sending.
- **"Removing the Gazzabyte seat is safe"** → issued on **Studio-only** evidence; the Mini authenticates as the partner. Corrected to the operator.

---

## 7. ⚠️ TRAPS ON THIS BOX (verified, not folklore)

- **`grep` AND `find` are wrapped shell functions** → gitignore-aware, **cannot see `.env*`**. My sweeps returned **1** then **0** files while printing "clean". **Only `os.walk` in Python with a visible positive control is admissible.**
- **A positive control must test COVERAGE, not just sensitivity.** cai and I both planted controls *inside* the search root; both passed; both were structurally blind to a **boundary** error — and the root WAS wrong (the Mini). **Put the control where a boundary error would put it — outside the assumed root, on the other host.**
- **`| tail` eats the exit code.** Hit it again tonight: script printed failure, captured code was 0. **Capture exit codes DIRECTLY.**
- `timeout` absent on macOS · `git ls-tree` needs `-r` · **`UID` reserved in zsh** · long inline python needs a `<<'PYEOF'` heredoc.
- `strategic_decisions` CHECKs: `domain`∈operations/… · `source`∈musa_direct/… · `decided_by`∈cc-orchestrator/…
- `agent_messages.message_type` ∈ review_request/question/decision/agreed/challenge/update/blocker/counter. Body column is **`body`**, not `message`.
- **Vercel:** ihsanos prod lives under team `team_mYxOkemmlg8a3HnKFAE9di7N` (`wingmen`), NOT the `.env` VERCEL_TEAM_ID.
- **Pushing `main` auto-deploys BOTH `ihsanos` and `ihsanos-irsyad` to PRODUCTION.** Name it before you push.
- **`check-schema-drift` is red on pristine `origin/main`** — branches in this lane need `--no-verify`. Not licence to bypass other hooks.
- Supabase Management API 403s from `urllib` (Cloudflare `error code: 1010` bot-block) — **use `curl`**.

---

## 8. 🧭 DOCTRINE ADDED THIS SESSION

- **A handoff is a claim by someone who cannot be asked — it deserves MORE suspicion, not less.** Three inherited claims dissolved on first contact tonight: CAI-561 "not authored", the NUL byte, "dashboard-only".
- **An acceptance criterion over a growing set must be a PROPERTY, never a frozen count** (cai). Hard-coded totals expire silently; the dangerous version is someone quietly adjusting expected values until green.
- **A remediation that produces the outage it was written to prevent is not a remediation** (cai).
- **Never instruct a secret to travel over a messaging channel** — secrets are placed **by the operator, at the machine** (cai).
- **A password is NEVER shared. The reason is ATTRIBUTION, not secrecy** — if two people know a credential, no action with it is attributable to either.
- **When a human's behaviour contradicts your instrumentation, believe the human** (Nazim).
- **A column name is not an implementation** (cai).
- **One message from the body already in the thread beats correct territory** (cai, CAI-555).
- **`/api/health` is the definitive probe** for service-role breakage — page codes are 200 in both states.

---

## 9. ▶️ NEXT ACTIONS (in order)

1. **Wait for the operator to stop driving.** Then drive §1 steps 1→4, gating at step 3 on **BOTH hosts**.
2. **Address bus #11465** (Nazim, P2 — mobile view-as needs an owner).
3. **Own or route task #7** (mobile view-as) — with a real mobile render check, never a blind CSS push to a live client.
4. **After the windows close today** (11:24 / 13:19 / 14:54Z) surface the 121/122 grants to cai. Do **not** press early.
5. Then: CAI-586 structural read-only smoke · CAI-578 restore proof · scope fleet identity (§2 #9).
6. **Every turn:** reconcile `operator_log.unprocessed()` + `agent_messages`; stamp `read_at` AND `responded_at`.
