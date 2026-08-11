# auto-recycle-on-bloat — design (cc-fleet-health, for orch-console review)

**Goal (op#18444, operator):** a body crossing the RED context threshold gets recycled
*autonomously* — no human flagging — so the operator never re-flags bloat. Independent
trigger, same pattern as the sre-liveness watchdog. It FIRES the reset scripts, so it is
kill-switch-adjacent → console reviews this design before I build, and reviews the diff.

## Architecture (mirror dev.wingmen.sre-liveness)
- **Independent launchd job** `dev.wingmen.auto-recycle-on-bloat`, NOT tied to any agent's
  heartbeat (that independence is the point — the recycler must survive the recycled body).
  StartInterval ~180–300s.
- Reads the **same context gauge** context_health_watchdog uses (cc_session_costs bloat).
- For each body at/над RED (≥ threshold, propose 88%), evaluate the SAFETY GATES below; only
  a body clearing ALL gates is recycled. Everything is detect→log→act, fail-loud, reversible.

## SAFETY GATES (each learned the hard way this session; all must hold to fire)
1. **CLEAN-IDLE BOUNDARY (not a flicker).** The body must be at a *sustained* clean idle —
   idle across K consecutive checks (~3×20s), no `esc to interrupt`. Recycling a busy lane
   discards in-flight work. (This session: reset_lane correctly refused busy cc-irsyad-2.)
2. **NOT-SELF-COMPACTED RE-CHECK (the critical one).** IMMEDIATELY before firing, re-read the
   body's context% — if it dropped below threshold, SKIP. Bodies at ~100% frequently
   AUTO-COMPACT mid-turn and self-recover (this session: cc-irsyad-1 AND cc-irsyad-2 both went
   100%→~35% green while working). Firing on a stale-high reading would force-discard live work
   on a lane that already self-healed.
3. **COMPOSER GHOST/STAGED-SAFE.** The (b') CC_GHOST fix + the reset scripts' composer-preserve
   (logs verbatim, CC_EMPTY!=1) + verify-empty (proceeds on CC_GHOST, refuses on real residue)
   already make the reset ghost-safe and never silently drop staged work. Nothing extra needed;
   the recycler just uses reset_lane/reset_* which inherit it.
4. **TIER / SCOPE BOUNDARY (charter §3 — non-negotiable).** Auto-recycle fires ONLY on WORKER
   LANES (Tier A/B) — the bulk of bloat. It NEVER blind-fires a governance singleton:
   - **cai (Tier C):** DETECT + NUDGE-TO-SELF-ARM only (cai must say 'ready, fire my recycle').
     Never unilateral (governance-continuity gate).
   - **hub / orch-console (supervised singletons):** DETECT + ESCALATE for supervised reset,
     never blind-fire.
   - **cc-fleet-health (me):** never self-red-reset (assert_no_sre_red_reset); the sre-liveness
     watchdog + operator handle a wedged SRE.
   So the operator's "stop flagging bloat" is satisfied for the COMMON case (worker lanes
   auto-recycle) while singletons get auto-nudged/escalated instead of the operator noticing.
5. **RE-DERIVE / FRESH-HANDOFF BOOT.** The reset boot must reconstitute the body: worker lanes
   get a re-derive boot (charter + bus-reconcile-on-wake + worktree/PR resume + residency);
   any body with a handoff mechanism uses/writes a fresh one. Durable state (committed code,
   bus, worktree) survives the in-place /clear; the boot points the fresh body at it.
6. **DEBOUNCE / ANTI-LOOP.** Never recycle the same body twice within a window. A body that
   re-bloats fast has a deeper issue → ESCALATE to the operator, do NOT loop-recycle.
7. **DEAD-MAN'S-SWITCH.** If a reset REFUSES or errors, FAIL LOUD (operator bus escalation),
   leave the body unchanged. Never silent. Every fire logs an attributable record
   (agent_messages from cc-fleet-health + a stamped log line).

## Observe-first ladder (binding, per CAI-768 auto-recycle blessing)
Ship in armed STAGES, each separately proven — never fully-armed on day 1:
- **Stage 0 (detect-only / dry-run):** log exactly which bodies it WOULD recycle + why, fire
  NOTHING. Prove the gate logic against real bloat over several days (esp. gate 2 — that it
  correctly SKIPS self-compacting lanes).
- **Stage 1 (supervised):** on a clear worker-lane candidate, it PROPOSES + I confirm before it
  fires. Prove the reset+boot round-trip.
- **Stage 2 (armed, worker-lanes only):** fires autonomously on worker lanes clearing all gates.
  Singletons stay detect+escalate forever (never armed).

## Open questions for console
1. RED threshold: 88%? And a separate higher "hard" line (e.g. 95%) that shortens the
   clean-idle wait (a body about to wall can't afford a long idle-wait)?
2. Worktree lanes with no handoff file: is the re-derive boot (charter+bus+worktree) sufficient,
   or should the recycler first NUDGE the lane to write a checkpoint at a clean idle, then reset?
3. Debounce window (e.g. 2h) + escalate-on-re-bloat — agree?
4. Does this live as a standalone script + plist (like sre_liveness_watchdog.py), or fold the
   trigger into an existing in-loop watcher? I lean standalone (independence, matches the pattern).

## Files (once blessed)
- `scripts/auto_recycle_on_bloat.py` (the trigger + gates + dry-run mode)
- `~/Library/LaunchAgents/dev.wingmen.auto-recycle-on-bloat.plist`
- tests: gate logic (esp. gate 2 self-compact-skip, gate 4 tier boundary) as pure unit tests.

Reviewer: orch-console (kill-switch-adjacent). I build + TDD + commit+push after the design + the
Stage-0 diff are blessed.
