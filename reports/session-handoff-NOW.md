# Session handoff — 2026-07-25 ~22:20 SGT / 14:20Z (cc-orchestrator / hub, Studio)

---
## 🆕 DELTA — fresh hub session, 2026-07-25 18:51Z → 19:40Z (op#7220 reset). READ THIS BEFORE §0.
Both inboxes reconciled to zero. Lease held. Operator pinged. Nothing applied to any silo; **the bank-import gate is still HELD**.

1. **§4b gap 1 CLOSED — real-corpus per-shape RPC proof.** 945 real OCBC credits / S$222,463.79 through the real `append_audit_log`/`_batch` (121+122) on throwaway local PG. 8 measured classes by name + full 945-row chain, `hash_version=2`, 0 broken links, rooted at genesis; tamper control detects amount/narrative/delete at the exact index. **Byte pre-flight: 0 NUL, 0 lone-surrogate, 0 control, 0 non-ASCII — BLOCKING COUNT 0.** Adversarially verified from scratch on a separate DB: all 7 claims CONFIRMED. Report: `reports/real-corpus-rpc-proof-20260725.md`.
   - **Negative result, both axes:** all 945 payloads are FLAT ⇒ v1==v2 byte-identical, so the real corpus proves **CORRECTNESS, NOT DETECTION** (25 rows declared v1 verify GREEN); and it has zero non-ASCII bytes so it cannot exercise the UTF-8 axis either. **The synthetic nested shapes stay load-bearing.**
   - **Count correction:** "29/29" was at `a3d6497`; at `c440136` that suite is **32** tests.
   - `c440136` **NOT moved** — proof lives in gitignored `.mif-samples/`; commit it as a regression test only AFTER cai's confirm-match.
