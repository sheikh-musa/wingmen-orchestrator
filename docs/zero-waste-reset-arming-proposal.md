# Context-ceiling auto-RESET: arming proposal (op#35929/35930)

For orch-console/cai/Musa to rule on. cc-fleet-health does not self-arm this —
see `scripts/lib/fleet_health_boundaries.py::assert_no_sre_red_reset`.

## What already auto-fires today (no change needed)

The CHECKPOINT half is already live, unconditionally, fleet-wide:
`launchd/dev.wingmen.context-health-watchdog.plist` runs
`context_health_watchdog.py --alert --arm=amber` every 10 minutes
(`StartInterval=600`, confirmed loaded and ticking). Any `auto_reset: true`
body (today: the hub, cai) that crosses `_SOFT` (60% of its context window)
gets auto-checkpoint-nudged — a fresh handoff, no `/clear`, deduped
30 min apart (`_CHECKPOINT_DEDUP_MIN`). This is the full "ceiling triggers
checkpoint" ask. Nothing to build.

The RED `/clear` executor (`run_executor(..., arm_level="red")` /
`_do_reset`) exists, is heavily gated, and is exercised by tests — but no
scheduler anywhere passes `--arm=red`. It only runs if a human invokes it
by hand. That is the actual gap: "ceiling triggers reset" has no automatic
trigger today, by design (CAI-500).

## What the red path already checks, every time, live (not a proposal — this is what's in the code now)

Before any `/clear` is even attempted:
1. Body is in the hand-maintained `_AGENT_REGISTRY` with `auto_reset: true`
   (never self-compacting bodies — orch-console/cai-when-self-compacting
   /cc-quality/cc-fleet-health are all `auto_reset: false`).
2. Pane reachable, idle (`esc to interrupt` NOT showing), authenticated
   (no login/error/model-picker screen).
3. **Nothing owed** (`_owed_action_in_flight`): operator inbox provably
   drained for that body's scope, zero open `exec_work_items`, pane idle.
   Any one of these being *unprovable* (DB down, undeclared scope) counts
   as owed — fails closed.
4. A fresh handoff exists or gets checkpointed first; re-verified idle+auth
   immediately before the destructive step (state can change while writing
   the handoff).
5. **Capture verified** (`_verify_capture_before_clear`): handoff on disk,
   inbox still drained (re-read), no unsent input-box text, no *live*
   background agents (two-sample timer check — a frozen footer doesn't
   block it, a genuinely advancing one does).
6. `/clear` is phantom-guarded: typed, pane re-captured to confirm the text
   actually landed in the box, only then Enter.
7. Post-clear: pane still reachable + authenticated, else abort loud and
   leave the saved handoff as the recovery path.
8. **Proof, not inference**: `_confirm_reset` requires a NEW
   `cc_session_costs` session_id with token count collapsed to ≤50% of the
   pre-clear reading before the word "reset" is ever used. Three honest
   outcomes (`confirmed-reset` / `confirmed-not-reset` / `unconfirmed`,
   never collapsed into each other) — this exists because of a real 2026-07-26
   incident where "sent the keystrokes" got reported as "cleared."
9. De-duped 30 min apart (`_RESET_DEDUP_MIN`); any failure at any step pages
   loudly via `nazim_send.sh` and leaves the body intact.

This is already a strong gate set. The question for arming isn't "is the
per-attempt safety good enough" (it already looks thorough) — it's "who
decides WHEN to fire it, and under what ceiling-crossing conditions, and
what stops a bad day from becoming many resets."

## Gaps relative to a safe AUTOMATIC trigger (not yet built, proposed here)

1. **No circuit breaker / daily cap.** `_RESET_DEDUP_MIN=30` only prevents
   re-firing the *same* body within 30 minutes — nothing caps total resets
   per body per day, or fleet-wide per day. A body that oscillates near the
   ceiling (checkpoints, dips under the dedup window, crosses again) could
   legitimately re-fire every 30 min for hours with nobody looking. Propose:
   a hard cap (e.g. 3 red resets per body per rolling 24h) that, once hit,
   downgrades to checkpoint-only + a loud page instead of continuing to
   auto-reset — the body needs a human look, not another `/clear`.
2. **No explicit "who armed this" record.** Today arming is a CLI flag a
   human types. An automatic trigger needs its own attributable row (who/
   what set it running this cycle, under which policy version) so a reset
   is traceable to a decision, not just a flag that happened to be set.
3. **No separation between "detected the ceiling" and "decided to act."**
   Proposed: the ceiling-crossing DETECTION (already live, `--json`/`--alert`)
   feeds a small, separately-reviewable **policy gate** that decides whether
   THIS crossing, for THIS body, right now, is allowed to arm red — not the
   watchdog's own `--arm` flag flipped wholesale. Concretely: a new
   `reset_policy.py` (or similar) that reads the ceiling-crossing event +
   the circuit-breaker state + a time-of-day/quiet-window check, and is the
   ONLY thing allowed to pass `arm_level="red"` into `run_executor`. This
   keeps the per-step live gates above untouched and adds one more layer
   in front of them.
4. **No quiet-window / blast-radius limit.** Proposed: auto-red stays
   confined to the exact two bodies it's scoped for today (hub, cai) —
   never silently widen to a newly-registered `auto_reset: true` body
   without a separate review, even if that body technically passes every
   gate above. The registry is already hand-maintained and reviewable
   (`_AGENT_REGISTRY`'s own comment says so) — keep it that way; don't let
   the arming policy auto-discover new candidates.

## Who should hold the arming authority (not cc-fleet-health)

Not a promise, a concrete proposal: the policy gate in (3) above should be
a SEPARATE CAI-500-gated artifact from this watchdog — reviewed and
version-pinned the same way `context_health_watchdog.py --arm=red` itself
was gated (cai sign-off before the flag exists at all, per the file's own
header: *"cai gated it on stricter conditions not yet built. To enable
auto-reset later: change --arm=amber -> --arm=red, ONLY after those gates +
review"*). Concretely:

- **cai** reviews and version-pins the policy gate's exact conditions
  (ceiling %, circuit-breaker cap, quiet-window rules) before it can run
  unattended — same bar as the existing CAI-500 conditions already in code.
- **orch-console** (or a future autoscaler role) is the one who flips the
  scheduler from `--arm=amber` to `--arm=red` for a given body, as an
  attributable, logged action — never a background launchd job silently
  escalating its own arm level.
- **cc-fleet-health** stays exactly where it is: builds/maintains the
  detection, checkpoint, and policy-gate CODE, reports ceiling crossings
  and circuit-breaker trips, and never flips the arm level itself
  (`assert_no_sre_red_reset` stays unmodified and enforces this).

## Sizing if cai/console want this built

- Daily reset cap / circuit breaker: small, ~1-2h (a counter in the existing
  `_EXEC_STATE_FILE` JSON, checked in `run_executor` before calling
  `_do_reset`).
- `reset_policy.py` gate + attributable arming record: medium, ~3-4h
  (new module + tests; `run_executor`'s `arm_level` param already exists as
  the seam to call it through).
- Quiet-window/blast-radius guard: small, ~1h (registry already explicit;
  mostly a policy check + test).

None of this is built yet. If cai/console GO on the policy direction above,
cc-fleet-health can build all three; the actual arm-level flip for a given
body stays console's attributable action, not a code change.
