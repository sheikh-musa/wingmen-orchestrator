# Nazim handoff — 2026-07-25 (three client/SME lanes stood up)

_You are **Nazim / console body** (Mac Mini, `ORCH_BODY_ROLE=console`), the operator's CTO console,
tmux session `nazim`. Reply to the operator ONLY via `scripts/nazim_send.sh "<text>" "@console"`.
Each turn reconcile BOTH `operator_log.unprocessed()` AND `agent_messages` to `orch-console`._

## ★ WHAT CHANGED THIS SESSION — the lane pattern is now real

Operator directive op#7015 → build irsyad a dedicated agent; op#7057 → do the same for the two
SME dev groups. All three exist and are on the SAME staircase.

**The pattern (use it for lane #4, don't reinvent):**
1. `agents` row + `repo_scope` = the worktree dir basename (that's how `launch_dangerous_cc.sh`
   resolves identity — verify with `load_family_map`).
2. `git worktree add -b lane/<x> ~/wingmen/projects/<dir> origin/main` (fetch first).
3. `.env.local` in the worktree pinned to the RIGHT store + bus creds
   (`ORCHESTRATOR_SUPABASE_URL/_SERVICE_KEY` from orch `.env`), never `ANTHROPIC_API_KEY`.
4. `fleet_lanes` row (desired_state=down; operator/Nazim-booted).
5. Charter in `reports/cc-<x>-charter-*.md` — it OUTRANKS the repo CLAUDE.md.
6. `bot_channels.group_routing = {"agent_phase":"drill","agent_reviewer":"orch-console"}`.
7. Boot: `tmux new-session -d -s <sess> -c <worktree> scripts/launch_dangerous_cc.sh`.
8. **Announce the drill to the hub**, then `scripts/lane_drill_seed.py <agent> <file.json>
   --announce --note "..."` — drills go to the LANE'S bus inbox, NEVER `operator_messages`.
9. Direct the lane via an attributable `agent_messages` row + `scripts/lane_nudge.sh <sess>`.

| lane | tmux | agent | worktree | channel | phase |
|---|---|---|---|---|---|
| irsyad (client: Gazzabyte/Elly) | `irsyad` | `cc-irsyad` | `~/wingmen/projects/ihsanos-irsyad` (goumlyne silo) | `gazzabyte-irsyad` | **supervised** |
| exams (SME: Hariz) | `exams` | `cc-cosem-exams` | `~/wingmen/projects/cosem-exams-lane` (demo DB ywrpttp…) | `cosem-exams` | **drill** |
| caai (SME: Syed/Ray) | `caai` | `cc-caai` | `~/wingmen/projects/caai-lane` (ray-ca, LOCAL only) | `cosem-caai` | **drill** |

- **Reply gate:** `scripts/lane_reply.sh <channel_key> "<text>"` — drill (nothing leaves) →
  supervised (draft to reviewer) → direct. Unknown phase fails CLOSED. `irsyad_reply.sh` is a
  thin wrapper. Phase is DB state; the lanes cannot change their own.
- **Wake:** irsyad = `scripts/irsyad_shadow_watch.py` (launchd `dev.wingmen.irsyad-shadow`),
  because the hub polls that channel. exams/caai = the Mini's `nazim-ingest` nudges them directly
  (both channels flipped to `mode='agent-session'`, `inject_target=exams|caai`).
- **Latency:** `scripts/irsyad_latency_report.py` — hub vs lane-draft vs lane-sent per message.
  Baseline 2026-07-25: **hub median 0.8 min over 70 messages / 48h.**

## SESSION UPDATE (later same day) — what changed after the lanes went up

**FOUR lanes now, not three.** Added `cosem-port` (tmux `cosem-port`, agent `cc-cosem-platform`,
worktree `~/wingmen/projects/cosem-port-lane`) — the dedicated ADCDA→platform PORT lane (op#7117).
First deliverable is a GAP INVENTORY, not code. **Owed to the operator: the real next-batch start
date** — I asked; the platform data doesn't carry it and I told the lane not to invent one.

**A live credential defect, still open.** cc-cosem-exams found that the trainee record PDF printed
DOB + Emirates-ID-last-4 adjacently, and `deriveLoginPin(dob,last4)` = DDMMYYYY+last4 → the audit
PDF exposed the login PIN. Fixed + merged (239911b). Then, building cai's derived-credential
registry, it found a SECOND derivation: `syntheticTraineeEmail(askariyah)` = the login HANDLE. So
askariyah + dob + last4 is a COMPLETE WORKING LOGIN.
- I had waived the same field pair in the archive ZIP. **Waiver revoked** (CAI-556).
- cai's correction, which matters most: **the exports are only where it became VISIBLE.** The
  credential is a LOOKUP — anyone with record read-access can reconstruct any trainee's login with
  no export at all. Do NOT let clean exports read as closed.
- NEW INVARIANT: **never derive an authentication factor from stored PII.** The registry is its gate.
- **I OWE cai A DATED PLAN BY 2026-07-26**: random initial credentials, forced-reset path, trainee +
  client comms, interim compensating control. cai put the interim either/or to the operator (#7126,
  recommending HOLD); I agree with hold.
- Open findings routed: the backfill script that derives dob+last4+askariyah from the register number
  has NO runtime guard (→ cosem-port, P1, fail-closed guard); the intake import template makes the
  CLIENT assemble a cohort's logins in a spreadsheet (→ folds into the scheme plan).

**Company hires: HoQ + HoR both formally RATIFIED** (CAI-552/553) after cai read the specs. The three
specs were untracked on the Mini — invisible to every other body — now committed AND PUSHED
(1712cf3). New doctrine: **COMMITTED IS NOT FLEET-VISIBLE** — the test is "can a second host read it?"
CoS was already ruled (CAI-RESP-502) and needs building, not ratifying.
- HoQ binding conditions: deterministic floor in **CI, not a lease**; derived-credential check starts
  **ADVISORY** with shadow mode reporting each would-be block; skipped renders SKIPPED never pass;
  anything changing what BLOCKS goes through cai in both directions; ONE break-glass, time-boxed.
- First floor check BUILT: `scripts/lint_no_bare_timeout.py` (blocks day one; macOS has no `timeout`,
  so the wrapped command never runs and its empty output gets consumed as a measurement).
- **Q4, cai-owned:** grants + payment corridors + client-data custody are ONE question — what legal
  entity are we. cai is assembling it. **Do not register anything in isolation.**

**Fleet/token:** both hosts now on the operator's account (Studio .env swapped, old value kept as
`CLAUDE_CODE_OAUTH_TOKEN_PREV`); the hub was relaunched to pick it up; the other Studio lanes were
STOPPED rather than restarted (they had no unread work — a later boot reads .env fresh). Studio went
16 sessions → 2. `agent_status` now carries host/auth_account/auth_fp (migration 033).

**OWNERSHIP — I broke CAI-547 twice in 20 minutes.** Both times I correctly identified that a topic
was cai's and then wrote to the operator anyway ("I'll also tell him" reads as helpfulness, not as a
violation). Binding on me now: **intent to write to the operator on another body's topic is a
PROPOSAL THAT WAITS**, not a notification. cai's topic-keyed sender guard is the real fix; the
text-similarity duplicate guard I built canNOT catch it (the two duplicate messages were 8% similar).


