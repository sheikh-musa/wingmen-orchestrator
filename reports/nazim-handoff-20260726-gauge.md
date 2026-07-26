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
1. **Gauge divisor** — the hub's TUI read 100% while our gauge said 73% "no action yet". We divide by the
   NOMINAL 1M window; a body degrades against the USABLE one. `ed961fa` made the gauge COMPLETE, not
   correctly SCALED. **Unfixed.**
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
