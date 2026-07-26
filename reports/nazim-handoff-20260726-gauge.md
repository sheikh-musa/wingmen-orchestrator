# Nazim handoff — 2026-07-26 (post-reset session; gauge + bridge fixes)

_You are **Nazim / console body** (Mac Mini, `ORCH_BODY_ROLE=console`), the operator's CTO console,
tmux session `nazim`. Reply to the operator ONLY via `scripts/nazim_send.sh "<text>"`.
Each turn reconcile BOTH `operator_log.unprocessed()` AND `agent_messages` to `orch-console`._

**READ FIRST, THEN `reports/nazim-handoff-20260725-lanes.md`** — that file's ⚑ FINAL STATE block is
still the standing doctrine (lanes, ownership rules, the token item). This file supersedes it only
where they conflict. I booted from it at 01:35Z on Opus 5 (it says Fable 5 — the reset changed that).

## ⚑ STATE AT HANDOFF (~02:10Z)

**Both inboxes were clear when I wrote this.** Operator stamped through #7346+; bus drained.

### THE LIVE ITEM IS NO LONGER THE TOKEN — IT IS THE HUB'S CONTEXT
- **The hub's own TUI reads `100% context used`. Our gauge reads 73%.** Ours is wrong: it divides by
  the NOMINAL 1M window; the body degrades against the USABLE window (minus system prompt, tools,
  reserved output). So the gauge says "amber, no action yet" about a session that is full.
  Filed to the hub as #11515. **I flagged my OWN commit `ed961fa` rather than letting it read as
  settled: it makes the gauge COMPLETE, it does NOT make it correctly SCALED.**
- **A reset is owed on the hub and I did NOT run it.** It was mid-flight on the irsyad thread and had
  just applied mig120. The idle gate would refuse anyway (correctly). Recommendation given to the
  operator: hub finishes client work → then reset with a fresh handoff. **His call, still open.**
- **DO NOT trust operator_messages #7345** ("cc-orchestrator cleared", 02:02:36Z). **It was FALSE** —
  verified same session `adc34035…`, created 07-25 18:54Z, token count still CLIMBING across the
  supposed reset (731,509 → 764,994). The Mini's watchdog is `--arm=amber`, its executor logged
  `not idle/authed … skip` every pass, and the red boundary guard refused red correctly. Something
  still announced the outcome to his phone — `delivered` recording INTENT not OUTCOME, the same
  defect we closed in the send paths tonight, resurfacing in the RESET path. **Root cause not yet
  found — that is open work.** I corrected him directly.

### WHAT I SHIPPED (all committed AND pushed on `fable/substrate-safe-fixes`)
1. **`194111a` nudge_cai.sh** — it checked the MINI's tmux for a session that lives on the STUDIO and
   printed "no live cai session" while cai was alive holding 4 unread. Now re-execs over SSH on cai's
   host (so the composer guard runs against the REAL pane) and reports **COULD NOT CHECK** on
   unreachability instead of a verdict. Both paths verified. (First cut captured `rc=$?` after `fi` —
   the if-statement's status — silently turning an unreachable host back into success. Caught by
   running the failure path. **Do that.**)
2. **`9b0d6af` ingest.py** — the operator's 01:36:49Z message logged as bare `[non-text update]` with
   its text lost and NO record of what arrived. Added video/video_note/animation/sticker/poll/venue/
   location/contact/dice; unknown types now record the message's sorted KEY NAMES (never values — the
   row is durable and widely read) plus a WARNING to `logs/nazim-ingest.log`. Found a latent bug:
   Telegram sets `document` on GIFs too, so **every GIF had been filed as a FILE**. 11 tests, proven
   non-vacuous by re-running against stashed pre-fix code (10 failed). **Daemon restarted — live.**
   Inbound is only PROVEN at his next real message; I could not self-test it.
3. **`ed961fa` context gauge** — 6 bodies watched, 10 alive. **Four live Mini lanes reported NOTHING**
   (cc-irsyad at 481K/48%, the second-fullest body, invisible). Root cause was upstream: the writer
   resolves identity via `_DIR_TO_CC`, which knows only the 4 original families. Fixed with a
   **SEPARATE `_LANE_DIR_TO_CC`** — widening `_DIR_TO_CC` would have enrolled five live lanes into the
   loop detector's runaway KILL path. Stale readings now MARKED not suppressed (cc-cosem was showing
   green on a 4.3-day-old reading). Over-window rows now surface as UNMEASURABLE instead of vanishing.
   **See the divisor caveat above — this is not finished work.**

### OWNERSHIP MOVES
- **Task #11 (seed `tabung_report_approvers`) is the HUB'S, not mine** — I struck it (#11510) so
  goumlyne has ONE writer. cai GRANTED, hub APPLIED mig120. **Allowlist is deliberately EMPTY**: the
  hub told the client to switch Saddam + Zuremi on themselves (self-service, option A). I verified
  that was safe — `approvers-client.tsx:153` really does render *"No approvers configured yet —
  approval emails are not being sent"*, so an empty allowlist is NOT a silent failure. Hub's answer
  checked out; no P1.
- **cc-irsyad** filed a good P1 (mig120 blocking the client's save — confirmed at source) with a WRONG
  addendum claiming a media-ingestion gap. It was a wrong-HOST claim: the screenshot was on the
  Studio, where that channel is ingested. It **withdrew cleanly** when told. Binding rule given to it:
  before reporting anything absent, establish you are on the host where it would exist; otherwise the
  finding is "could not verify from this host", never "missing".
- I filed the hub a finding it had not: **mig120's header claims the feature is "inert until applied"
  — only the READ half is.** `approvers.ts` genuinely fail-closes to `[]`; the client-editable WRITE
  path ships live and fails loudly with "Please try again", which can never work. Hub's to sequence.

### STANDING — unchanged and still binding
Writing to the operator on another body's topic is **a proposal that waits**. Re-read the last
outbound row on a tag before sending on the hub's thread. Verify every "done": a name is not an
implementation, and a measurement whose tooling failed reports **"could not measure"**, never a finding.

### OPEN / OWED
1. **Hub reset** — operator's call, my hand on the trigger when he says or when it idles.
2. **The gauge divisor** (nominal vs usable window) — the real fix, not yet done.
3. **The false "cleared" alert** — root cause unfound.
4. **The Studio runs its own checkout on `feat/operator-telegram-bridge`**, so cai/hub/cc-cosem/
   cc-ihsanos readings there still come from the OLD writer map. My fixes are on
   `fable/substrate-safe-fixes` and have NOT landed there. Needs a branch decision.
5. Operator still owes: batch start date · interim credential call · geofence · TDU group + Alderei
   token. **Do not re-inflate the token item** — both tokens work; it is exposure, not outage, and it
   was pasted into his own DM, not a group.
6. `cc-fleet-health` is unmeasurable (writes no recent transcript) — mapped, not fabricated.

### DELEGATION
The operator asked pointedly why I wasn't delegating. Both fixes above (ingest, gauge) were done by
agents, verified by me before I believed them — I re-ran the tests, read the diffs, and checked that a
helper one agent used was real. **Keep delegating; keep verifying. Do not relay an agent's report as
fact.**
