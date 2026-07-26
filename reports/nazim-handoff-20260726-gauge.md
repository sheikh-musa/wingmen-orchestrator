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
   **Do not re-inflate the token item** — both work; exposure not outage; his own DM, not a group.

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
