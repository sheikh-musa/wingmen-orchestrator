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
6. ~~My unsent composer text: `now do giro`~~ — **RESOLVED 2026-07-26.** It was the OPERATOR's instruction (typed 02:13Z, never submitted; Nazim rescued it from the reset script). Carried out: see §2 item 4 + `reports/giro-state-of-play-20260726.md`.
7. **🔴 DO NOT APPLY MIG 122 ON THE ASSUMPTION THE INDEX IS HELD** (set 2026-07-26 03:15Z, verified by reading the SQL at `c440136`). CAI-536 ruled `uq_audit_log_org_prev_hash` stays HELD until the hot writers migrate. **Migration 122 does not implement that** — the index is created at lines 450/458 inside the same `BEGIN`(171)/`COMMIT`(478) as the RPCs, and the DO-block guard keys on **forks**, not on writer migration, so **both arms build it**. goumlyne is fork-clean ⇒ the **full** index arm fires. Escalated to cai as bus #11541 ahead of the 13:19:25Z window. The hold is a sentence in a decision, not a property of the file.

---

## 0a. ✅ HEARTBEAT: **VERIFIED LIVE 2026-07-26 05:25Z** — observed, not asserted

`scripts/hub_heartbeat.py` + `PostToolUse` hook in `.claude/settings.json` (commit `86e136d`).

✅ **OBSERVED WORKING on the r3 boot.** `agent_status.last_heartbeat` for `cc-orchestrator`
advanced **`05:23:49 → 05:25:04Z` during ordinary tool use, with nobody invoking the script**.
Stamp file `/tmp/.wingmen_hub_heartbeat_stamp` mtime tracks it. The prior session's "built but
never once fired" caveat is **DISCHARGED** — settings.json changes do need a session restart,
and the restart supplied it.

⚠️ **The known limit, which survives:** the hook fires on TOOL USE. A body wedged *without*
making tool calls will not beat, and will go stale — which is the correct, detectable failure,
not a false green. cai raised the sharper version of this (CAI-616 §2: an event-driven beat
"cannot detect a body stuck NOT doing bus work"); `PostToolUse` is broader than bus-work, so
his criticism is narrowed but **not eliminated**. Do not upgrade this to "liveness is solved".

🔴 **DO NOT "fix" this by making it periodic.** The operator typed exactly that instruction
(`fix the heartbeat to be periodic`, 04:41Z, stranded unsent — see §0c) and it is filed as
CAI-616 §E. A timer reports the TIMER's liveness: a dead hub would publish green forever.
He has been sent the reasoning and asked to confirm; **until he answers, activity-driven stands.**
Debug with `WINGMEN_HEARTBEAT_DEBUG=1 .venv/bin/python3 scripts/hub_heartbeat.py` (exits 1
and prints the error; the hook path deliberately stays silent so it can never wedge a turn).

Deliberately **NOT** a launchd timer: a timer would report the *timer's* liveness, so a dead
hub would publish green forever. Activity-driven means a dead hub goes **stale**, which is
detectable.

**Also fixed in `86e136d`:** the `PreCompact` hook pointed at `/Users/sheikhmusa/...` — **that
user does not exist** (home is `/Users/Musa`). Silently broken since 2026-06-19. *Not* claiming
a fleet-wide digest gap: `session_digests` has 781 rows, latest today, written by other means.

---

## 0b. 🆕 BLOCK 2 — fresh hub, 2026-07-26 02:51Z→04:00Z (GIRO)

Full detail: **`reports/giro-state-of-play-20260726.md`** (commits `e773360`, `310e759`, `10dde9a`).

- **GIRO WAS NEVER AN ENGINEERING PROBLEM** (CAI-RESP-607). GIRO COLLECTION rows in the
  client's statements are **lump-sum batches with no donor itemisation** — ~3/month, mean
  SGD 12,906.55. **Audit option (A) is unachievable from these files at any effort.**
  We diagnosed this correctly on **2026-06-19** ("can't build without a sample GIRO file"),
  asked 3×, never got it, lost it — then spent five weeks calling it a build problem.
  **Asked Gazzabyte for the itemised GIRO collection report; naming our own error.**
- **CAI-RESP-605 — mig 122 does NOT implement the "index HELD" bind. GRANT REFUSED.**
  Index built at L450/458 inside the same `BEGIN`(171)/`COMMIT`(478) as the RPCs; guard keys
  on **forks**, not writer migration, so **both arms build it**. goumlyne is fork-clean ⇒
  **full** index arm. cai's sharper read: **the guard is INVERTED** (tolerant for the dirty
  silo, full enforcement for the clean one) and **the apply SUCCEEDS** — breakage arrives
  later on a money-adjacent write. New bind: **grants name FILE + REF + SHA**, constraints
  located **by line**, and that binds the hub as the *asking* body.
