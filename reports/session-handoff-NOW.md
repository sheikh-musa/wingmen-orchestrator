# Session handoff — 2026-07-25 ~22:20 SGT / 14:20Z (cc-orchestrator / hub, Studio)

**RESTORE POINT — session ended here by operator instruction ("reset and continue") at ~14:55Z / 22:55 SGT.**
This file is current: written fresh at 90%, amended for the template publication and for CAI-586. Digests **873 / 874 / 875**.
**A fresh hub should read §0 first, then §2, then §7.** Nothing is mid-flight that a reset corrupts — no agents running, both branches pushed, nothing applied to any silo, no grant assumed.

**FRESH FILE at 90% context.** Supersedes `session-handoff-20260725-1350Z-ARCHIVE.md` (→1320Z→1245Z→1145Z→1100Z→0430Z).

---

# 🚨 0. TWO OPERATOR DASHBOARD ACTIONS BLOCK A LIVE CLIENT
Neither is executable by the hub. Both were sent to him.

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
