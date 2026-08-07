# Chief of Staff — the Fleet's One Front Door

_Design spec (not a build). Written 2026-07-21. Phase 2 of `reports/fleet-target-topology-20260721.md`._

_Status: **DRAFT FOR REVIEW** — foundational architectural change (restructures how the operator talks to the fleet). Requires cai ratification before build, because it amends ORCH-TOPOLOGY-001's per-channel operator-thread-ownership rule (§6, §4). No live-bridge change is proposed by this document._

---

## 0. TL;DR (read this, then decide)

- **What:** The Chief of Staff (CoS) is a **role**, not a new body — Nazim (console) evolved into the operator's single point of contact, plus a **thin triage/routing layer** in the ingest path that dispatches behind the scenes. We explicitly **reject** standing up a brand-new always-on agent (it manufactures a new single point + a new context-bloat sink and duplicates what Nazim already is).
- **One door:** `nazim-console` (@nazim_cto_bot) becomes the operator's **canonical** door. The hub's `operator-orch` channel (@wingmennorchbot) is **demoted** from a daily-driver door to an internal executor + failover surface. The operator opens one thread; the split becomes invisible.
- **Resilience preserved:** CoS is a role **layered on top of** the existing two-body / two-host substrate — the bodies stay physically distinct, so Nazim can still SSH-reset a dead hub (the break-glass that saved us today). A new **`front_door_lease`** (parallel to `orch_lease`) fails the CoS role over to the hub if the Mini/Nazim dies, so the one door is **not** a new single point.
- **Pens unchanged:** the CoS **delegates** to pen-holders (dispatches a bus row to the hub for a hub-pen action); it never seizes pens. `orch_lease.py` and the deny-by-default ingest gate are untouched.
- **First ship (reversible, zero behavior change):** a **passive triage classifier** that annotates each inbound with a suggested route. Nazim reads the suggestion; nothing else changes. Confidence is earned before any auto-routing arms.
- **Top open questions:** (a) does the CoS get its own bot identity or keep @nazim_cto_bot? (b) how thin must the CoS stay to dodge the 100%-context death that hit the hub today? (c) the precise ORCH-TOPOLOGY-001 amendment for cai.

---

## 1. The problem (grounded in what exists today)

Today the operator talks to **two front doors** and carries the routing overhead himself:

- **Nazim / console** — @nazim_cto_bot, channel `nazim-console`, on the Mac Mini (`ORCH_BODY_ROLE=console`). Nazim replies with `scripts/nazim_send.sh`. Owns QR / cosem / general / CTO matters.
- **Hub / orch** — @wingmennorchbot, channel `operator-orch`, on the Studio (`ORCH_BODY_ROLE=hub`, holds the five `orch_lease` pens). Replies with `scripts/tg_send.sh` (gated by `orch_lease.py check`). Owns storefront / merchant / fleet-status.

The operator has to **know which body owns which topic** before he types. That routing choice is *his* — the target topology (§2 there) says it should be invisible. Worse, the two bodies are **single points**: when the hub degraded to 100% context today, coordination degraded with it, and the current mitigation for a cross-domain message is the `feedback_nazim_hub_thread_ownership` rule — "storefront msg on my channel = ROUTE to hub silently, don't double-answer." That rule is protective but it **leaks the split to the operator**: he messages Nazim, Nazim goes silent, and the answer surfaces on a *different bot's thread*. Two threads, one conversation — exactly the seam we want gone.

### What already works and must be preserved (the substrate is good)

The 2026-07-03 unified-ingest cutover (BOT-INGEST-TOPOLOGY-001 / CAI-RESP-357) already gives us most of the plumbing a front door needs:

- **`nervous_system/ingest.py`** — one daemon, config-driven `bot_channels` registry, strict per-update order **DEDUPE → LOG → GATE → ROUTE**. Route modes: `agent-session` (tmux nudge), `ai-responder` (persona drain), `log-and-route` (nudge the hub). "Transport only — no brains here" (amendment A2).
- **`nervous_system/tg_out.py`** — one outbound queue (`tg_out` table), at-least-once, chunked, audited.
- **`nervous_system/operator_log.py`** — durable log (`operator_messages`) as source of truth (Option B), with **body-scoped reconciliation**: `_channel_scope_sql()` already carves the operator surface so `console` sees `tmux-console` + `nazim-console`, `hub` sees everything else, shared feeds (`war-room`, `hafiz-partner`) excluded from both.
- **`nervous_system/responder_runner.py`** — `ai-responder` channels drain to registered persona handlers in a **separate process** (a hung persona can't dark the operator channel). This is the domain-agent seam the CoS will reuse (mamadah, Ray-AI/AI-CA).
- **`scripts/lib/orch_lease.py`** — the pen gate: fail-closed for `console`, fail-safe for `hub`, CAS `take` for DR. **This is the pattern the front-door lease copies.**

The CoS is therefore **mostly a re-wiring of existing pieces**, not a green-field build. That is the whole reason it can ship incrementally and reversibly.

---

## 2. What it is (recommendation, with the alternatives judged)

**Three candidate forms were on the table:**

| Form | Verdict | Why |
|---|---|---|
| (a) A **new always-on agent** that owns the operator relationship | **Reject** | Adds a third body = a *new* single point and a *new* context-bloat sink (it would accrete the entire operator conversation — the exact 100% death that hit the hub). Duplicates the CTO/EA relationship Nazim already holds. Maximum build cost, worst resilience. |
| (b) **Nazim evolved** into the CoS role | **Adopt (relationship half)** | Nazim is *already* the operator's CTO/EA, already has his own sanctioned voice (`nazim_send.sh`), already reconciles both inboxes (`feedback_nazim_reconcile_both_inboxes`). The relationship owner exists — we promote it, we don't invent it. |
| (c) A **routing/triage layer** in front of both bodies | **Adopt (dispatch half)** | The ingest path (`DEDUPE→LOG→GATE→ROUTE`) is the natural home for triage. Intelligence added at ROUTE, not a new brain. |

**Recommendation: (b) + (c). The Chief of Staff is a ROLE = { relationship owner: Nazim-persona } + { triage/dispatch layer: in ingest }, bound to a failover-able `front_door_lease`.**

Precise definition:

- The **relationship owner** is the Nazim persona — one coherent voice that owns the operator conversation, acknowledges everything, and presents a single thread. In steady state this runs as the console body on the Mini.
- The **triage/dispatch layer** is a new stage that runs *after* GATE in the ingest path: it classifies each inbound (domain, intent, executor) and decides **direct-answer** (CoS handles) vs **delegate** (to a lane, a hub-pen op, or a domain agent). It is stateless and thin.
- The **`front_door_lease`** binds "who is the relationship owner right now" to a body, exactly as `orch_lease` binds the pens. Normally the Mini/Nazim; fails over to the hub on death. This is what stops the one-door being a new single point.

Why a role and not a body: a role is **cheap to fail over** (flip a lease row) and **cheap to keep thin** (the persona relays; deep work lives in the executors' own context windows). A body is neither.

---

## 3. Unifying the two front doors

**Goal:** the operator opens **one** thread and never chooses a body again; the durable-log + reconciliation model is preserved unchanged.

### 3.1 Which channel becomes canonical — `nazim-console`

Recommend **`nazim-console` (@nazim_cto_bot) as the operator's canonical door**, for four reasons:

1. The operator already DMs it and has `/start`-ed it (`reference_nazim_channel_needs_operator_start`) — no new bot for him to adopt.
2. It already carries `nudge_when_busy=true` (CAI-RESP-382) — immediate delivery, which is what a front desk wants.
3. It's on the **Mini**, the host the operator has access to (`reference_operator_mini_access_not_studio`).
4. It is **structurally separate** from the hub's pen-(iv) channel, so promoting it does **not** entangle the front door with the pen gate.

**What happens to `operator-orch` (@wingmennorchbot):** it is **demoted, not deleted.** It stops being a door the operator is expected to open, and becomes:
- an **internal executor surface** — where the hub receives delegated work (via bus, not via the operator typing to it), and
- the **failover door** — if the CoS role fails over to the hub (§4.4), `operator-orch` is the channel it speaks on. Keeping the bot alive is *deliberate resilience*, not legacy cruft.

> Open question (§6): a purist "one identity" version would mint a neutral **@wingmen_chief** bot so the operator never sees "Nazim" vs "orch" branding at all, with both underlying bodies speaking through it. That is cleaner conceptually but adds a bot identity, a new `/start`, and a token to manage. **Recommendation: ship on @nazim_cto_bot first (zero operator friction); revisit a neutral identity only if the "Nazim" branding becomes a conceptual blocker.** The persona name is a paint color; the routing is the building.

### 3.2 The durable-log model is preserved exactly

Nothing about Option B changes. Every inbound still lands in `operator_messages` (DEDUPE→LOG), every outbound still flows through `tg_out`, reconciliation still runs on `operator_log.unprocessed()`. **The only change is the *scope* of ownership:**

- **Today:** `_channel_scope_sql()` splits the operator surface between two co-equal bodies — each owns a slice, each stays off the other's thread.
- **Under the CoS:** the **front-door-lease holder owns the whole operator conversation.** Executors (the hub's pens, lanes, personas) report their results *back through the CoS*, which relays them in the one voice — they do not open a parallel operator thread. Concretely, `_channel_scope_sql()` gains a third mode keyed on the front-door lease rather than a static `ORCH_BODY_ROLE`, so on failover the *reconciliation scope moves with the lease* (see §4.4).

This is the single doctrine change and the single reason cai sign-off is required (§6).

---

## 4. Routing / delegation — where the intelligence lives

The CoS presents one thread while dispatching to the right executor behind it. The mechanism, in the existing `DEDUPE → LOG → GATE → ROUTE` pipeline, adds a **TRIAGE** stage between GATE and ROUTE:

```
DEDUPE → LOG → GATE → [TRIAGE] → ROUTE
                          │
        ┌─────────────────┼───────────────────────────┐
        ▼                 ▼               ▼             ▼
   direct-answer     delegate→lane   delegate→        delegate→
   (CoS handles)     (bus row)       hub-pen (bus)    domain-agent
                                                      (ai-responder)
```

### 4.1 Triage — two tiers, deterministic first

The classifier decides **{domain, intent, executor, direct-vs-delegate}** for each inbound. Kept cheap and auditable:

1. **Tier 1 — deterministic (no LLM).** Reuse what exists: the `@tag` vocabulary (`@ihsanos`, `@qr`, `@cosem`, `@fleet`, `@cai`…), sender identity (`from_user_id`/`from_username`, already captured by BOT-INGEST-SENDER-001), channel, and reply-threading context. Most messages route on this alone. **Deterministic, logged, reversible.**
2. **Tier 2 — LLM classify (fallback, ambiguous only).** When Tier 1 is under-confident, a *small, bounded* classify call (Max plan, `feedback_max_plan_first`) maps free-text intent to a domain + executor. It returns a route + a confidence; **low confidence defaults to direct-answer by the CoS** (never a silent mis-route). Tie ambiguity is a flag, not a guess (`feedback_ingestion_orientation_agnostic` pattern: ambiguous = flag-not-guess).

**Where the intelligence lives:** the triage *decision* lives in a new pure-function module (call it `nervous_system/triage.py`) — unit-testable, no side effects, mirroring how `gate_allows()` is a pure function today. The *conversational judgment* (what to actually say, what to build) stays in the executors. The CoS brain is a **router + relay**, deliberately not a deep worker — this is the primary defense against context bloat (§6).

### 4.2 The four executor paths

- **direct-answer** — the CoS answers itself (status, acknowledgment, a quick factual reply, a genuine fork it must escalate to the operator). Uses the front-door voice.
- **delegate → lane** — a build/engineering ask goes to a tmux lane as an attributable **fleet-bus row** (never raw send-keys; `feedback` + ORCH-TOPOLOGY-001). The lane executes in *its own* context window.
- **delegate → hub-pen** — anything needing a singleton pen (a `tg_send` to a client group, a fleet-status assertion, bus-drain) is dispatched to the hub as a bus row. **The CoS never seizes the pen** — `orch_lease.py` still fail-closes it (§4.3). The hub executes and reports the result back to the CoS thread.
- **delegate → domain-agent** — a persona/vertical (mamadah second-brain, Ray-AI/AI-CA for cosem, nutri-study) is reached through the **existing `ai-responder` seam** (`responder_runner.py` + `HANDLERS` registry). No new mechanism — the CoS just chooses this route and the persona's reply is relayed back.

### 4.3 One thread, closed loops

The CoS **owns follow-up**. When it delegates, it records the dispatch (a `cos_dispatch` audit row: inbound id → executor → status) so it can:
- **acknowledge** immediately on the one thread (the existing `throttled_busy_ack` / `reassure_if_unhandled` machinery already does the "got it" beat — reuse it),
- **track** the delegated work to completion (executor writes result back via bus / `work_outputs`), and
- **relay** the result to the operator in the one voice, then **close the loop** by stamping `operator_log.mark_handled_through()`.

Escalate to the operator **only** on a genuine fork (money / safety / residency / irreversible / real judgment) — the `feedback_escalate_safety_forks_to_cai` + `feedback_dont_ask_when_path_is_clear` bar. Everything else the CoS resolves and reports.

### 4.4 Failover — the front door is NOT a new single point

A **new substrate table `front_door_lease`** (schema mirrors `orch_lease`: `holder`, `holder_host`, `acquired_at`, `renewed_at`, `ttl_seconds`, `taken_over_from`, `takeover_reason`) binds the CoS role to a body:

- **Steady state:** holder = Nazim @ Mini. The CoS relationship owner + reconciliation scope live there.
- **Health signal:** the SRE loop (cc-fleet-health, Phase 1) already senses body/host health. If the Mini/Nazim is dead past TTL, the hub runs a **CAS `take`** on `front_door_lease` (loud, reversible — identical to `orch_lease.py take`), flips itself to the CoS role, and begins answering the operator on `operator-orch` (its failover door, §3.1).
- **Scope moves with the lease:** because `_channel_scope_sql()` keys reconciliation on the front-door lease (§3.2), the operator conversation is *automatically* owned by whichever body holds the lease — no message is stranded.
- **Reversible:** when the Mini recovers, the lease hands back (CAS), and the CoS role returns to Nazim.

**Critically, `front_door_lease` and `orch_lease` are independent.** In steady state CoS = Mini and pens = Studio — two roles on two hosts. This is what **preserves the break-glass** (§4.5): Nazim (CoS, Mini) can still SSH-reset a dead hub (pens, Studio) because they were never merged. The one-front-door is a role *above* the two-body substrate, not a collapse *of* it.

### 4.5 Preserve-what-works checklist (explicit)

| Thing that works today | Under the CoS |
|---|---|
| **Break-glass** (Nazim resets a dead hub from the Mini, `reference_studio_hub_breakglass_from_mini`) | **Preserved** — two bodies / two hosts stay distinct; CoS is a lease-role on top, not a merge. |
| **Pen model** (ORCH-TOPOLOGY-001, `orch_lease.py`) | **Unchanged** — CoS *delegates* to pen-holders via bus; never seizes a pen; gate still fail-closes. |
| **Deny-by-default gate** (`gate_allows`, empty allowlist = accept nothing) | **Unchanged** — GATE still runs *before* TRIAGE. Triage never sees a gated message. |
| **Audit log** (`operator_messages`, Option B source of truth) | **Unchanged + extended** — add a `cos_dispatch` audit trail (what routed where) on top. |
| **at-least-once inbound/outbound** (ingest offset fail-safe, `tg_out` retries) | **Unchanged** — CoS rides the same queues. |
| **No single point** | **Improved** — `front_door_lease` failover means the one door survives a body/host death. |

---

## 5. Migration path (incremental, reversible, non-disruptive)

Every step is gated the target-topology way: **provably safe + dead-man's-switch (fails loud) + reversible.** Each ships behind a config flag that reverts to today's behavior.

- **Step 0 — substrate (DONE).** Unified ingest, durable log, gate, `tg_out`, body-scoped reconciliation, `orch_lease` pattern, `ai-responder` seam all exist. No work.

- **Step 1 — PASSIVE TRIAGE (ships first; zero behavior change).** Add `nervous_system/triage.py` (pure function) + a `cos_triage` annotation written alongside each inbound: `{suggested_domain, suggested_executor, direct_vs_delegate, confidence, tier}`. **No routing changes** — Nazim just *reads* the suggestion when he reconciles. This is pure observation: it builds confidence in the classifier against real traffic at zero risk. **Dead-man's-switch:** classifier error → annotation is null → today's manual behavior. **Reversible:** stop reading the column. *This is the smallest useful thing and it ships alone.*

- **Step 2 — SINGLE-DOOR FRAMING (the operator-visible win).** Make `nazim-console` canonical. Replace the current cross-domain rule (`feedback_nazim_hub_thread_ownership`: "storefront on my channel → go silent, hub answers on its thread") with: **the CoS acks on `nazim-console`, dispatches to the hub via bus, and relays the hub's answer back on `nazim-console`.** The operator now sees **one thread** for a cross-domain message. **Reversible:** a `FRONT_DOOR_MODE=unified|per-channel` flag flips back to today's per-channel ownership. **Requires cai** (this is the ORCH-TOPOLOGY-001 amendment — §6). Guard against the double-answer window: while `unified` is on, the hub's reconciliation scope excludes the operator DM entirely (it only speaks when *relaying-back to the CoS*, never direct-to-operator).

- **Step 3 — ACTIVE ROUTING.** Arm the triage layer to auto-dispatch (lane / hub-pen / domain-agent) using the Step-1 classifier, now trusted. CoS owns follow-up + loop-closure (`cos_dispatch` tracking, §4.3). Escalate-only forks. **Reversible:** drop back to Step-2 (CoS acks + a human decides the route). Depends on Phase-1 closed loops (the SRE hire) so the CoS isn't hand-driving upkeep while also routing.

- **Step 4 — FRONT-DOOR FAILOVER.** Add `front_door_lease` + the hub's CAS-takeover path + lease-keyed reconciliation scope (§4.4). Now the one door survives a Mini/Nazim death. **Reversible:** lease hands back on recovery. This is the step that formally discharges "no single point" for the front door.

**Ordering rationale:** Step 1 is free and de-risks everything downstream. Step 2 delivers the operator-visible "one door" and is the only step needing doctrine change, so it's worth doing early to get the cai amendment settled. Steps 3–4 add autonomy and resilience on the now-proven base.

---

## 6. Risks + open questions

**Risks (with mitigations):**

- **Single-point risk** — one door = one throat to choke. *Mitigation:* `front_door_lease` failover (Step 4); the door is a role, not a body. *Open Q:* must **two** bodies be warm-and-CoS-capable at all times, or is a cold hub-takeover (seconds of lag) acceptable? Recommend cold-takeover to start (cheaper), warm only if the lag hurts.
- **Context bloat** — one interface accreting every conversation is *precisely* the 100%-context degradation that killed the hub today. *Mitigation, load-bearing:* the CoS is a **router + relay, not a deep worker** — all deep work delegates to executors with their own windows (§4.1). The CoS body inherits the **auto context-reset** detect→act loop (f77b434, awaiting cai arm) so it self-heals; because the durable log is the source of truth, a reset CoS reconstitutes the conversation from `operator_messages`. *Open Q:* what is the CoS context budget, and at what threshold does it delegate-more vs hold? Needs a number, tied to the auto-reset trigger.
- **Governance / ORCH-TOPOLOGY-001** — the CoS **amends** the per-channel operator-thread-ownership rule: today "each body stays off the other's thread"; under the CoS "the front-door-lease holder relays across the boundary." The **pen model is untouched**, but thread-ownership is doctrine. *This needs cai ratification (`feedback_always_run_gates_through_cai`).* The precise ask: _"Amend ORCH-TOPOLOGY-001 per-channel thread-ownership so that the front-door-lease holder owns the whole operator conversation and relays executor results into one thread; pens, gate, break-glass separation, and the durable-log/Option-B model are unchanged."_
- **Double-answer window during migration** — while both the old per-channel rule and the new unified rule could be live, the operator might get two answers. *Mitigation:* the `FRONT_DOOR_MODE` flag is mutually exclusive; in `unified` the hub is hard-scoped out of the direct-to-operator path (§ Step 2). Never both at once.
- **Mis-route / silent drop** — a wrong classification could send an operator message into the void. *Mitigation:* low confidence = direct-answer by CoS (never a silent delegate); every dispatch is audited (`cos_dispatch`); Option B still guarantees an unhandled inbound resurfaces. A mis-route is at worst a slow answer, never a lost one.

**Open questions for the operator / cai:**

1. **Bot identity** — keep @nazim_cto_bot as the canonical door (zero friction, "Nazim" branding), or mint a neutral @wingmen_chief? (Recommend: keep Nazim first.)
2. **Context budget** — the CoS's max context before it must hand off / reset (§ context-bloat).
3. **Doctrine amendment** — cai's ruling on the ORCH-TOPOLOGY-001 thread-ownership change (the exact ask above).
4. **Warm vs cold failover** — is a few-seconds hub-takeover lag acceptable, or must a second CoS-capable body stay warm?
5. **Persona vs orchestrator voice** — when the CoS relays a domain-agent (Ray-AI) answer, does it speak *as* the CoS or attribute *to* the specialist? (Recommend: CoS voice, attributed — "Ray (cosem) says…" — so the operator always knows who did the work.)

---

## 7. How it advances the topology + the company org

**Topology (`fleet-target-topology-20260721.md`):** the CoS *is* Phase 2 ("Unify the front door"). It sits on Phase 1 (closed detect→act loops, the SRE hire cc-fleet-health) — those loops mean the CoS routes *work*, not upkeep. It sets up Phase 3 (goal queue): the CoS is the natural **producer of goals** — it turns operator intent into queued goals that agents self-assign, so "direction-not-dispatch" flows through it. And Step 4's `front_door_lease` is a down-payment on Phase 4 (failover the single points), reusing the exact `orch_lease` CAS pattern.

**Company org (the "hires"):** the fleet is being built as a company, and the CoS is the **EA / Chief of Staff** — the operator's single point of contact who triages and routes to the right department:

- **SRE** (cc-fleet-health, running) — keeps infra healthy so the CoS never hand-drives upkeep.
- **Chief of Staff** (this spec) — the operator's one door; routes to the right department; owns follow-up.
- **Head of Quality** (next) — gates deliverables behind the CoS so nothing ships un-reviewed.
- **Revenue + client-success** (next) — own external relationships.

The deepest payoff: **the CoS is the internal prototype of the client-success interface.** "One relationship owner, triage-and-route behind" is exactly how a *client* (Gazzabyte, ADCDA, Elly) should experience the fleet — one contact, invisible machinery. Build the operator's front door well, and the same pattern becomes every client's front door.

---

## 8. RECOMMENDED design (decision)

> **The Chief of Staff is a failover-able ROLE — Nazim evolved into the operator's single relationship owner, plus a thin, pure-function triage/dispatch layer in the ingest path — bound to a new `front_door_lease` that mirrors `orch_lease`.** `nazim-console` becomes the canonical door; `operator-orch` is demoted to an internal-executor + failover surface. The CoS *delegates* to lanes, hub-pens, and domain-agents (reusing the fleet bus and the `ai-responder` seam), presents one thread, owns follow-up, and escalates only genuine forks. The pen model, deny-by-default gate, durable-log/Option-B model, and the two-body break-glass separation are all preserved unchanged; the one doctrine change (per-channel thread-ownership → front-door-lease ownership) goes to cai.

### Phased build plan

| Phase | Ships | Reversible via | Gate |
|---|---|---|---|
| **0** | (done) unified ingest, log, gate, tg_out, lease pattern, ai-responder | — | — |
| **1** | `nervous_system/triage.py` (pure fn) + passive `cos_triage` annotation — **zero behavior change** | stop reading the column | none (observation only) |
| **2** | `nazim-console` canonical; CoS acks + relays hub answers into one thread; `FRONT_DOOR_MODE=unified\|per-channel` flag | flip flag to `per-channel` | **cai** (ORCH-TOPOLOGY-001 amendment) |
| **3** | arm active routing (lane / hub-pen / domain-agent) + `cos_dispatch` follow-up/loop-closure | drop to Step-2 (ack + human route) | depends on Phase-1 loops |
| **4** | `front_door_lease` + hub CAS-takeover + lease-keyed reconciliation scope | lease hands back on recovery | cai (parallels `orch_lease` DR) |

**Ship Step 1 first.** It is free, it de-risks the classifier against real traffic, and it changes nothing the operator can feel. Everything after it is a config flag away from today.