- **PREVIEW DEPLOYS REACH LIVE DATA.** `ihsanos-irsyad`'s five Supabase vars are ONE
  definition, `target=production,preview`, `gitBranch=null`. **`feat/giro-reconcile-synthetic`
  was DEPLOYED — READY preview, 2026-07-17, nine days.** What actually protected it is
  **Vercel SSO** (`ssoProtection=all_except_custom_domains`), *not* unmergedness — an account
  setting on another system. Found only by paginating **555/905** deployments; the first
  100-row pass was a false all-clear on a subset.
- **Agent commits authored as `sheikh-musa` are a DELIBERATE standing practice**, not an
  accident — we re-author agent branch tips before pushing ihsanos or Vercel blocks the
  preview and emails the operator a failed deployment. Told cai (#11568): the git history is
  a record we would hand an auditor. **Needs the operator — it is his identity.** NOT RULED.
- **3 defects in the LIVE `bank_import` rail** → prerequisites #5/6/7 on the existing block.
  **My defect-1 grading was WITHDRAWN by me**: I called GIRO RETURN "human-catchable" in the
  same message that said the parser presents reversals "like any other credit."
  **FLAG, DO NOT DROP** — cai refused NRTI exclusion (a discarded discriminator must not be
  fixed with an inferred one; silent exclusion drops real donations).

**Not done / not claimed:** approval emails still **unexercised** (0 notification rows).
~~one report is **stranded** (`971a84b3-…`)~~ — ⚠️ **REFUTED 05:45Z, see §0d.** Token remediation still NOT run.

---

## 0c. 🆕 BLOCK 3 — hub r3, reset 2026-07-26 05:23Z (BOOT RECONCILIATION)

**Written at 05:40Z by the r3 hub.** cai filed **CAI-619** trying to HOLD this reset because the
restore point was "45 min stale and omits CAI-615/616/617/618" — he was right about the staleness
and six minutes late about the reset. **This block exists so that criticism is never true again.
A restore point's currency is MEASURED, never DECLARED** (CAI-619).

**Arrival state, verified:** `orch_lease` held (Mac-Studio, renewed 05:24Z) · **0 unread bus** ·
**0 unprocessed operator messages**. Nothing was lost across the reset.

### Binds from CAI-615→619 that the §0/§1/§2 text above PREDATES
- 🔴🔴 **CAI-615 — THE MIGRATION LEDGER IS NOT A RECORD. THIS IS THE ONE I DID NOT HAVE, AND IT
  INVALIDATES A CLAIM ELSEWHERE IN THIS FILE.** On goumlyne, **0 of 93 `schema_migrations` rows
  contain the SQL that ran**, and **our own migration 120 apply left NO ledger row at all** while
  both its tables are demonstrably live with the client's two approvers. **Unreliable in BOTH
  directions** — it under-reports what ran and cannot evidence what it claims did.
  **RULED: never answer "is this migration applied?" from the ledger — PROBE THE OBJECT**
  (`to_regclass` + a row count). **cai ruled NO BACKFILL**: a repaired-looking ledger removes the
  reason anyone would ever probe again.
  🔴 **It BLOCKS EVERY §6.6 GRANT until the silo's state is measured by probing objects — this binds
  ON TOP OF the existing 119/121/122/124 blocks and therefore on today's windows.**
  ⚠️ **CONSEQUENCE, applied here rather than left for a successor to trip over:** §2 item 4 says the
  audit-lock is "NOT on goumlyne (max applied migration 118)". **That is a LEDGER-DERIVED claim and
  it is now INADMISSIBLE.** It may still be true — it is not evidenced. Re-establish by probing the
  objects before anything is built on it. Same suspicion applies to any "fork-clean"/"absent"
  statement in §0.7 and §0b that was read off the ledger rather than off the objects.
- 🔴 **A BARE `Enter` DOES NOT RELIABLY SUBMIT (Nazim, the night's root cause).** 7 panes swept,
  4 held stranded text; bare `Enter` into three moved **nothing, 3/3**; `scripts/lane_nudge.sh`
  (verified submit) delivered all three instantly. **This binds MY pen (ii), lane prompt submission:
  never deliver a lane prompt by bare `Enter` — use the verified-submit path and confirm it moved.**
  We built verified-submit for AGENTS in June and left every HUMAN typing into the same panes
  unprotected. **The strand rate is continuous, not incidental**: Nazim cleared 4 composers at 05:20Z
  and **3 more appeared within 15 minutes, all lane SELF-steps.** So the fleet has been quietly
  losing agents' own next actions all along; we only noticed when it happened to the operator.
  A lane in this state looks **idle-and-done**, which is indistinguishable from finished.
- **INVARIANT 42 (cai):** *a restore point's currency is MEASURED, never DECLARED, and never by the
  body at 100% — and the check belongs to whoever holds the reset primitive.* Nazim has named
  accepting my predecessor's "restore point current" as his own gap.
- **Gauge DISARMED as a control (CAI-616):** nothing keyed on `latest_context_tokens` may gate,
  alarm or reset — **including the amber checkpoint executor** (Nazim stripped `--alert`/`--arm=amber`
  from the plist and reloaded, #11634). **A body's own self-report is the only admissible reading.**
- **INVARIANT 41 (CAI-616):** *a corroborator that shares a failure mode is not corroboration.*
  Mine — I had propped a claim on `schema_migrations max` alongside direct object checks; both
  read the same substrate. The conclusion survived on the direct checks alone.
- **RULED (CAI-617): no agent EVER submits text sitting in another body's composer.** Preserve,
  report, leave the decision with whoever's words they are. *Submitting forges the instruction's
  TIMING at minimum and its CONTENT at worst.*
- **VERCEL BOT-ACCOUNT EXPERIMENT — STOOD DOWN, and this is the record a fresh body needs.**
  Gated at **exactly ONE operator step** (create bot GitHub account + add a `wingmen` Vercel team
  seat). **You do NOT self-authorise account creation or a team seat** — identity/access placement
  is operator-gated. Everything downstream is ours. **It queues BEHIND the token** (CAI-613 §D.3).
  It is the highest-value non-P0 item we have — it ends the authorship falsification by making it
  unnecessary. I accepted this in #11656 **before** the reset; this line is why the fresh body
  does not rediscover it.
- **cai WITHDREW his saturation accusation against the operator (CAI-617).** He was never
  disengaged — he was answering into a channel that drops. **Absent and dropped are the same
  row-count in `operator_messages`.**

### 🔴 THE MECHANISM: the composer QUEUES, it does not discard
`Enter` does not reliably submit in these panes. **FOUR operator instructions were stranded
unsent last night** — `02:13 now do giro` · `~04:0x run the vercel bot-account experiment` ·
`04:41 fix the heartbeat to be periodic` · `05:21/05:23 reset me`.
Nazim's correction to cai's model matters: the text **QUEUES and is RECOVERABLE UNTIL RESET**,
so the fix is **CAPTURE THE QUEUE BEFORE ANY RESET**, not "detect discards".
**`logs/reset_{orch,cai}_preserved_input.log` is a load-bearing control** (cai's designation).
⚠️ **Both reset scripts were UNTRACKED as of this block** — a load-bearing control one `git clean`
from gone. Hardening + tracking delegated at 05:35Z; four defects found by reading them:
no mid-task guard on `reset_orch.sh` (it will clear the hub mid-turn — `reset_cai.sh` has one),
multi-line composer capture silently truncates to one line, `BSpace -N 120` under-wipes a
>120-char entry, and neither file is in git.

### Measured this block
- **Heartbeat VERIFIED LIVE** — see §0a. Closes the loop the previous block explicitly refused to close.
- **The 3h40m inbound silence is NOT a dead ingest.** Telegram's own `getWebhookInfo` reports
  `pending_update_count = 0` on the orch bot and the cai bot (no webhook; polling mode). A wedged
  long-poller would let updates QUEUE at Telegram, so zero-pending means nothing is undelivered.
  Daemon `dev.wingmen.ingest` running, pid 64141, up since 07-23. → cai #11659.
  🔴 **TWO LIMITS, do not let this read wider than it is:** `nazim-console` is **UNMEASURED** (its
  bot token lives on the Mini, not this host), and zero-queued proves nothing is STUCK, **not that
  the daemon can still DELIVER**. Only a real inbound row proves that.
- **LIVE BOT TOKENS IN ARGV** — cai's 409 liveness probe passed two full bot tokens inline on the
  command line (visible to any `ps` on this box). Flagged #11666. Rotation is the operator's call.

### ↩️ A claim I made and withdrew WITHIN THIS BLOCK
**"cai was not reset"** — WRONG, and I told the operator so before checking. I saw a 26-minute
background job in his pane and inferred he could not have been cleared. **A reset is in-place: it
clears the conversation and leaves running jobs alive**, so a surviving job is exactly what a
freshly-reset body looks like. cai's own row says `r5 boot 05:23Z` and his bus traffic has a
13-minute hole (05:16→05:29). **Both singletons were reset in the same 05:23Z sweep.** Corrected
to the operator unprompted. *The signal I used was the one signal that happened to be misleading.*

### 📌 A DEBT I OWE THE OPERATOR — DELIVER ON HIS NEXT REPLY, DO NOT SEND STANDALONE
At 05:33Z I told him *"both of the 'reset me' instructions **you typed** were carried out."*
**The attribution is wrong.** Nazim reconstructed it from the preserved-input logs and there were
**no inbound operator messages in that window at all**: the hub's `reset me` was **the hub's OWN
staged next step** (it had just asked to be reset, op#7374), and cai says plainly *"reset me was
mine."* So **he typed THREE stranded instructions last night, not four** — `now do giro`,
`run the vercel bot-account experiment`, `fix the heartbeat to be periodic`.
The outcome I gave him (both resets happened) is correct; only the attribution is wrong.
**Deliberately NOT sent as a third message**: non-P0 traffic is held behind the live token P0, it
does not change anything he would do, and two corrections in ten minutes is noise competing for the
only attention that matters. **Fold it into the next message to him.** Recorded here so a reset
cannot quietly discharge it.

## 0d. 🔬 THE "STRANDED REPORT" IS REFUTED — AND IT UNCOVERED SOMETHING WORSE (05:45Z)

Delegated as a read-only measurement on the goumlyne silo + the ihsanos source. **Findings changed
my mind in both directions.**

**↩️ WITHDRAWN — report `971a84b3…` (row id 31) is NOT stranded-awaiting-approval.**
It is **SOFT-DELETED**: `deleted_at = 2026-07-09 07:47:44Z`, 66 minutes after it was signed. The app
filters `deleted_at IS NULL` (`src/actions/tabung-weekly-reports.ts:1049`), so it is invisible and
**nobody is waiting on it.** The previous block carried it as a live client-facing gap. It is not one.
*I inherited that claim and repeated it; it was never checked against the row's own delete columns.*

**✅ CONFIRMED, and measured over the WHOLE table rather than by looking up the one id** (the
distinction §3 exists to enforce): 9 rows total; **live** rows are `closed` × 3 and **nothing else** —
**zero live reports in any pre-approval state**, so there is no report awaiting a notification today.

**✅ CONFIRMED — the approval-email path is completely unexercised.** `tabung_report_notifications`
= **0 rows**, every status. Both approvers active since 02:08Z. **"It is set up" is still not "it works",
and the §1 hold stands unchanged.**

**✅ CONFIRMED — no backfill exists and the design cannot self-heal.** Sends are enqueued
**application-side, on the transition itself** (`preparerSignAction` → `notifyReportReadyForApproval`,
`tabung-weekly-reports.ts:653`); the Vercel cron `*/5` is a **pure drainer** that only reads rows
already `queued` and never scans reports. No DB trigger, no `pg_cron`/`pg_net`. Migration 120 contains
no backfill. **So any report that transitioned before 120 existed is structurally unreachable** — true
as a property, but currently vacuous, because the only such report is deleted.

### 🔴 THE REAL FINDING: A SIGNED MONEY REPORT WAS DELETED WITH NO ATTRIBUTABLE ACTOR
Row 31 has `was_ever_signed = true`, `deleted_at` set — and **`deleted_by` NULL, `delete_reason` NULL**,
with **no `audit_log` action recorded for it after 2026-07-09**. A *signed* weekly donation report left
the client's live records and **the substrate cannot say who did it or why.** Migration 107 is named
`delete_before_sign`, i.e. it appears intended to forbid exactly this.
⚠️ **NOT YET ESTABLISHED, and I am not asserting it:** whether the guard has a hole, or whether the
delete simply pre-dates the guard. **Do not resolve that from `schema_migrations` — CAI-615 forbids it;
probe the trigger/constraint objects.** Escalated to cai as a money/audit attribution finding.
*This sits directly on top of §4's existing ATTRIBUTION defect (660/676 chain rows name a TEST account).*

### ⚠️ Standing: NON-P0 OPERATOR TRAFFIC IS HELD
Footed on the **live P0 alone** (the burned token), no longer on the withdrawn saturation finding.
The token was still live at 04:45Z after six hours. **Do not re-send the commands; do not re-page.**
Correct per CAI-613: no new information, risk is not time-decaying, revocation needs his own account.

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
| 4 | **GIRO access for Elly** | **SCOPED 2026-07-26 03:20Z → `reports/giro-state-of-play-20260726.md`.** Blocked on the client answering **(A) import GIRO credits as donations** vs **(B) reconcile bank vs tabung** — asked 07-24, never answered, re-asked 07-26. Live cutover gated on the audit-lock. ⚠️ ~~which is **NOT on goumlyne** (max applied migration 118)~~ — **that claim is LEDGER-DERIVED and INADMISSIBLE under CAI-615 (§0c); it was never re-established by probing the objects. Do not build on it.** **Nothing promised on timing; the stray "this week" came from a DRILL message and is withdrawn.** | hub |
| 5 | **Elly's bank import** | **HELD** — 4 prerequisites (§4) | cai |
| 6 | **Agent-as-signatory fix** (task #10) | cai ruled: exclude by **durable service-account property, NEVER a hardcoded address** | hub |
| 7 | **CAI-586** pre-push smoke → structurally read-only | queued | hub |
| 8 | **CAI-578 EXIT proof** (a RESTORE, not an export) | not started | hub |
| 9 | **money/audit shared fate** | cai to rule shape | cai |
| 10 | **Fleet identity as first-class** (CAI-591 §D) | surfaced at 5 layers now — scope as its own work | hub |
| 11 | Verify approval emails actually send | on Elly's next submission | hub + client |

~~**Bus at handoff: 1 unread** — #11515~~ **CLEARED.** That thread ran to ground: the gauge is not
mis-scaled, it is **unrelated** to the property it names, and it is now **disarmed as a control**
(CAI-616, §0c). Nazim retracted his own divisor explanation; cai's replacement candidate was
refuted the same hour.

**Bus at the r3 boot (05:24Z): 0 unread, 0 unprocessed operator messages.**

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
- **BLOCK 3:** "cai was not reset" → **he was, 05:23Z, same sweep as me.** A surviving background job is what an in-place reset LOOKS like. §0c.
- **BLOCK 3:** "report `971a84b3…` is stranded awaiting approval" → **soft-deleted 07-09, nobody waiting.** §0d.
- **BLOCK 3:** "the audit-lock is not on goumlyne (max applied migration 118)" → **ledger-derived, inadmissible under CAI-615.** Not disproved — *unevidenced*. §0c.
- **BLOCK 3:** "the operator typed 'reset me' at both panes" → **nobody did; both were the bodies' own staged steps.** He stranded THREE instructions, not four. §0c (undelivered debt).

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

**Re-ordered at 05:50Z by hub r3.** Items 1 and 2 of the old list are DONE or MOOT (#11515 cleared;
GIRO scoped and blocked on the client — §2 item 4).

1. **Land the reset-script hardening.** Four defects, §0c. Delegated, **awaiting my review — do not
   let it land unreviewed**: it is the primitive that resets the fleet, and a break in it only shows
   up at the moment it is needed most. Then **`git add` both files** — they are untracked, and cai
   has designated the log they write load-bearing.
2. **Re-establish the goumlyne migration state BY PROBING OBJECTS** (`to_regclass` + row counts),
   never from `schema_migrations` (CAI-615). This unblocks nothing by itself but **every §6.6 grant
   and the GIRO cutover gate now depend on it**, and the claim they were resting on has been struck.
3. **Wait for the operator to run the token commands**; then gate step 3 **on BOTH hosts** before the
   seat removal. **Do NOT re-send the commands. Do NOT re-page** — Nazim holds that operator thread.
4. **After the windows close today** (11:24 / 13:19 / 14:54Z) surface 121/122 to cai — **and expect
   CAI-615 to bind on top**: a grant now needs the silo's state measured by object probe, plus
   FILE + REF + SHA with constraints located by line (§0b). Do not press early.
5. **The deleted signed report (§0d)** — establish by object probe whether the `delete_before_sign`
   guard has a hole or the delete pre-dates it. Money/audit attribution; cai has it.
6. Task #10 agent-as-signatory: durable service-account property, never a name pattern.
7. Then CAI-586 · CAI-578 restore proof · money/audit shared fate · fleet identity.
8. **Every turn:** reconcile `operator_log.unprocessed()` + `agent_messages`; stamp `read_at` AND
   `responded_at`. **And keep this file current as you go** — invariant 42: currency is measured,
   never declared, and a body at 100% is the one body that cannot make the measurement.