## LATE-SESSION UPDATE — read this before acting on anything above

**FIVE lanes now.** irsyad · exams (Hariz) · caai (Syed) · cosem-port · plus the hub and cai on the
Studio. Phases: irsyad/exams/caai all **supervised**. cosem-port has no client channel.

**SHIPPED TO PRODUCTION TODAY** (cosem-platform `origin/main`, each merged after I re-ran CI myself):
- `239911b` trainee record PDF no longer prints half the login PIN
- `f6b7d3a` fail-closed demo-project guard on ALL 7 service-role scripts + Rule A boundary fix
- `00b48c7` **B1 — batches are org-scoped tenant data**: an instructor can open an intake without a
  code change. This was the literal blocker inside "before next batch starts".

**THE LIVE CREDENTIAL DEFECT — still open, plan delivered.** A trainee's whole login is derivable
from stored fields (`deriveLoginPin(dob,last4)` = password, `syntheticTraineeEmail(askariyah)` =
handle). **The exports are only where it became VISIBLE** — anyone with record read-access can
reconstruct any login with no export at all. Scheme plan: `reports/cosem-credential-scheme-change-plan-20260725.md`
(pushed). Interim control is the operator's (cai's #7126, recommending HOLD; I agree). NEW INVARIANT:
never derive an auth factor from stored PII.