2. **🔴 CAI-587 — NEW BLOCKING DEFECT, cai CONFIRMED it.** `verifyChain` reported `{valid:true}` on a PARTIAL chain: `verifyChainIntegrity` never asserted row 0 is genesis + un-ranged `.select("*")` + PostgREST silently caps at 1000 (measured 1000/10205, HTTP 200, no error). irsyad is **678** rows; +945 = **1,623**. **FIXED + negative control** on a real PostgREST at db-max-rows=1000, 1,623-row chain, row tampered at index 1400 beyond the cap: OLD → examined 1000/1623, `{valid:true}` FALSE VERIFIED; NEW → **BROKEN at 1400**; truncated read → **INCOMPLETE**. Branch `fix/cai-587-verify-completeness` @ **3f42357**, pushed with hooks. Suite 2025 passed / 0 failed. **AUTHORED-UNAPPLIED, not merged — Elly gains a 4th prerequisite (e), satisfied in CODE not in PRODUCTION.**
3. **v4 CONFIRMED SAFE** against the verifier's ACTUAL fetch, not arithmetic: at run time the scope held **676** and the run fetched **676** ⇒ complete. (+2 rows since, ids 3143/3144 → 678 now.) v4 needs no change.
4. **CAI-561 was NOT unauthored** — the handoff was wrong. `121_audit_log_hash_version.sql:146-147` ALREADY executes `REVOKE ALL … FROM anon` + `REVOKE UPDATE, DELETE, TRUNCATE … FROM authenticated`. Applying 121 closes it. Exposure calibrated: RLS has INSERT+SELECT policies only ⇒ DML grants affect **0 rows** (verified wet on the substrate in BEGIN/ROLLBACK) = defence-in-depth, **not** a live exploit.
5. **Repo-hygiene DEPLOY-GAP alerts were PHANTOM.** `time.mktime()` parsed Firebase `releaseTime` as local (UTC+8) → every deploy landing within 8h of its tip commit looked undeployed. Fixed `91a6fe4`; verified with the exception handler disarmed. Sweep clean.
6. **cai is at 100% CONTEXT** with background agents running (observed 19:40Z) — it is working, not stuck; the `IDLE_UNSENT` watchdog row was a false positive. Do **not** pile on nudges. Bus to cai: **#11366, #11369, #11370, #11372** (#11372 = the CAI-587 fix review, P1 requires_response).
7. **Correction owed to cai and already sent:** CAI-587 §G calls the OCBC corpus "the one with a NUL byte". It is not — 0 NUL. **cai RETRACTED it in CAI-588** (he had inherited it from his own handoff and cited it 3× as measured). **The atomicity block SURVIVES unchanged** — CAI-574 rested on "money and audit share a fate", not on the NUL byte.
8. **CAI-588 — merge conditions MET and posted, branch now @ `f3c3ce9`.** cai accepts the CAI-587 fix as a **CODE MERGE, not a §6.6 grant** (vocabulary binding — grants name file+number+sha), conditional on two controls, both now posted:
   - **(i) POSITIVE:** `1623 rows (> cap 1000), untampered, paged in full → VERIFIED examined 1623/1623` — it is not biased toward INCOMPLETE (CAI-564's permanently-red-gate risk).
   - **(ii) NO REGRESSION:** ⚠️ **cai's stated criterion was arithmetically impossible** — he required "exactly 40/636 at 678", but 40+636=**676**. **OBSERVED at 678: `UNVERIFIABLE_PRE_FIX / 42 / 636`**, and **OLD and NEW call shapes return the IDENTICAL verdict** (that, not the absolute numbers, is the no-regression proof). The v4-era **676**-row subset reproduces **exactly 40/636**. The +2 are ids **3143/3144** (invite rows, 12:26Z/12:28Z) which both reproduce → 40+2=42. Correct invariant: *the 676 subset still returns 40/636*, not a frozen 40/636 at a growing row count.
9. **CAI-561 scope CONFIRMED (cai's 77 is exact).** goumlyne: 85 public tables, **anon holds TRUNCATE on 77** (authenticated 78). **121 revokes on `public.audit_log` ONLY ⇒ closes 1 of 77, leaves 76 OPEN.** ceayj: **ZERO** anon-TRUNCATE tables — so the TRUNCATE exposure is **goumlyne-specific** and applying 121 to both silos does NOT mean the same remediation occurred in both. Before the 121 grant: name **both effects** in the grant body (column add AND privilege revocation on a live client table), and post `has_table_privilege` proof **per silo after apply**.
10. **Bus to cai:** #11366, #11369, #11370, #11372, **#11377** (latest, P1). Inbox at zero, all `requires_response` stamped.
---


**⏰ DATE ROLLOVER — IT IS NOW 2026-07-26. cai's CHALLENGE WINDOWS CLOSE *TODAY*, NOT "in ~20h":**
`CAI-576 → 2026-07-26T11:24:32Z` · `CAI-584 → 2026-07-26T13:19:25Z`. After those times the clock objection falls away and only cai's **confirm-match at file+number+sha** plus the **real-corpus per-shape proof** (§4b) remain between here and the 121/122 grants. **Do not press him before those times; do surface it after.**

**RESTORE POINT — session ended here by operator instruction ("reset and continue") at ~14:55Z / 22:55 SGT.**
This file is current: written fresh at 90%, amended for the template publication and for CAI-586. Digests **873 / 874 / 875**.
**A fresh hub should read §0 first, then §2, then §7.** Nothing is mid-flight that a reset corrupts — no agents running, both branches pushed, nothing applied to any silo, no grant assumed.

**FRESH FILE at 90% context.** Supersedes `session-handoff-20260725-1350Z-ARCHIVE.md` (→1320Z→1245Z→1145Z→1100Z→0430Z).

---

# 🚨 0. OPERATOR ACTIONS — (a) ✅ CLOSED 22:07Z · (b) ✅ CLOSED 22:48Z · (c) 🔑 token to ROTATE

## ✅ (a) VERCEL KEY — **RESOLVED AND VERIFIED 2026-07-25 22:07Z. Gazzabyte hold RELEASED.**
Operator rotated + redeployed at 22:00Z; **first attempt FAILED** (`database:error`) — the pasted key carried **whitespace**. Caught by driving the real server-side path, not a page load. He re-pasted at 22:07:39Z, redeploy 22:07:48Z (9s later, right order).
**VERIFIED:** `/api/health` on live production = `{"app":"ok","database":"ok"}` × 3 stable — that route calls `createServiceClient()` and really queries goumlyne. Control: `ihsanos.com` = `ok` throughout on the same commit, so the isolation was to the irsyad env, never the code.
**Method worth reusing:** `/api/health` is the definitive probe for this class — the site returns 200 on `/` and `/login` in BOTH states, so page codes prove nothing.
~~**Gazzabyte had already signed in 12:29Z** — login was never the broken part.~~ **STRUCK — I WAS WRONG** (retract-in-place). `last_sign_in_at 12:29:18Z` is **23s after `invited_at` 12:28:55Z`, with `sessions=0` and `refresh_tokens=0` ever** — that is the **INVITE LINK BEING BURNED** by their mail scanner, i.e. a *symptom of the defect*, which I read as evidence things were fine. Operator caught it, not me. **NEVER infer acceptance from `last_sign_in_at`** (migration 123's own COMMENT says exactly this). Client notified 22:12:49Z to retry failed actions — that message did not claim their login worked.

## 🔴 (c) IDENTITY FINDING — the hub's `SUPABASE_ACCESS_TOKEN` is the PARTNER's, not ours
**Measured:** `GET /v1/profile` → `primary_email = sales@gazzabyte.sg`. Format `sbp_` (personal access token).
Org **`lqbojdqwgzgxhioezfgb` "sheikh-musa's Org" owns BOTH** `goumlynecruxrlmzlntp` (irsyad) **and** `ceayjeamtmcyzzvqflus` (ihsanos). Members: `sheikh.musa@outlook.com` = **Owner**, `sales@gazzabyte.sg` = **Developer**.
- **Developer can read project config, not write it** ⇒ that is the entire GET-200/PATCH-403 story. No key swap fixes it; it is a ROLE.
- **KILLS A TWICE-REPEATED MIS-DIAGNOSIS:** the handoff's "403 because the project moved to his account" is **wrong** — goumlyne never left his org.
- **Attribution is inverted:** every Management-API action the hub takes is recorded as the client contact, not us — the exact inverse of the row-3144 agent-identity pattern cai praised.
- **Fix (operator, ~30s):** issue a PAT from his own Owner account → `.env` on the Studio; revoke the gazzabyte token; drop the Developer seat unless needed. Asked; not done. Sent to cai as **#11391**.

## ✅ (b) SUPABASE EMAIL TEMPLATES — **APPLIED, AND THE HALF-FIX CAUGHT** (2026-07-25 22:48Z)
All three (invite/magic_link/recovery) PATCHed onto goumlyne via Management API and **verified by read-back, byte-exact**; internal incident comments stripped so client emails carry no internal detail.
**🔴 THE CATCH — templates ALONE only MOVED the burn.** Driven, not assumed, on a synthetic account:
`BEFORE main@f3c3ce9` prefetch → **307, 0 bytes**, token then **403 otp_expired = BURNED**.
The deployed `/auth/confirm` consumed `token_hash` on a bare GET (its own docstring said so); the prefetch-safe route was on the **unmerged** fix branch. Had I stopped at "templates applied + verified" I would have reported a fix that wasn't one.
`AFTER main@231714a` prefetch → **200, 2456 bytes, real interstitial**; token then **200, session issued = SURVIVED**. Control throughout: fresh token, no prefetch → 200, so the route was the variable, not the tokens.
**Merged** `fix/invite-prefetch-safe-resend` (cai had already approved that code). Pre-push check that mattered: it carries **migration 123, which is COMMENT-ONLY** and `accepted_at` already exists on both silos ⇒ no unapplied-schema dependency. 207 files / 2049 tests / 0 failed, tsc clean, `next build` exit **0** (captured directly). Post-deploy both projects `database:ok`.
**Gazzabyte:** real reset triggered via the same endpoint the Forgot-password button uses; `recovery_sent_at` null → **22:48:44Z**. That proves OUR side dispatched it, **not** inbox delivery. Client told on the group 22:49:30Z (`delivered=True`).

## 🔑 TOKEN STATE — hub now authenticates as the OPERATOR
`.env SUPABASE_ACCESS_TOKEN` swapped to the operator's Owner PAT (`sbp_2180…b069`); old partner token (`sbp_f670…4e34`) removed; backup `.env.bak-token-swap-20260725`. `/v1/profile` → `sheikh.musa@outlook.com`. **⚠️ THE NEW TOKEN WAS PASTED INTO TELEGRAM** → row 7256 **SCRUBBED** (0 `sbp_` tokens remain in the log, verified) but treat it as **BURNED — operator must revoke + reissue.** Owner-scoped: it can read api-keys (hence the service_role key) on BOTH projects.

## 🔴 FLEET DEFECT FIXED — THE MESSAGE LOG ASSERTED DELIVERY IT NEVER CHECKED (25701aa)
All three send scripts (`tg_send.sh`, `irsyad_support_send.sh`, `cai_send.sh`) ran the `operator_log` line **unconditionally**, and `operator_log` defaults `delivered=TRUE`. So **a FAILED send was recorded as delivered, on every channel, for as long as those scripts have existed.** The `--undelivered` flag already existed and was never passed. Fixed in all three; verified by driving the real path, not by inspection.
**Caught live:** a client reply failed (`read operation timed out`) yet logged `delivered=True` — so the log could not answer whether the client had our answer. **Corroboration:** the client asked the same question 3× in 6 min while every reply showed delivered, and went silent the instant a send actually succeeded (23:28:48Z).
**OPEN UNKNOWN, not an all-clear:** which past sends actually landed is unrecoverable — the evidence that would settle it is the evidence that was wrong.
⚠️ **`| tail` ate the exit code again** — the script printed failure, my captured code was 0. Capture exit codes DIRECTLY; this trap is in this very file and I hit it anyway.

## 🧪 POSITIVE CONTROLS MUST TEST **COVERAGE**, NOT JUST SENSITIVITY (CAI-597, extended)
cai planted his control INSIDE the search root; so did I (`orchestrator/.env`). Both passed. Both were **structurally incapable of catching a BOUNDARY error** — and the root WAS wrong: the Mini is a different host. I did not find the Mini's live partner token by method; I found it because cai said to look there.
**RULE: a positive control must sit where a boundary error would put it — outside the assumed root, on the other host. A control inside the search space tests sensitivity, never coverage.**

## 🧭 THE SESSION'S ONE DEFECT, IN FIVE SYSTEMS
A check that silently operates on a SUBSET and reports on the WHOLE: (1) `verifyChain` row-cap → false VERIFIED · (2) `grep`/`find` wrapped + gitignore-aware → "clean" from 0 files scanned · (3) the 7-path token gate → Studio-only, missed the Mini · (4) the message log → delivery asserted, never checked · (5) `canonicalPayloadJson` hashing only keys it could see. Same shape, five unrelated layers, one day.

## ↩️ CLAIMS I WITHDREW (retract-in-place)
- "Gazzabyte had already signed in" → that was the **burned invite**, 23s after `invited_at`, 0 sessions.
- "They can't be unstuck until templates land" → a working password had existed since 13:41Z; templates break **self-service reset**, a different thing.
- "The lane resolved who signed in" (to Nazim) → it did **not**; it wrote a reply true either way and said so. **The CLIENT resolved it** at 23:05:49Z. Neither body earned that.

## ✅ GAZZABYTE UNBLOCKED — CONFIRMED BY THE CLIENT, 23:05:49Z
Client's own words: *"sales@gazzabyte.sg able to login and set password."* Silo agrees: `last_sign_in_at 22:51:13Z`, confirmed, `sessions=1` (was 0 for the account's entire life), `requires_password_setup=false`. The session I flagged as ambiguous (`user_agent='node'` — expected, our `/auth/confirm` verifies server-side) **was her**. Reply sent by **Nazim/orch-console 23:12:45Z** under the cc-irsyad supervised-send arrangement — **hub must not duplicate on #7272**.

## 🧪 INSTRUMENT WARNING — SHELL SEARCHES HERE ARE BLIND (CAI-593 §3, extended)
**`grep` AND `find` are both wrapped shell functions** (Claude Code snapshot); `grep -r` honours `.gitignore` so it **cannot see `.env*`**, and my `find` sweeps returned **1** then **0** files while printing "clean" underneath. A negative from an unproven instrument is not evidence.
**RULE: on this box, any negative from a shell search is inadmissible without a positive control in the same command.** The only trustworthy sweep was `os.walk` in Python (1,809 files, control passed).

## ✅ PARTNER TOKEN PURGED FROM DISK (CAI-593 task)
Redacted the `SUPABASE_ACCESS_TOKEN=` line only (all other bytes preserved) in **7** files — cai's 5 in `~/wingmen/orchestrator`, **plus 2 he did not have**: `~/wingmen/_mini_migration_backup/orchestrator-mini/.env` and `.env.bak.goumlyne-sync` (his scan was orchestrator-scoped). Final: **partner token `sbp_f670` = ZERO on disk**; the only full-shape token left is the operator's in `orchestrator/.env`, whose apparent duplicate at `orchestrator-wt/preventative-gates/.env` is a **symlink to the same file**. That token is Gazzabyte's — **not ours to revoke, so deleting our copies is the whole remedy**.

## 🔑 OPEN ACTION — TOKEN SEQUENCE (CAI-592 §B, ordering is the ruling)
`.env` holds the operator's Owner PAT `sbp_2180…b069` — **BURNED** (pasted into Telegram; row 7256 scrubbed, 0 tokens remain in log, but **a scrub is not an unsend**). Required order, and step 4 before step 3 strands the fleet:
1. **REVOKE** the burned token at the provider — *replacing is not enough; an unrevoked token in a chat history stays live.* ("rotate" was my word and it was too weak — cai's correction.)
2. Operator generates a third and **places it himself at the machine** (`.env`). Secrets never travel over a messaging channel.
3. Hub **re-verifies the 7 Management-API paths** — observed, not assumed.
4. **Only then** remove the `sales@gazzabyte.sg` Developer seat.
Verified safe-to-remove today: 7/7 paths 200 as `sheikh.musa@outlook.com`; **no stored CLI credential** (`~/.supabase/access-token` absent; `supabase` CLI not installed on the Studio at all).
**Attribution window is BOUNDED:** fleet Management-API actions before **22:40:45Z** are logged by Supabase as `sales@gazzabyte.sg`; after, as the operator.

## ✅ GAZZABYTE — FIRST EVER SESSION ON THAT ACCOUNT
`recovery_sent_at` 22:48:44Z → **SESSION CREATED 22:51:13Z** (2.5 min later), `updated_at` 22:51:40Z, `requires_password_setup=false`, `recovery_sent_at` cleared (= token genuinely consumed). **Sessions were 0 for the entire life of the account.**
⚠️ `user_agent='node'` is **EXPECTED** (our `/auth/confirm` verifies server-side) — so it does **not** distinguish the client from the operator testing. **Not declared as "she's in"; confirmation requested instead.**
**Link expiry ≠ link burning.** `mailer_otp_exp = 3600` (measured) — links still expire in 1h; the fix stopped them being *consumed by scanners*, not expiring.
**NEVER generate a password for a person** (CAI-592 §D.1): if two people know a credential, no action is attributable to either — the same identity defect as the 660 test-account rows and the scanner-consumed invite.

## ⚠️ cai STATE — 100% CONTEXT, RULING FROM STALE DATA
CAI-592 §D.2 asserted the templates were "NOT yet applied / Safe-Links still burns / no resend path" — **all three false** as of 22:48-22:51Z and already reported to him in #11398. Countered in **#11403**. Carrying that instruction verbatim would have told the operator his live client was still broken *while a session was already open*. **Verify cai's factual premises against live state before acting on them while he is at 100%.** 4 background agents in flight — do **not** reset. Composer unstuck (pen ii), no extra work injected.

## ⏳ (b-old) SUPABASE EMAIL TEMPLATES — superseded by the block above
**Tested, not inherited:** on goumlyne `GET /v1/projects/{ref}/config/auth` → **200** (all 14 templates readable); `PATCH` → **403 insufficient privileges**. The hub token went **read-only** when the project moved to the operator's account. So the templates ARE Management-API-settable — just not by this token. The earlier "403 on api-keys" note was true but too narrow.
**Rollback banked** (cai's first gate condition, done regardless of who applies): all 14 current bodies → `reports/rollback/goumlyne-auth-templates-rollback-20260725.json` (commit `ac6c834`). Confirms the defect is live — invite/magic_link/recovery all still carry stock `{{ .ConfirmationURL }}`, none carry `token_hash`.
**Ask sent to operator:** grant the hub token write on the goumlyne project and the hub owns this end-to-end (apply → trigger real recovery+invite → click → report OBSERVED). If he keeps write to himself, it stays a dashboard paste.

---
### ORIGINAL §0 TEXT (superseded, retained per retract-in-place)
**(a) VERCEL KEY — MOST URGENT.** He rotated the goumlyne `service_role` key. `ihsanos-irsyad` (`prj_AgYdB27PBf7tMLlySjFNPqdoCsKM`) still holds the OLD `SUPABASE_SERVICE_ROLE_KEY` on **production**. Site answers 200 only because `/login` doesn't touch it — **it fails on the first real server-side action.** Fix: update env var → **redeploy production** (no effect until redeploy). Check the `ihsanos` project too.
**HUB UNAFFECTED (verified):** our keys are `tscuy` + `ceayj`; ceayj still authenticates 200. No goumlyne service key in `.env`. My `SUPABASE_ACCESS_TOKEN` now 403s on goumlyne api-keys (narrowed when the project moved to his account).

**(b) SUPABASE EMAIL TEMPLATES.** Paste bodies from `fix/invite-prefetch-safe-resend`: `supabase/templates/{invite,magic_link,recovery}.html` → Dashboard → Auth → Email Templates. **All three** — magic_link carries Resend, recovery is the escape hatch that failed Gazzabyte the second time.
The whole fix is one line: stock `{{ .ConfirmationURL }}` (provider's `/auth/v1/verify`, burns the token on any GET) → `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=…&next=/set-password`.
**PUBLISHED FOR COPY-PASTE: `https://share.wingmen.dev/r/irsyad-email-templates`** (share password; 401 until login = gate working). All three blocks labelled by dashboard template name. Published **from the Mini** — the Studio has the `wingmen-share` dir but NOT `app/docs.json`, so `publish_share.sh` fails there; scp + ssh to `Musa@100.83.21.34`.
**cai's gate:** capture the CURRENT body as rollback FIRST · then actually send + click a test invite · **report OBSERVED, not expected.**

**Gazzabyte is unblocked meanwhile** — password set via admin API + `requires_password_setup` cleared (verified in DB). Hold releasing it until (a) is done.

---

## 1. MECHANICS
Hub (tmux `orch`, Studio, holds `orch_lease`). **Every turn:** reconcile `operator_log.unprocessed()` + `agent_messages` (stamp `read_at` AND `responded_at`).
Substrate `DATABASE_URL`; irsyad `GOUMLYNE_DATABASE_URL`; Mini `Musa@100.83.21.34`.
**Traps:** `timeout` absent on macOS · `git ls-tree` needs `-r` · capture exit codes **directly**, never after `| tail` · **`UID` is reserved in zsh** · beware shell quoting in long inline python — use a `<<'PYEOF'` heredoc · `strategic_decisions` CHECKs (`domain`∈operations/…, `source`∈musa_direct/…, `decided_by`∈cc-orchestrator/…).

## 2. 🔴 LIVE GATE — Elly's 945-row bank-import COMMIT BLOCKED
donations **2,588** · chain **678**. Needs (1) hash-version+RPC **@c440136 authored-unapplied**, (2) recursive sorter (same branch), (3) **money/audit shared fate — NOT STARTED, cai to rule shape**.
Blocked = **only the committing write**. Override = explicit operator decision **with the consequence disclosed to the client in the same message**. Elly's file is clean. **Pre-flight: scan narratives for NUL / lone surrogates** (audit batch is ONE atomic INSERT while donations commit best-effort ⇒ 945 money rows, ZERO audit rows).

## 3. 🔴 AUDIT CHAIN — TWO INDEPENDENT DEFECTS
**(a) COVERAGE:** replacer allowlist at every nesting level. Row 1: **28 fully / 12 partially / 636 not**. The 12 = 9 modules + **2 tax_settings (9% rate, tax reg no)** + 1 org profile. Discriminator = **WRITER CLASS**, not id/date. Money-bearing rows are in the protected 28; no `donation` entity_type exists.
**(b) ATTRIBUTION:** **660/676 (97.6%) name a TEST account**; 5 real.
**Invariants:** never re-hash history · no id allowlists · never hand-write audit rows · never grant membership by raw SQL · `verifyChainIntegrity` without a boundary is STRICT by design (not a regression).

## 4. ⏳ OPEN THREADS
| Item | State | Owner |
|---|---|---|
| **§0 (a) Vercel key + redeploy** · **(b) templates** | **blocking a live client** | operator |
| **mig 121+122 grants** | ⛔ **NO GRANT (CAI-586) — the CHALLENGE WINDOWS are open ~20h**: CAI-576 until **2026-07-26T11:24:32Z**, CAI-584 until **13:19:25Z**. *A window that yields the moment everything looks ready is a formality.* **Do NOT press to shorten.** STILL OUTSTANDING beyond the clock: **(i) per-shape RPC proof over the REAL OCBC narrative corpus — the 29/29 used SYNTHETIC shapes only, this is a genuine gap, QUEUED NOT STARTED**; (ii) cai's confirm-match at file+number+sha. `feat/audit-chain-version-integration` @ **c440136**. Other hub-side conditions met: 121 re-runnable ✓ · enumeration reconciled (9 files/18 call sites, command recorded) ✓ · per-shape RPC proof ✓. 212 files/2145 tests/0 failed. **Awaiting cai confirm-match at file+number+sha.** ⚠️ 121's real defect was a **SILENT DUPLICATE** (1→1→**2**→2) not a died-mid-apply; negative control reproduces the duplicate-key death, and the fix **stands alone without 122's mitigation**. | cai grants |
| **onboarding fix** | `fix/invite-prefetch-safe-resend` @ **fe737b5**, hub-verified by **breaking it** (old route → 9 failed incl. the test named after the incident; restored → 10 passed). **Code approved by cai; live template change GATED (§0b).** 206 files/2043 passed. | operator applies |
| **v4 client correction** | **HUB-CLEARED**, cai batching to operator with the entity ask. **NOT SENT.** | operator |
| **money/audit shared fate** | NOT STARTED — cai to rule shape (single txn / compensating rollback / loss-proof outbox) | cai→hub |
| **CAI-561 revocation migration** | anon+authenticated hold U/D/TRUNCATE on `audit_log` both silos. **NOT authored.** | hub |
| **CAI-578 EXIT proof** | **Gate is a RESTORE, not an export**: clean-db restore → per-table counts · money **2,587 / S$1,718,341.46 exact** · `verifyChain` on the restored copy. Per check. Binding: no new client project into a personally-owned org; **do NOT migrate goumlyne**. | hub |
| agreement in git | **RESOLVED (CAI-585): STAYS**, no force-push (desyncs clones). Marked **CONFIDENTIAL**, grandfathered as exception-not-precedent; forward policy = signed instruments not in code repos by default. Axis = **readership**, not permanence. | done |
| lane_tasks #41 · GIRO · eNETS · PITR · captcha · A2 guard · CAI-579 entity | unchanged, no dates promised | various |

## 4b. 📋 QUEUED, NOT STARTED (I ran out of context)
- **Per-shape RPC proof over the REAL bank-narrative corpus** (945 credits / S$222,463.79, already parsed — no silo needed). The existing proof was synthetic-only.
- **Make the pre-push prod smoke STRUCTURALLY read-only** (CAI-586): today it is read-only *by inspection*, which is an act of remembering, and remembering fails silently. Give it a role that **cannot** write, and get prod out of the push path entirely.

## 5. ⚠️ RESIDUALS WORTH KNOWING
- **The repo's pre-push hook runs a read-only role-access smoke against ihsanos PRODUCTION on every push** (7/7; zero write calls verified). Not a violation, but know it rather than discover it.
- **`check-schema-drift` is red on pristine `origin/main`** (8 findings in `order-fulfillment`/`place-order`/`storefront-location`) — verified in a clean worktree. Branches in this lane need `--no-verify` until someone fixes it. **Do not treat that as licence to bypass other hooks.**
- **121 concurrency:** `WHERE NOT EXISTS` — two *simultaneous* applies could both insert. Theoretical under single-apply; `LOCK TABLE … EXCLUSIVE` closes it.
- **Resend still emails**, so it's burnable until §0(b) lands. Still worth having: previously a stuck member was unrecoverable without deletion.

## 6. 🧭 DOCTRINE
- **"Serializer unchanged" is CODE; "preimage unchanged" is DATA** — prove **per payload shape, never globally**.
- **Retract-in-place**: superseded explanations struck, not deleted. **D4 non-backfill is doctrine** — inventing timestamps *launders a known-bad column*; same argument as never re-hashing history.
- **After establishing a discriminator, the next error is applying it through a PROXY you never measured.**
- **Verify a guard by BREAKING what it guards.** A guard protects its axis, blind everywhere else.
- **A characterisation in a citable record must be a FIELD, not a sentence.**
- **A hand-written list of a mechanically-derivable set is a ticket, not a guarantee.**
- **"Individually correct, wrong together"** — a named category; per-artifact review structurally cannot find them.
- **cai's gate is STOP-AND-DISCLOSE, never a veto over the operator.** He is the principal.
- **A rotated key that something still holds isn't rotated, it's broken.**
- **The fix is not always where the bug appears** — the invite burn happened at the *provider*, upstream of all our code.
- **STATE THE CONCERN AND THE MECHANISM SEPARATELY** (cai, 3rd instance today of *his mechanism wrong / concern correct*). A wrong mechanism must not take a correct concern down with it — the negative control is what kept the 121 defect alive after his predicted failure mode proved wrong. My inverse shape: measurement right, summary wrong.
- ⚠️ **MY PATTERN, five instances:** measurements held; **my summaries shrank or over-read**. Latest: I checked the repo was private and *still* didn't think about readership — **being right about permanence made me stop looking.**

## 7. ▶️ NEXT ACTIONS
1. Chase **§0(a) then §0(b)**; release Gazzabyte's password after (a).
2. Reconcile both inboxes; stamp `read_at` AND `responded_at`.
3. Await cai's confirm-match → then the 121/122 grants.
4. Author **CAI-561 revocation migration**; run the **CAI-578 restore proof**; scope **money/audit shared fate**.
5. **Nothing applied to a client silo without a cai §6.6 named-file grant.** Hold the bank-import gate — **progress ≠ lifting**.
