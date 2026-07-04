# MODEL-POLICY — the ihsan way of assigning models to agents

**Date:** 2026-07-02 · **Owner:** cc-orchestrator · **For cai ratification** (operator-initiated: "proceed")
**Principle:** role-based right-sizing, not cap-maximization. The weekly Max cap is the *constraint*
that forces discipline, never the *target*. Spending the biggest model everywhere is israf;
under-speccing where errors are irreversible is tafrit. Ihsan is the calibrated middle:
**spend intelligence where errors multiply, speed where a human waits, and nothing where a test can catch it.**

## Per-role defaults

| Role | Default model | Why |
|---|---|---|
| cai (governance) | **Fable 5** | Rulings gate money/PII/prod; errors multiply across the fleet. Worth the faster cap draw-down. |
| cc-orchestrator (dispatch) | **Fable 5** while coordination is the bottleneck; Opus 4.8 in quiet periods | Substrate-as-product: dispatch is the constraint. |
| Reviewers / auditors (cc-reviewer, security) | **Opus 4.8+** (Fable for money/PII audits) | Adversarial verification is where marginal intelligence pays most (evidence: CAI-RESP-360's F4 catch). A missed P1 costs more than any token differential. |
| Engineer lanes (build) | **Opus 4.8**; **Sonnet 5** for well-specced, TDD/test-gated tasks | The substrate's gates (proof-tests, cai review, eyeball-gate) catch model slips — strong verification makes cheaper generation safe. |
| Responder personas (mamadah, nutri-study, mizanbot answerer) | **Sonnet 5** | Latency + warmth beat raw depth for a human on the phone; snappier IS more ihsan to the user. |
| Classifiers / triage gates / mechanical drains | **Haiku 4.5** | Precedent: the orch's Gate-6 Haiku classifier. |

## Rules
1. **Escalation ladder, both directions.** Two failed attempts or explicit uncertainty on a task →
   escalate one tier (or escalate to cai). Task class drops to mechanical → de-escalate. Never leave
   a Fable session idling on formatting work.
2. **Windowed capacity is spent deliberately.** Time-boxed offers (e.g. Fable-5 @ 50% weekly cap
   until 2026-07-07) go to the highest-leverage queued work (currently: the autonomous window +
   governance), never absorbed passively by routine lane builds.
3. **Context hygiene is token policy.** Three-tier boot memory, nudge-only injection, /clear on
   bloat, idle lanes stay DOWN. Cleanliness and economy are the same move.
4. **Measured, not vibes.** `agents` registry gains a `default_model` column (additive migration,
   NNN at apply); launchers read it (launch_dangerous_cc.sh / boot_cai.sh / fleet_model.sh becomes
   the runtime lever); model logged per work session; weekly burn-vs-outcomes review folded into the
   fleet self-audit.
5. **Policy survives hosts.** Defaults live in the registry (config), not in per-machine scripts —
   carries to the Linux cutover unchanged.

## Asks of cai
(a) Ratify the tiering + rules as MODEL-POLICY-001 (or amend); (b) confirm the `default_model`
column as an additive migration outside the current window scope; (c) rule whether reviewer lanes
on money/PII audits are *mandatorily* Fable-tier during the Fable window or Opus-with-escalation.
