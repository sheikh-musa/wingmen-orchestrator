# Nazim handoff — 2026-07-26 (post-reset session; the false-page incident)

_You are **Nazim / console body** (Mac Mini, `ORCH_BODY_ROLE=console`), tmux `nazim`. Reply to the
operator ONLY via `scripts/nazim_send.sh`. Each turn reconcile BOTH `operator_log.unprocessed()` AND
`agent_messages` to `orch-console`._

**READ THIS FIRST, THEN `reports/nazim-handoff-20260725-lanes.md`** — that file's ⚑ FINAL STATE block is
still standing doctrine (lanes, ownership, the token item). This supersedes it where they conflict.
Booted 01:35Z on Opus 5 (the older file says Fable 5 — the reset changed it).

## ⚑ THE ONE THING TO UNDERSTAND: I PAGED THE OPERATOR WITH A FALSEHOOD

Two Telegram alerts (op #7343, #7345) told him `cc-orchestrator cleared + boot nudge sent` for a reset
that **never happened**. Not the launchd watchdog — that runs `--arm=amber` and has never taken the
destructive path. It was `tests/test_context_health_watchdog.py::test_do_reset_full_happy_path` under an
ordinary `pytest` run. **I ran the 02:02 one myself**, verifying an unrelated fix; his phone buzzed 11s later.

Mechanism: `PaneState` gained `bg_agents` **inserted ahead of `raw`** (029f1ef, 07-25). A test file
unchanged since 07-22 constructs it positionally, so a string silently began binding to `bg_agents` and
`raw` went empty — flipping the step-4 `booting` check to False, the branch that pages. **The suite
stayed green**: it asserts the reset returned True and never asserted whether a page was emitted.
It then travelled — cai repeated it to the operator and filed it against itself as its worst error; the
hub believed it; I sent a correction about it while being its cause. Four bodies, nobody questioned the instrument.

## SHIPPED (all committed AND pushed on `fable/substrate-safe-fixes`)
- `194111a` **nudge_cai.sh** — checked the MINI's tmux for a session on the STUDIO; said "no live cai
  session" while cai was alive with unread mail. Now re-execs over SSH and reports **COULD NOT CHECK**.
- `9b0d6af` **ingest.py** — the operator's 01:36Z message logged as `[non-text update]`, text lost, no
  record of what arrived. Added video/sticker/poll/etc; unknown types now record the message's KEY NAMES
  (never values) + a log warning. Found a latent bug: **every GIF was filed as a FILE**. Daemon restarted.
- `ed961fa` **context gauge** — 6 bodies watched, 10 alive; four Mini lanes reported NOTHING (cc-irsyad
  at 48%, invisible). Stale readings now marked (cc-cosem was green on a 4.3-day-old reading).
  ⚠️ **Complete but MIS-SCALED — see open items.**
- `fc453f3` **reset_orch.sh** + `e87b34b` **reset_cai.sh** — both wiped the composer with 120 BSpace and
  no capture, and both hardcoded a PAST reset's world in their boot text. The hub's composer held
  **"now do giro"**, an operator instruction unsubmitted for 37 min, which the wipe would have destroyed.
- `82bd3ad` **harm channel** — paging impossible from pytest, two ways. `conftest` already set a dummy
  `MUSA_TELEGRAM_ID`; `nazim_send.sh` re-read `.env` and ignored it, so that safeguard was decorative.
- `3d9ce56` **"cleared" is now PROVED** — three states from `cc_session_costs`: `CONFIRM_RESET` (new
  session_id AND tokens ≤50%), `CONFIRM_NOT_RESET` (post-clear telemetry, same session), `CONFIRM_UNKNOWN`
  (writer runs on 300s; lag must not masquerade as either). `ok=True` ONLY on CONFIRM_RESET. Inverted
  hedge fixed. `PaneState.__post_init__` type-asserts. `conftest` autouse fixture makes live seams RAISE.
- `30a6738` **git identity** — my commits were authored `sheikh-musa` by the machine's GLOBAL config.
  Repo-local identity set (`cc-orch-console (Nazim)`); global untouched.

## RESETS DONE (both verified on TWO sources — never on "I typed the command")
- **Hub** 02:55Z: 802,287 → 91,270, new session `f58a2fb4…`. Preserved "now do giro" and handed it over;
  the fresh hub is running it.
- **cai** 03:28Z on its own declaration: 486,287 → 112,022, new session `c606c70c…`. Composer held
  "reset me" (preserved). Telemetry lagged first — reported as CONFIRM_UNKNOWN, resolved to CONFIRMED.

## STANDING — unchanged and binding
Writing to the operator on another body's topic is **a proposal that waits**. Re-read the last outbound
on a tag before sending on the hub's thread. **Verify every "done" at source** — testimony about code has
been wrong more often than right; three bodies described one commit three different ways and only opening
the file settled it. A measurement whose tooling failed reports **"could not measure"**, never a finding.
**Name the host in every path** (cost us ~6 confusions today). **Never assert ABSENCE from a grep** —
read the file. **A correction is an assertion** and carries a heavier burden, because it arrives with "I checked".

## OPEN / OWED
1. ~~**Gauge divisor** — we divide by the NOMINAL 1M window; a body degrades against the USABLE one.~~
   🔴 **RETRACTED — see the LATE BLOCK. That explanation is REFUTED**: the gap was 27 points at 02:00 and
   76 points at 04:00 on the same body, and no fixed usable-window offset produces both. The real
   relationship is UNKNOWN. Struck in place rather than deleted, because a handoff that quietly rewrites
   its own wrong claim is how the next body re-derives it. `ed961fa` made the gauge COMPLETE, not correct.
   **Do not trust it to detect a full body; use the body's own footer.**
2. **Authorisation gate** — every arming gate lives OUTSIDE `_do_reset`, so any direct call bypasses all
   of them. **cai RULED (CAI-606): gates INSIDE `_do_reset`, RE-EVALUATED AT CALL TIME — not a token,
   because a token certifies a PAST gate.** Not yet built. This is the next job.
3. **Preview exposure** — `ihsanos-irsyad` preview target shares PRODUCTION Supabase creds (incl.
   service-role), unscoped by branch, and the unguarded GIRO branch has had a READY preview since 07-17.
   What held is **Vercel SSO — an accidental, unrecorded control**. cai RULED (CAI-609): take the preview
   down FIRST, then branch-scope to match the sibling `ihsanos` project, and **DO NOT read the sensitive
   values**. **Hub's pen to execute; verify a preview build still succeeds afterwards.**
4. **Git identity** — cai's CAI-610 log covers RE-AUTHORING (the hub's Vercel workaround) but is blind to
   DEFAULT-AUTHORING (mine, every lane). **Key any detector on the EMAIL, not the name**: Mini global is
   `sheikh-musa`, Studio is `Musa`, same email — a name-keyed sweep certifies the Studio clean. Five of my
   commits remain misattributed incl. `82bd3ad`, whose message admits MY error under his name. Forward-only.