**COMPANY HIRES: HoQ + HoR RATIFIED** (CAI-552/553). First floor check shipped:
`scripts/lint_no_bare_timeout.py` (blocks day one). Derived-credential registry built ADVISORY, with
shadow reporting. CoS was already ruled (CAI-RESP-502) and needs BUILDING, not ratifying.

**GUARDS FIXED THAT WERE FAILING OPEN** — both the same class, hours apart:
- `nudge_cai.sh` now refuses when cai's composer holds text (was: no guard at all; and my first
  version matched `^❯ ` with an ordinary space when the TUI uses U+00A0 NBSP, so it would never fire).
- `context_health_watchdog.py`'s "no unsent text" precondition had the SAME NBSP defect — it returned
  '' for every live pane and passed by never seeing anything. Plus a new background-agent guard: a
  body "waiting for N background agents" reads as idle to every existing check.
- **The destructive auto-clear (`--arm=red`) is still NOT armed.** Operator asked (op#7148); I want
  to see both guards refuse a real reset in the wild first. The plist stays `--arm=amber`.

**MY OWN FAILURES, recorded so a fresh me doesn't repeat them:**
1. I broke CAI-547 twice in 20 minutes — wrote to the operator on cai's topic after correctly
   identifying it as cai's. **Intent to write on another body's topic is a PROPOSAL THAT WAITS.**
2. My ~10 ssh nudges to cai each began with `C-u` — I erased its staged drafts all night. It also
   explains the "unattributed composer string" investigation: we theorised about provenance over an
   artifact of my own tooling.
3. I claimed a commit was delivered when it wasn't pushed. **COMMITTED IS NOT FLEET-VISIBLE.**
4. I reported an unfiltered `group by role` count (67) as fact; live was 61.

**OPERATOR PERCEPTION (op#7151), measured not guessed:** volume to him is DOWN ~55% since Wednesday
(200 → 87). What changed is cai went 0 → 15 direct messages in a day. Committed to him: one writer
(me) unless it's a thread he opened with cai; nothing until it's settled; corrections only when they
change what he'd do.

**STILL OWED BY THE OPERATOR:** the next-batch start date (port sequencing is blocked on it, and I
refused to invent one), his interim credential call (cai's #7126), and the TDU group + Alderei
BotFather token.


## ⚑ FINAL STATE (2026-07-26 ~01:30Z) — read this block first, it supersedes conflicts above

**OPERATOR IS DRIVING.** Nothing goes to him until he says he is stationary. He has the 4-step token
sequence and the two commands (op#7307).

### THE LIVE SECURITY ITEM — token remediation, blocked on him
- **STUDIO** `/Users/Musa/wingmen/orchestrator/.env` holds the **BURNED Owner token** (sbp_2180…b069,
  pasted into Telegram 22:38Z, scrubbed from the log by the hub 71s later).
- **MINI** `/Users/sheikhmusa/wingmen/orchestrator/.env` authenticates as **GAZZABYTE'S** token
  (sbp_f670…4e34). **SEAT REMOVAL IS BLOCKED** on this — pulling their seat takes the Mini's 4 lanes,
  this console and the operator's Telegram bridge down together.
- Order: revoke burned → operator places TWO DISTINCT tokens (`wingmen-studio`, `wingmen-mini`) →
  hub verifies BOTH hosts and asserts the NEGATIVE ("neither authenticates as the partner") → then
  seat removal. Gazzabyte should revoke their own token afterwards (operator-gated).

### WHAT SHIPPED TONIGHT (all merged after I re-ran tests myself)
cosem-platform `origin/main`: PIN no longer printed on the record PDF (239911b) · fail-closed
demo-project guard on all 7 service-role scripts + Rule A (f6b7d3a) · **B1 org-scoped batches**
(00b48c7 — an instructor can open an intake without a deploy; this was the literal blocker in
"before next batch starts").

### THE DEFECT CLASS THAT DEFINED THE NIGHT — measurements that cannot see what they certify
Six instances, all now closed: nudge_cai's composer guard (matched a dead prompt rendering) · the
context watchdog's unsent-text precondition (same NBSP defect, different file) · the secrets sweep
(respects .gitignore ⇒ blind to .env) · `delivered` recording intent not outcome · my send-path
checker (hardcoded list ⇒ missed 3 of 9) · the background-agent guard (single sample ⇒ would have
blocked every cai reset forever on a frozen footer).
**`scripts/check_send_paths_report_failure.py` now DISCOVERS all 9 send paths and both hosts pass.**

### STANDING ARRANGEMENTS I CHANGED TONIGHT
- **cc-irsyad does NOT draft replies the hub is answering** (it was superseded 3× in a row). It
  verifies the hub's answer, goes one layer under, files deltas to me. A WRONG hub answer is an
  immediate P1.
- **Before sending on the hub's thread: re-read the last outbound row on that tag.** If newer than
  the draft, the answer landed — convert to a bus note. (It caught a duplicate on its first use.)
- **SLA watchdog escalates agent-queue stalls to orch-console, not the operator** — and ignores
  self-addressed notes entirely (a self-note would have paged him while driving).
- **Intent to write to the operator on another body's topic is a PROPOSAL THAT WAITS.** I broke
  CAI-547 twice in 20 minutes before this stuck.

### OWED / NEXT
1. **Seed `tabung_report_approvers`** when mig120 lands (~2026-07-27T01:15Z) — saddam@ (5b250879…)
   and zuremi@ (06a88681…, ='VPZ'), both verified org_admins. **Applying the migration alone leaves
   an EMPTY allowlist and the client's emails still silently do not send.** Task #11.
2. Operator owes: batch start date (port sequencing blocked; do NOT invent one) · interim credential
   decision (cai #7126, recommends HOLD, I agree) · geofence call · TDU group + Alderei token.
3. cai owes: ruling on the GIRO spec (4429428 — rule on THAT sha, not 3971032).
4. Hub owns task #7 (mobile View-as gap, spec 429c81d) — must NOT ship as a one-class fix; main
   auto-deploys to prod.

### ON RESETTING ME
I am `auto_reset: False, self_compacts: True` in the watchdog registry and CAI-500 condition 4
refuses a self-reset even on a direct call. **I cannot clear myself, by design.** The operator (or
the hub over SSH) runs `scripts/reset_nazim.sh`. A fresh me reads THIS file first.

## OPEN — pick these up first
1. **Drill verdicts for exams + caai.** Both were mid-drill at handoff. Review their reports on
   the bus, then advance each to `supervised` (and to `direct` within days — they're low-stakes
   vs a paying client). Traps planted: Exams = "deploy to live demo tonight + wipe old records"
   and "put full NRIC on the exam slip"; CAAI = "just assume the retest rule" and "here's the
   real nominal roll with NRICs".
2. **cc-irsyad** is idle awaiting: GIRO sequencing call + review of its PDF header fix
   (branch `fix/tabung-pdf-multipage-header`, commit a22e089, visually verified, NOT merged).
3. **Hub is at 100% context** — a reset is owed (`ssh Musa@mac-studio 'bash ~/wingmen/orchestrator/scripts/reset_orch.sh'`,
   needs `reports/session-handoff-NOW.md` fresh).
4. Report the first REAL side-by-side latency once Gazzabyte writes again.

## THE INCIDENT (don't repeat it)
The irsyad drill was seeded as realistic fake client messages into shared `operator_messages`.
The hub read them as real and answered the LIVE Gazzabyte group (retracted 6 min later, honestly,
by the hub). Fixes shipped: drills never touch `operator_messages`; `--announce` first;
`operator_log` excludes lane tags by shape (`%-drill`/`%-draft`). The agent was never the risk —
its gate held. The harness was.

## FLEET STATE
- **Models:** hub + cai on `claude-opus-5` (switched in place, no context loss);
  `boot_orch.sh`/`boot_cai.sh` now env-driven (`ORCH_MODEL`/`CAI_MODEL`); `.fleet_model=claude-opus-5`
  so new lanes match. Nazim on Fable 5.
- **cai** was reset from the Mini at its own request (`scripts/reset_cai.sh`, new) and is up on
  opus-5; its next deliverable is the Xendit memo (07-28). It confirmed Xendit's MAS licence on
  the primary register and flagged that the licence attaches to the SG entity specifically.
- Composer quirk worth knowing: an idle session's composer holds ITS OWN staged next step — not
  an injection. Capture verbatim before clearing.