5. **GIRO** — HOLD stands; binding blocker is the unanswered A-vs-B question (with the hub, ~10 days).
   Spec re-corrected twice to `b0b6910` (verified by me at source); cai free to rule on it.
6. Operator owes: batch start date · interim credential call · geofence · TDU group + Alderei token.
   ~~**Do not re-inflate the token item** — both work; exposure not outage.~~
   🔴 **THAT GUIDANCE IS NOW WRONG — DO NOT FOLLOW IT.** It was true at boot and is not now. The BURNED
   token `sbp_2180…b069` is **STILL LIVE**, measured twice with a discriminating control (fabricated → 401,
   burned → **HTTP 200 as his own account**) at 03:50Z by cai and 04:45Z by me. It is his PERSONAL PAT —
   management-plane access to everything he owns — and it has been live in a Telegram log since 22:38:49Z.
   **Step 1 (revoke) has never happened. This is the ONE operator-facing P0** (op#7367). Struck in place,
   not deleted: a handoff that quietly rewrites a stale instruction is how the next body de-prioritises a
   live P0. The third token `sbp_6707…74a8` IS genuinely dead — re-verified with BOTH controls.

## THE PATTERN, IF YOU READ NOTHING ELSE
Thirteen-plus defects tonight and **not one failed toward alarm**. The sweep that found nothing, the guard
that saw no unsent text, the gauge that said amber-no-action, the reset that reported success, the grep
that proved absence. Every one failed toward calm, and calm is the shape nobody re-checks.
Proposed operational form: **a check reporting "nothing wrong" must state what it would have taken to
detect something wrong.** Every one of tonight's would have failed that question instantly.

## DELEGATION (cai RULED, CAI-603)
**Fact out, judgement in.** Delegate sweeps, enumeration, verification. Never delegate a ruling, a grant,
or a message to the operator or a client. **The delegate must NOT inherit your scope** — hand it your
search root and it reproduces your boundary error and returns confident corroboration. Scored 1-1 tonight:
the gauge audit refuted my hypothesis AND my search root (paid off); the ingest fix confirmed my diagnosis
(manufactured corroboration, recorded as such). Verify agents' reports before believing them — I caught a
lane's false absence-claim and my own error in an agent's brief this way.

---

## ⚑ LATE BLOCK (04:05Z) — read this before acting on anything above

### THE ONLY OPERATOR-FACING ITEM: THE BURNED TOKEN IS STILL LIVE
cai measured it at 03:50Z **with a discriminating control**: fabricated token → 401, the Studio's
`sbp_2180…b069` → **HTTP 200 as his own account**. It is his PERSONAL PAT (management-plane access to
everything he owns) and it has been live in a Telegram log since 22:38:49Z. **Step 1 (revoke) has never
happened.** Sent as op#7367 — token first and alone, everything else dropped, per cai. **If he has not
revoked it, this is still the top item.**
- **The third token `sbp_6707…74a8` (7 Mini `.env.local` files) IS genuinely dead** — but my original
  finding was UNDER-EVIDENCED. Re-tested with controls both ways: fabricated → 401, **known-live → 200**,
  subject → 401. **A negative control only proves a test can say no; a positive control proves it can say
  yes.** Never run a revocation check without both (cai's invariant 38, my correction adopted).
- cai's first probe said "DEAD" — its script had `if e.code in (401,403): print("DEAD")`, hardcoding an
  interpretation of a status it had never observed. It would have published a false all-clear.

### THE CONTEXT GAUGE IS NOT MEASURING — I RETRACTED MY OWN EXPLANATION
Hub footer **100%**; our gauge **24%**, classified **green/"ok"**, seconds apart. Earlier the same pair
was 100% vs 73%. **27 points then, 76 now — no fixed usable-window offset explains both, so my divisor
theory (#11515, and what I told the operator) is REFUTED.** I do not know the real relationship.
Untested candidate: the figure may be one call's context, with sub-agent context uncounted.
🔴 **Do NOT trust the gauge to detect a full body. Use the body's own footer.** `ed961fa` made it
COMPLETE, not correct.

### THE HUB REFILLED A FULL WINDOW IN 65 MINUTES
Reset 02:55 (802,287 → 91,270); at 100% again by 04:00, same session `f58a2fb4`. It pulls 20–30k Explore
surveys into its own context. **Resetting hourly is a symptom, not a schedule** — CAI-603 (fact out,
judgement in) is aimed exactly here. I did NOT reset it a second time: mid-task, queued work.

### HUB HEARTBEAT — FIXED ONLY AS FAR AS A HAND-WRITTEN ROW GOES
`cc-orchestrator` had **NO `agent_status` row at all** (absent, not stale) while alive and working, so
every liveness check returned nothing rather than DOWN. The hub inserted one and then **refused to call it
fixed**: nothing refreshes it, so it will freeze and assert liveness it is not proving. Treat that row as
a REGISTRATION, not evidence it runs. **OWED BY ME: a heartbeat produced by the turn loop.**
Good news worth recording: the DB REFUSED its first write (`app.current_agent_id` GUC not set) — a guard
that fired correctly, unprompted.

### RULINGS LANDED TONIGHT
- **CAI-609 EXECUTED** by the hub, verified three ways (preview deleted; 5 vars production-only, re-read
  from the API; fresh preview driven to READY; values never read). **Its deviation was right**: cai's
  ruling was self-contradictory (branch-scoping requires supplying values; reading values was forbidden).
  Previews now get nothing — safer than the sibling precedent.
- **CAI-615 — THE MIGRATION LEDGER IS NOT A RECORD.** goumlyne: 0 of 93 rows hold the SQL that ran.
  Confirmed by me on the live silo: ledger max=118, **no 120 row, yet both mig120 tables LIVE with the
  client's 2 approvers.** Unreliable in BOTH directions. 🔴 **Never answer "is it applied?" from the
  ledger — probe the object** (`to_regclass` + row count). **NO BACKFILL** (cai ruled): a repaired-looking
  ledger removes the reason anyone would probe again; the gap is doing work by being visible.
- **CAI-611** — the attribution property was never missing: **93.7% of commits already carry a
  Co-Authored-By trailer**. Mini corroborates independently: 91/98 = 92.9%. My delta: **the trailer names
  a MODEL, not a BODY** — it answers "was this an agent?", never "which agent?". Local `git merge --squash`
  PRESERVES the trailer (tested); GitHub's squash button = COULD-NOT-DETERMINE.
  🔴 **DO NOT apply the repo-local identity fix to `ihsanos`** (deliberate override, Vercel premise).
- **CAI-612** — spec `b0b6910` approved as a spec. Range must be named from the **last approved point**
  (`4429428..b0b6910`), not from main.

---

## ⚑ BLOCK 3 (05:35Z) — THE ROOT CAUSE OF THE WHOLE NIGHT'S SILENT-DROP CLASS

### 🔴 `Enter` DOES NOT RELIABLY SUBMIT. THIS IS THE MECHANISM.
Swept all 7 panes both hosts at 05:20Z: **FOUR held stranded text.** Pressed a bare `Enter` into three
lane panes and captured before/after: **nothing moved, 3 of 3.** Ran the same three through
`scripts/lane_nudge.sh` (verified submit + clear/retype fallback): `submitted + working (try 1)`, all
composers cleared.
**`lane_nudge.sh`'s docstring has documented this since 2026-06-20** ("the Enter frequently fails to
submit… silently stalled lanes ~5x"). **We built verified-submit for AGENTS and left every HUMAN typing
into the same panes unprotected.**
**THIS IS WHY THREE OF THE OPERATOR'S INSTRUCTIONS VANISHED TONIGHT** — "now do giro" (02:13),
"run the vercel bot-account experiment", "fix the heartbeat to be periodic" (04:41). He was NOT
disengaged; he was answering into a channel that drops on the most ordinary action a person can take,
and a stranded prompt looks identical to a submitted one in a busy pane. cai withdrew a saturation
finding it had made about him on this basis (CAI-617).
**Two of those were the best items of the night; three bodies then spent hours rediscovering them.**
🔴 **NEVER deliver by bare `Enter`. Use `lane_nudge.sh`.** Told the operator (op#7373).

### DETECTOR SPEC (owed by me, now concrete)
Watch every pane for **text that persists across samples without the pane entering a working state** —
that is what a failed Enter looks like, and it separates stranded-and-forgotten from staged-and-deliberate.
Must ALSO record when staged text DISAPPEARS with no submission ("run the vercel bot-account experiment"
vanished from two panes with nothing recording whether it ran). Build with the turn-loop heartbeat.

### BOTH SINGLETONS RESET AGAIN — on their own declarations, both CONFIRMED on two sources
    cai  05:23:18Z  296,557 -> **71,867**  new session `0bc1aab0…`
    hub  05:23:36Z  301,292 -> **60,340**  new session `131245ce…`
Minutes before, the gauge called both **27% "green"** while both footers read 100%. Strongest argument
for CAI-616 anyone produced.
**A FALSE ALARM I RAISED AND WITHDREW — do not re-raise it:** `reset_orch.sh` logged
`PRESERVED staged composer text: reset me` 30s after I verified the hub's composer EMPTY. Looked like a
wrong-pane target. Checked first: both scripts hit their intended panes, no operator input existed, it
was the hub's OWN staged next step. Recorded because two log lines will look alarming to the next reader.

### GAUGE DISARMED IN PRACTICE (CAI-616 implemented)
`dev.wingmen.context-health-watchdog.plist` — **`--alert` AND `--arm=amber` both removed**, reloaded,
verified running as `arm=off reset=DRY-RUN alert=off`. Detects and logs only; gates/alarms/resets nothing.
Also replaced the plist's own header, which still claimed `ARMED=AMBER` — I nearly left a comment
asserting a state nobody would re-check, in the file fixing that exact class.
**cai's single-divisor candidate: CONFIRMED as fact (one 1M window for every body) but REFUTED as the
explanation** — a wrong divisor is constant per body; our two readings were the same body same day
(73 then 24). The relationship is NOT a scale factor; it varies within one session.

### QUEUED, NOT DONE — and named as a limit
**cosem-platform main exports `emirates_id_last4`** (`src/modules/exams/archive-export.ts:45,:351`)
protected ONLY by a COMMENT at :228 ("SYNTHETIC data only in this build"). **No enforcement of any
kind** — verified — and reachable from a dashboard button. **Third instance tonight of "the protection is
a comment"** (GIRO prototype ⛔ header; mig120's inertness claim). cc-cosem-exams authored 4 fixes;
**only 1 of its 5 commits is merged** (verified per-commit `--is-ancestor`). I declined to review 4 PII
commits this deep into a session and said so rather than producing a review that reads well.

### MY OWN ERRORS THIS BLOCK
- Claimed cai "was not draining" from a single snapshot; it drained minutes later. A snapshot is not a state.
- Said I would stop nudging cai, then sent 3 more by running the script as a "test". Each run is a real nudge.
- `nudge_cai.sh` now SKIPS when the composer shows `Press up to edit queued messages` (queued+unprocessed
  = already signalled). Guard is a SINGLE SAMPLE of a racing state — known-weak, not proven.

### STANDING ADJUDICATION — SLA stalls against DOWN lanes (do not re-litigate each one)
Messages to a lane with **no live session and no `agent_status` row** generate an SLA escalation to
orch-console every ~30 min, forever. **Adjudication: CORRECTLY WAITING. No nudge, no escalation, no
action.** A notification addressed to a down lane is *designed* to be read at its next boot; nobody can
read it now and no nudge can help. Verify the down-ness on three sources before applying this (Mini
`tmux has-session`, Studio `tmux ls`, `agent_status` row) — then close it and move on.
Known recurring: `cc-cosem-tdu` (#11703, tdsct pass-mark notification), `cc-cosem-exams` (#11652,
stopped by my order).
🔴 **DO NOT "fix" this by suppressing on `fleet_lanes.desired_state='down'`.** I nearly did. That field
is an AUTOSTART POLICY, not a pause state — `cc-irsyad`, `cc-fleet-health` and `cc-scholar` are all
`down` AND currently WORKING, so suppressing on it would permanently and invisibly silence the
supervised **client-facing irsyad lane**. It would fail toward calm, like everything else tonight.
**The real gap:** nothing in the substrate expresses "intentionally paused / not running, don't page
about its queue". Stopping a lane is done by MESSAGE ONLY — socially real, invisible to every automated
check. Same class as the composer. That is a schema change and it is OWED, not hacked.

### A BIAS OF MINE, NAMED (twice in one hour)
I twice went looking for a defect in `priority_sla_watchdog.py` and it was **right both times**: once it
was reporting that I had never stamped `responded_at` on 18 messages (my replies were invisible AS
replies); once it was a 1-second race on a 33-minute threshold, with the `attended_for` recheck already
covering both escalation paths — I read the code instead of proposing the fix, and there was no bug.
**I am quick to suspect checks that page ME and slow to suspect ones that reassure me** — the exact
mirror of the fail-toward-calm theme. A check that creates work deserves the same evidentiary bar as one
that lets you relax, not a lower one.

### STANDING ADJUDICATION 2 — SLA stalls against a BUSY singleton
`pane_busy()` in `scripts/lib/composer_capture.sh` is now the authoritative busy check and it is
verified working from the Mini over SSH with the locale unset (the environment that matters):
🔴 **THE INVOCATION I ORIGINALLY WROTE HERE WAS WRONG AND WOULD HAVE ALWAYS SAID BUSY.**
`pane_busy` **ALWAYS `return 0`** — the exit code means "the measurement succeeded", NOT "busy". The
verdict is in **`CC_BUSY`**. Proved: cai `exit=0 CC_BUSY=0` (not busy) and orch `exit=0 CC_BUSY=1` (busy)
— same exit code, opposite verdicts. `pane_busy ... && echo BUSY` therefore reports BUSY for every pane,
always, which under this very adjudication would mean "close every SLA forever". A check that always
fires, inverted into a check that always reassures. **Read the VARIABLE:**
```
ssh Musa@mac-studio 'cd ~/wingmen/orchestrator && env -u LANG -u LC_ALL -u LC_CTYPE bash -c \
  ". scripts/lib/composer_capture.sh; pane_busy /opt/homebrew/bin/tmux cai:0.0; \
   echo \"CC_BUSY=$CC_BUSY STALE=$CC_BUSY_STALE REASON=$CC_BUSY_REASON\""'
```
Semantics (from the source, lines 95-148): live foreground -> `CC_BUSY=1 STALE=0 REASON="foreground turn
in progress"` · blocked on background agents -> `CC_BUSY=1 STALE=0 REASON="blocked on background agents…"`
· **FROZEN pane -> `CC_BUSY=0 STALE=1 REASON=''`** (empty reason is INTENDED: there is no busy state to
describe, only a stale marker) · idle -> `CC_BUSY=0 STALE=0`.
**If the recipient is BUSY: correctly waiting. Close the SLA, no nudge, no escalation, move on.** Do not
re-derive this each time — but DO run the check each time; "it was busy an hour ago" is not evidence.

### 🔴 cai IS BLOCKED AT 100% ON BACKGROUND AGENTS — DECISION CRITERION FOR A FRESH ME
As of 08:15Z cai has been at **100% context, blocked on 4 background agents, with `reset me` staged
(stranded 4th time)** since ~07:00Z. Its own pane says *"This is a good place to reset me."*
I am HOLDING rather than forcing: a reset now discards four agents' in-flight output.
**The criterion: if cai is still blocked on the same background agents well past a couple of hours, it
is wedged rather than working, and `RESET_FORCE=1` is the right call** — it now names WHICH busy state
is being overridden, so a forced clear is never silent. Capture the composer first (the script does it)
and expect `reset me` to be preserved as cai's OWN staged step, not an operator instruction.
Do NOT force merely because it is at 100%. Both singletons hit 100% roughly every ~2h tonight; that is
the work-pattern problem (CAI-603, fact out / judgement in), not a reason to interrupt live work.

### ⚑ CLOSING STATE (10:30Z) — audited, with two of my own claims withdrawn
**Measured from TELEMETRY, never panes:** cai 205,431 · hub 396,968 · me 793,212 (79%).
All four Mini lanes: `pane_is_busy` NOT-BUSY, `STALE=0`, 0 unread — genuinely idle and drained.
Both inboxes clear. Everything pushed (`88dd6a9`).

🔴 **cai's PANE IS FROZEN and shows `100% context used`. TELEMETRY SAYS 205,431 (~21%). DO NOT RESET IT
ON A PANE READING.** Verified: byte-identical captures, `CC_BUSY=0 STALE=1`. It is healthy and active.

### 🔴 TWO OF MY OWN FINDINGS, WITHDRAWN OR NARROWED — do not re-inherit them
1. **The strand RATE is withdrawn.** "Four panes stranded at 05:20, three more within 15 minutes" was
   entirely pane READS with no repaint check. **The MECHANISM survives** — bare Enter failed 3/3 and
   `lane_nudge.sh` submitted 3/3, an INTERVENTIONAL test, not a read. **Mechanism proven, rate unsupported.**
   I told the operator it was a continuous background loss; that continuity claim rests on the count.
2. **"A pane reading is only evidence if the pane is repainting" was TOO STRONG.** orch's pane is
   byte-identical AND truthful, because an idle pane legitimately does not animate. **Corrected: a reading
   that ASSERTS ACTIVITY needs repaint.** A static pane is consistent with idle-and-truthful AND
   frozen-and-lying; what separates them is whether the render CLAIMS motion. `composer_capture.sh` already
   encoded this (STALE raised only where a marker needs liveness proof) before I stated it wrongly.

### THE ITEM NOBODY OWNS YET
**There is no inventory of which fleet signals are pane-derived** — lane watchdog, composer capture, my
sweeps, context readings, busy checks, the reset scripts. Each needs a liveness precondition and nobody has
the list. Same shape as the §6.6 legs: we hardened the one we were looking at.

### OPERATOR — one live item, two queued
🔴 **The burned token `sbp_2180…b069` is STILL LIVE** (measured with a discriminating control at 09:41Z by
cai; ~11h). op#7367/7381/7385. **Queued behind it:** the batch FLOOR (not the date — a floor or even
"don't know" unblocks cosem-port) and the tdsct pass mark (18 labels + acronym + 2 confirmations, and
**tdsct theory cannot grade until answered** — a live consequence, not theoretical).
He asked whether we are over-correcting. **Answer, measured: 301 bus messages since 01:30, of which
withdrawals/corrections — me 37, cai 36, hub 16; the four building lanes produced 5 between them.** Real
pattern, cause unproven, and I refused to attribute it to the model. Two drivers named: instruments
genuinely were lying (so suspicion kept being rewarded), and **we spend all night praising self-correction,
which makes correcting the highest-status move available.**
