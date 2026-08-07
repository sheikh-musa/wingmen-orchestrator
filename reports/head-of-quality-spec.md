# Head of Quality — Nothing Ships Below the Ihsan Bar

_Design spec (not a build). Written 2026-07-22. A "hire" from `reports/fleet-target-topology-20260721.md`; format/rigor mirror `reports/chief-of-staff-one-front-door-spec.md`._

_Status: **DRAFT FOR REVIEW**. This does not invent a new quality bar — the bar already exists as doctrine (`docs/ihsan-gate.md`, operator mandate 2026-07-10). It proposes making that bar a **standing, machine-enforced gate wired into every lane's build→merge→deploy loop**, with a failover-able owner, so quality stops depending on the hub's attention. The one governance change (moving the gate from "hub confirms before greenlighting" to "a lease-held gate blocks the merge") goes to cai — §6._

---

## 0. TL;DR (read this, then decide)

- **What:** The Head of Quality (HoQ) is a **role wired as a mandatory GATE**, not a new always-on agent. It is `docs/ihsan-gate.md` (which already exists as doctrine) **promoted from a checklist-the-hub-remembers into a pre-merge/pre-deploy gate every lane must pass**, plus the *reviewer capacity* the fleet already has (`spawn_reviewer.sh` → cc-reviewer, `spawn_uiux_review.sh` → cc-uiux, the `review_dimensions` table). We **reject** a single standing "quality agent" body — it would be a new single point, a context-bloat sink, and a bottleneck exactly where throughput matters most (the ship path).
- **The bar, made checkable:** the ihsan bar is codified into a versioned **`ihsan_gate_manifest`** — a per-change checklist a gate can mechanically evaluate (CI green, design pipeline for UI, mobile+desktop eyeball, role×flow synthetic matrix, deny-by-default scoping, no PII/secret leak, i18n parity, deployed==GitHub SHA, no test-pollution). Deterministic checks auto-pass/auto-fail; judgment items (design honesty, ihsan polish) route to a reviewer whose verdict is **advisory** (267-H1 preserved), not an auto-block.
- **Where it plugs in:** a **`quality_lease`-held pre-merge/pre-deploy checkpoint** (mirrors `orch_lease`/the CoS `front_door_lease`). A lane cannot merge-to-main or promote-to-prod on a client/prod path without a **green gate token** (`quality_gate_runs` row) scoped to that diff's SHA. The gate is **risk-tiered**: a docs tweak passes on the deterministic subset in seconds; a money/PII/client-facing change pulls the full gate + cai money-gate. No token, no merge — enforced in a merge/deploy wrapper, not by promise.
- **Not over-gating:** the gate blocks on **hard, objective failures** (red CI, console error, 4xx/5xx, PII leak, deployed≠GitHub) and on **missing mandatory reviews** — it does **not** auto-block on advisory taste calls. Genuine judgment forks escalate to cai/operator (the `feedback_loosen_nonmoney_gates_keep_audit` + "over-gating is a failure mode too" bar). The gate's job is to make the *floor* unmissable, not to substitute machine taste for the reviewer's.
- **Top open questions:** (a) is the pre-merge gate *blocking* from day one or *advisory-then-blocking* after a confidence period? (b) who owns the **bar definition** — does every manifest change need cai, or only ship-rule/threshold changes? (c) exact `quality_lease` ↔ `orch_lease`/`front_door_lease` relationship (same holder, or independent so a degraded hub can't dark quality?).

---

## 1. The problem (grounded in what exists today)

The fleet already has a quality bar and real machinery to meet it. What it does **not** have is a mechanism that makes meeting it **systematic and unmissable**. Three grounded facts:

**(1) The bar exists as doctrine, enforced by human/hub attention.** `docs/ihsan-gate.md` is explicit and binding — operator mandate 2026-07-10 ("make the factory ihsan so the products we deploy are also ihsan"). It lists six gate items (code review, UI/UX every-page mobile+desktop, CI green, security for money/PII/auth, ihsan polish, reproducible+deployed==SHA). But its enforcement clause is: _"The hub (cc-orchestrator) enforces it before greenlighting any lane's ship."_ That is the flaw. **Enforcement rides on the hub remembering to check** — the same hub that hit 100% context and degraded coordination this week. A gate that lives in one body's attention is an open loop with a human (or a single, saturable body) in the critical path — precisely the bottleneck the target topology names.

**(2) The reviewer capacity exists, on-request.** `scripts/spawn_reviewer.sh` boots a fresh, independent, read-only **cc-reviewer** that applies the `review_dimensions` substrate (finance / security / design / performance) and posts an artifact-cited, **advisory** verdict to the bus (design/perf are advisory per 267-H1 — they inform the operator/deploy gate, never auto-block). `scripts/spawn_uiux_review.sh` boots **cc-uiux**, which captures Playwright screenshots at 390px + 1440px and reviews the *render* (solving the fleet's terminal-blindness). These are excellent, but they fire **only when someone spawns them.** Nothing guarantees a given diff *was* reviewed before it merged. A `cc-cosem-qa` QA-EDGE lane existed as a per-feature gate this cycle — proof the pattern works, but it was a bespoke, lane-specific arrangement, not a fleet-standing guarantee.

**(3) The standing quality feedback the operator has set is a checklist without a checker.** From `CLAUDE.md` + `MEMORY.md`, the operator has repeatedly codified: _"the bar is always ihsan"_; _"design pipeline mandatory for all UI (frontend-design + cc-reviewer design dim, mobile-first)"_; _"synthetic-test gate every module pre-UAT (flow×role matrix, fail on console/4xx-5xx/error-toast)"_; _"test the blast radius not the operator"_; _"verify UI changes visually / always eyeball mobile before shipping"_; _"deployed code must match GitHub"_. Each is a rule an agent is *supposed* to follow. Following depends on the agent remembering and the hub double-checking. The failure mode is silent: a lane self-declares "prod-ready," the hub is busy, a sub-bar artifact reaches a client — the exact thing `ihsan-gate.md` warns "is worse than none."

**The pattern behind all three: the quality bar is real, the tools are real, but the *enforcement* is attention-dependent and therefore skippable.** The HoQ closes that loop — it turns "the hub should check" into "the merge cannot happen without a green gate."

### What already works and must be preserved

- **`docs/ihsan-gate.md`** — the doctrine + the six-item floor + the "not every item applies to every change" risk-scaling. The HoQ *operationalizes* this file; it does not replace it.
- **`review_dimensions` table + cc-reviewer + cc-uiux** — fresh, independent, adversarial reviewers that never inherit builder context (the `auto_agent_id` identity guardrail). The HoQ *invokes* these; it does not re-implement review.
- **Advisory-not-blocking for taste (267-H1)** — design/performance verdicts inform, they don't auto-block. The HoQ **keeps this** — it blocks on objective floor failures + *missing mandatory review*, not on a reviewer's taste call.
- **The lane ship loop** — branch → build → verify (typecheck / lint / boundaries / i18n / test) → preview → Playwright/eyeball → merge+deploy. The HoQ inserts one checkpoint into this loop; it doesn't rebuild it.
- **The lease pattern** (`orch_lease.py`, the CoS `front_door_lease`) — fail-closed, CAS-takeover, failover-able. The `quality_lease` copies it.

The HoQ is therefore, like the CoS, **mostly a re-wiring of existing pieces** — doctrine → gate, on-request reviewers → auto-invoked reviewers, hub-attention → lease-held checkpoint. That is why it can ship incrementally and reversibly.

---

## 2. What it is (recommendation, with the alternatives judged)

**Three candidate forms were on the table:**

| Form | Verdict | Why |
|---|---|---|
| (a) A **new always-on "Head of Quality" agent** every ship is routed to | **Reject** | Adds a body = a new single point on the *ship path* (the worst place to add one — it bottlenecks throughput). It would accrete every diff's context = a bloat sink. And a standing reviewer that reviews everything drifts toward rubber-stamping. Maximum cost, worst resilience, and it *centralizes* quality when the goal is to make it ambient. |
| (b) A **mandatory GATE** wired into the build→merge→deploy loop, invoking ephemeral reviewers on demand | **Adopt (enforcement half)** | The gate is where "systematic, not on-request" lives. It is stateless, risk-tiered, and cannot be forgotten because the merge/deploy wrapper won't proceed without its token. Ephemeral reviewers (fresh cc-reviewer/cc-uiux per diff) keep review adversarial and context-clean. |
| (c) A **codified, versioned charter** (the bar as a checkable manifest) that both agents self-check against and the gate enforces | **Adopt (definition half)** | The bar can't be a vibe. A versioned `ihsan_gate_manifest` makes it a checklist a machine evaluates and a human ratifies — the single source of truth for "what ihsan means for this change class." |

**Recommendation: (b) + (c). The Head of Quality is a GATE-plus-CHARTER role = { the bar, codified as a versioned `ihsan_gate_manifest` } + { a `quality_lease`-held pre-merge/pre-deploy checkpoint that mechanically evaluates the manifest and auto-invokes the existing reviewers for judgment items }.** It is a **role, not a body** — for the same reason the CoS is: a role is cheap to fail over (flip a lease) and cheap to keep from bloating (the gate is a stateless evaluator; deep review lives in ephemeral reviewer lanes with their own windows).

Precise definition:

- **The charter (`ihsan_gate_manifest`)** — a versioned substrate object that maps a **change class** (docs / internal-tool / UI / money-PII-auth / client-facing-prod) to the **exact set of gate items that must hold**, each item tagged `deterministic` (machine-checkable) or `judgment` (reviewer-evaluated, advisory). This *is* `docs/ihsan-gate.md` turned into data. Versioned so a change to the bar is an auditable, cai-ratified event, not a quiet edit.
- **The gate (`quality_gate`)** — a stateless evaluator invoked at the pre-merge and pre-deploy seams. It resolves the change's class → pulls the manifest → runs deterministic checks → auto-invokes reviewers for judgment items → writes a `quality_gate_runs` verdict scoped to the diff SHA. **Green token required to merge/promote** on client/prod paths.
- **The `quality_lease`** — binds "who owns the gate right now" to a body, so the gate itself is not a new single point and cannot be darked by one host dying. Mirrors `orch_lease`.

Why a gate and not a body, restated for this domain: quality must be **ambient and unskippable**, not a station work queues at. A gate is ambient (it's the merge/deploy seam itself); a body is a queue. And a body reviewing everything becomes either a bottleneck or a rubber stamp — a gate + *fresh* reviewers-per-diff is neither.

---

## 3. The charter / the bar — codified into checkable gates

The ihsan bar becomes the versioned `ihsan_gate_manifest`. Each item is `deterministic` (D — machine auto-evaluates, hard pass/fail) or `judgment` (J — a fresh reviewer evaluates, advisory verdict, escalates on genuine forks). This is the checklist a gate enforces.

### 3.1 The gate items

| # | Gate item | Type | How it's checked | Blocks on |
|---|---|---|---|---|
| G1 | **CI green** — unit + e2e pass, lint 0 (incl. architecture/module-boundary lint), typecheck 0 | **D** | CI status for the diff SHA | any red |
| G2 | **Design pipeline for UI** — `frontend-design` applied + cc-reviewer design dim ran; mobile-first | **D** presence / **J** quality | presence of a design-pipeline artifact + a cc-uiux verdict row for the diff | *missing* pipeline artifact (D) blocks; the taste verdict (J) is advisory |
| G3 | **Mobile + desktop eyeball** — every changed page captured & read at 390px and 1440px | **D** presence / **J** quality | `spawn_uiux_review.sh` screenshot manifest exists for every changed route; cc-uiux read them | *missing* captures (D) blocks; render-quality (J) advisory |
| G4 | **Role×flow synthetic matrix** — every role × every touched flow driven headless | **D** | synthetic run artifact; **fail on: any console error, any 4xx/5xx, any error-toast, broken layout, missing empty-state** | any hard failure in the matrix |
| G5 | **Scoping / security** — deny-by-default preserved; least-privilege; RLS/permission DB-enforced not UI-only; **no PII or credential/secret in diff, logs, or client-facing output**; residency (TENANT-RESIDENCY-001) verified for client data | **D** (leak scan, gate-config diff) + **J** (cc-reviewer security dim) | secret/PII scanner + deny-by-default assertion + (for money/PII) cc-reviewer + cai | any secret/PII leak (D); missing security review on a money/PII path (D); residency unverified (D) |
| G6 | **i18n parity** — no missing keys, no hardcoded strings on a localized surface, both locales render | **D** | i18n lint / key-parity check | any missing key or hardcoded string on a localized surface |
| G7 | **Deployed == GitHub** — the promoted artifact's real source == the intended `main` SHA (trace CLI deploys — no git SHA → ahead-of-GitHub is a defect) | **D** | `git ls-remote`/rev-parse == deploy provenance (server / x-vercel-id headers); ancestor-of-origin/main check | deployed SHA ≠ intended SHA |
| G8 | **No test-pollution** — no synthetic/test rows, seed data, or scaffolding left in a prod/client store; migrations tracked (no out-of-band schema) | **D** | test-artifact scan on the target store + migration-tracking check | leftover test data on a client/prod store; untracked schema |
| G9 | **Ihsan polish** — on-brand, content-complete, no glitches, responsive, fast (measured load, no blocked-main-thread controls) | **J** | cc-reviewer design + performance dims (advisory, 267-H1) | *not auto-blocked* — advisory; escalates a genuine "this is sub-bar" call to operator/cai |
| G10 | **Reproducible + tracked** — committed on a branch, work-outputs/proofs on the bus (`work_outputs`), review verdicts + screenshots + CI + DB-proofs recorded | **D** | presence of the evidence bundle in `quality_gate_runs` | missing evidence bundle for the applicable subset |

### 3.2 Risk classes — which items apply (not-over-gating, encoded)

`ihsan-gate.md`'s "not every item applies to every change" becomes explicit, so the gate is proportionate:

| Change class | Applicable gate items | Extra |
|---|---|---|
| **Docs / comment / non-shipping** | G1 (if CI touched), G7, G10 | none — seconds |
| **Internal tool / non-client** | G1, G4 (light), G6, G7, G8, G10 | no cai |
| **UI (client-visible)** | G1, **G2, G3**, G4, G6, G7, G8, G9, G10 | full design pipeline |
| **Money / PII / auth** | **ALL**, + G5 mandatory cc-reviewer security | **cai money-gate + design ratification; require_verified_authorization; residency gate** |
| **Client-facing prod deploy** | **ALL** | the full floor is a hard wall |

The class is inferred from the diff (paths touched, whether a client/prod deploy target, whether money/PII/auth code) and is **overridable upward, never silently downward** — a lane can request a stricter class; downgrading a class is an audited, gated action. Ambiguous class → default to the stricter (ambiguous = flag-not-guess).

**The over-gating guard (explicit):** the gate blocks only on (a) deterministic hard failures and (b) *missing* mandatory reviews/evidence. It **never** blocks on a judgment reviewer's taste verdict — those stay advisory (267-H1), surface to the operator, and escalate to cai only as a genuine fork. A clean-but-imperfect UI ships with the advisory noted; it is not walled. This is the `feedback_loosen_nonmoney_gates_keep_audit` line: money/irreversible/residency = strict; non-money = flexible + logged.

---

## 4. How it plugs into the loop (systematic, not on-request)

The HoQ inserts **one checkpoint** into the existing ship loop, at the two seams where sub-bar work would otherwise escape:

```
branch → build → verify(tc/lint/boundaries/i18n/test) → preview → Playwright/eyeball
                                                                        │
                                                          ┌─────────────┴──────────────┐
                                                          ▼                             ▼
                                                  [ QUALITY GATE #1 ]           [ QUALITY GATE #2 ]
                                                   pre-MERGE-to-main            pre-DEPLOY-to-prod/client
                                                          │                             │
                                          resolve class → manifest → run D checks →
                                          auto-invoke J reviewers (cc-reviewer/cc-uiux) →
                                          write quality_gate_runs(diff_sha, verdict, evidence)
                                                          │                             │
                                          GREEN token? ──yes→ merge/deploy proceeds      │
                                             │no                                          │
                                             ▼                                            ▼
                                   BLOCK + reason + (fork? → escalate cai/operator)   BLOCK + reason
```

### 4.1 Where enforcement actually bites

- **Pre-merge (Gate #1):** the merge-to-`main` action for a client/prod-bound lane is wrapped so it **requires a green `quality_gate_runs` token scoped to `HEAD`'s SHA.** No token → the merge wrapper refuses (fail-closed), exactly like `orch_lease.py check` fail-closes `tg_send` for the console body. This is the "enforced in code, not by promise" move that made ORCH-TOPOLOGY-001 real.
- **Pre-deploy (Gate #2):** the promote-to-prod action (and any client-facing deploy) re-checks G7 (deployed==GitHub) + G8 (no test-pollution) + G5 residency against the *actual* deploy target at deploy time — because a merge-time green can go stale (a CLI deploy from a dirty tree, a wrong `--scope`). Gate #2 is the last wall before the world sees it.

### 4.2 How it blocks + escalates (proportionately)

- **Deterministic fail (D):** hard block, machine-precise reason ("G4: 500 on `/checkout` as role=merchant", "G7: deploy SHA a1b2 ≠ origin/main c3d4"). The lane fixes and re-runs. No human needed to *decide* — the failure is objective. This is a fix-loop, like the cosem per-feature QA sweep + fix pass this cycle.
- **Missing mandatory review (D):** block until the review exists ("money path with no cc-reviewer security verdict"). The gate **auto-invokes** the reviewer (`spawn_reviewer.sh` / `spawn_uiux_review.sh`) rather than paging a human — the block resolves itself by producing the missing evidence, then re-evaluates.
- **Judgment verdict (J):** **never a hard block.** Advisory verdict recorded + surfaced. Escalates to operator/cai **only** on a genuine fork — the reviewer flags "this is materially sub-bar for a client" (a real judgment call, not a nitpick). Everything a reviewer can resolve, it resolves; only true forks reach a human (`feedback_escalate_safety_forks_to_cai`, `feedback_dont_ask_when_path_is_clear`).
- **The gate never rules — it enforces the floor and routes judgment.** Verdicts stay advisory to cai (267-H1); cai/operator remain the authority on taste and on the bar itself (§6).

### 4.3 Closed loop, self-healing

The gate is a **closed detect→act loop** (Phase-1 pattern): it doesn't page a human to "please review before merge" — it *is* the merge seam, it auto-invokes the reviewers it needs, and it self-heals a missing-evidence block by producing the evidence. It escalates only genuine forks. Every run writes an auditable `quality_gate_runs` row (class, items evaluated, verdicts, evidence bundle, green/blocked) — the durable record that makes "was this reviewed?" answerable forever, and that lets a rebooted/failed-over gate reconstitute state.

---

## 5. Relationship to existing (no duplication, clear boundaries)

The HoQ **orchestrates** existing quality capacity; it does not re-implement it. Boundaries:

| Existing thing | What it does | HoQ boundary |
|---|---|---|
| **`docs/ihsan-gate.md`** | The doctrine + six-item floor + risk-scaling | HoQ **operationalizes** it — the manifest is this file as versioned data; the gate is its enforcement clause made mechanical. The doc stays the human-readable charter. |
| **cc-reviewer** (`spawn_reviewer.sh`) | Fresh, independent, adversarial code review; `review_dimensions` (finance/security/design/perf); advisory to cai | HoQ **auto-invokes** it for J-items instead of waiting for a human to spawn it. cc-reviewer keeps owning the *review*; HoQ owns *that it ran and its verdict is on file*. No change to cc-reviewer's logic or its advisory status. |
| **cc-uiux** (`spawn_uiux_review.sh`) | Screenshot capture (390/1440) + render review | HoQ **auto-invokes** it for G2/G3; requires a capture manifest per changed route. cc-uiux owns the render judgment; HoQ owns the requirement. |
| **QA-EDGE lane** (`cc-cosem-qa`) | Was a bespoke per-feature QA gate for one lane this cycle | HoQ **generalizes** it — the standing gate is QA-EDGE for *every* lane, not a per-lane bespoke arrangement. QA-EDGE becomes an instance of the gate, not a parallel thing. |
| **Design pipeline** (`frontend-design` + cc-reviewer design dim) | Mandatory 5-arm pipeline for UI | HoQ **checks it happened** (G2) — it's a manifest item, not a new pipeline. The design pipeline stays the producer; the gate is the checker. |
| **SRE — cc-fleet-health** (Phase 1) | Deploy-health, infra/body/host health, closed detect→act loops | **Clean split by timeline: HoQ gates BEFORE the world sees it (pre-merge/pre-deploy, "is it good?"); SRE watches AFTER (runtime health, "is it up and healthy?").** They meet at the deploy seam — HoQ's Gate #2 hands a green deploy to SRE's health watch. No overlap: pre-ship correctness vs post-ship liveness. G7 (deployed==GitHub) is the natural handoff artifact both care about. |
| **cai** | Governance authority; money-gate; ratifies doctrine | HoQ **routes to** cai for money/PII/residency (unchanged gates) and **owns the bar definition WITH cai** (§6). HoQ never overrides a cai gate; it enforces the floor beneath it. |

The one-line rule: **cc-reviewer/cc-uiux/design-pipeline are the *reviewers*; the HoQ is the *system that guarantees the right reviewers ran and the floor held before merge/deploy*.** Reviewers judge; the gate enforces-that-they-judged and blocks on objective floors.

---

## 6. Scaling — systematic without becoming the bottleneck

As lanes multiply, the gate must not become the new chokepoint. Four levers, all borrowed from patterns the fleet already trusts:

1. **Risk-based depth (the primary lever).** Most changes are cheap classes (docs, internal tools) that pass on deterministic checks in seconds — no reviewer spawn, no human. The expensive full gate fires only on UI / money / client-facing changes. Cost is proportional to blast radius, so the gate scales with *risk*, not with *volume*.
2. **Parallel, ephemeral reviewers.** J-items spawn **fresh cc-reviewer/cc-uiux lanes per diff** (the `spawn_reviewer.sh` model — each in its own tmux session, its own context window). N lanes shipping = N independent gate runs in parallel; the gate is stateless so there is no shared queue to serialize on. This is `feedback_parallelize_by_default` applied to QA — tokens aren't the constraint.
3. **Deterministic-first, LLM-last.** Every D-item (CI, leak scan, SHA check, i18n parity, synthetic matrix) runs without an LLM — fast, free, auditable. A reviewer (cost) is invoked only for genuine J-items, and only for the change classes that need them. The gate spends judgment where it matters and nowhere else.
4. **Cache by SHA.** A `quality_gate_runs` verdict is scoped to a diff SHA; re-running the gate on an unchanged SHA returns the cached green. Only the delta since the last green is re-reviewed — the gate never re-does settled work.

The scaling failure mode to watch: a flaky synthetic matrix or a slow reviewer making the gate the thing lanes wait on. Mitigation: the gate reports its own latency (an SRE-style self-metric), flakes are quarantined (a flaky check is a bug in the check, not a license to skip the gate), and the deterministic floor never depends on a reviewer being fast.

---

## 7. Its place in the org / topology + open questions

**Topology (`fleet-target-topology-20260721.md`):** the HoQ is a **Phase-1 closed loop applied to the ship path.** The target topology's Phase 1 is "close the detect→act loops — every watchdog acts and self-heals." The HoQ is exactly that for quality: today a human/hub *detects* sub-bar work (or fails to) and *acts* by blocking; the HoQ makes that loop closed and automatic — the gate detects (evaluates the manifest) and acts (blocks + auto-invokes reviewers), escalating only genuine forks. It also feeds **Phase 3 (goal queue + autonomous pickup)**: when agents self-assign work off a goal queue, the gate is what lets them ship *without* a human in the loop safely — autonomous pickup is only trustworthy if a machine floor guarantees nothing sub-bar escapes. The gate is the safety rail that makes hands-off shipping possible.

**Company org (the "hires"):** the fleet is being built as a company. The HoQ is the **Head of Quality / QA function**:

- **SRE** (cc-fleet-health, running) — keeps infra healthy *after* ship.
- **Chief of Staff** (specced) — the operator's one door; routes work.
- **Head of Quality** (this spec) — the standing gate; nothing sub-bar ships. Where the CoS guarantees the operator has *one door in*, the HoQ guarantees the fleet has *one floor out* — no artifact reaches a client or prod below the ihsan bar.
- **Revenue / client-success** (next) — owns external relationships; **depends on** the HoQ, because "one relationship owner, invisible machinery" (the CoS's client-facing promise) only holds if what ships through that relationship is reliably excellent.

The deepest payoff mirrors the CoS's: **the HoQ is the fleet's internalization of "the factory is ihsan so the product is ihsan."** A client experiences the fleet as trustworthy precisely when the floor is machine-guaranteed, not attention-dependent. Build the internal quality gate well, and "we don't ship below the bar" becomes a structural fact a client can rely on, not a promise an agent might forget under context pressure.

### Governance + open questions (for operator / cai)

The HoQ **changes a ship rule**: today `ihsan-gate.md` says "the hub confirms before greenlighting"; the HoQ says "a lease-held gate token is *required* to merge/deploy." That is a doctrine change (who/what enforces the floor, and the fact that it's now blocking-in-code). Per `feedback_always_run_gates_through_cai`, it goes to cai. The precise ask: _"Ratify moving ihsan-gate enforcement from hub-attention ('hub confirms before greenlight') to a `quality_lease`-held pre-merge/pre-deploy gate that fail-closes merge/deploy on the deterministic floor + missing mandatory reviews, keeps design/perf verdicts advisory (267-H1), and is risk-tiered per the manifest. The bar's content is unchanged — this operationalizes the existing floor; it does not raise or lower it."_

1. **Blocking from day one, or advisory-then-blocking?** Should Gate #1 *block* merges immediately, or run advisory (report-only) for a confidence period against real traffic — mirroring the CoS "passive triage first" de-risking — before it's armed to block? (Recommend: advisory shadow-mode first on the deterministic items, arm blocking per-item as each proves low-false-positive; the *floor* items — CI, PII/secret leak, deployed==GitHub — block from day one since their false-positive rate is ~0.)
2. **Who owns the bar definition?** Every `ihsan_gate_manifest` change through cai, or only ship-rule/threshold changes (adding a class, changing what blocks)? (Recommend: content of the bar = cai-ratified versioned changes; the operator can raise the bar unilaterally, lowering/loosening a *blocking* item needs cai — asymmetric, matching the money-gate philosophy.)
3. **`quality_lease` relationship to `orch_lease`/`front_door_lease`.** Same holder (simpler) or independent (so a degraded hub can't dark the gate)? (Recommend: **independent** — the gate must survive a hub death exactly like quality must survive a busy hub; that independence is the whole point. It can *co-reside* with the SRE/console body but leases separately.)
4. **Advisory judgment → fork threshold.** What makes a J-verdict a genuine escalation-worthy fork vs a logged advisory? Needs a crisp line so the gate neither over-escalates (paging the operator on nitpicks — over-gating) nor under-escalates (letting a real sub-bar client artifact through as "just advisory").
5. **Emergency bypass.** Is there a break-glass for a P0 hotfix where the full gate would cost minutes the incident can't afford? (Recommend: a **loud, audited, cai/operator-authorized** bypass that still runs the ~0-false-positive floor — CI, leak scan, deployed==GitHub — and defers only the J-items, with a mandatory post-hoc gate run. Never a silent skip.)

---

## 8. RECOMMENDED design (decision)

> **The Head of Quality is a role wired as a `quality_lease`-held GATE plus a versioned CHARTER — not a new body.** The charter is `docs/ihsan-gate.md` codified as an `ihsan_gate_manifest` that maps each change class to a checkable set of gate items (deterministic + judgment). The gate is a stateless evaluator inserted at two seams — **pre-merge-to-main** and **pre-deploy-to-prod/client** — that resolves the change class, runs the deterministic floor (CI, synthetic role×flow matrix, scoping/security leak scan, i18n parity, deployed==GitHub, no test-pollution), auto-invokes the existing fresh reviewers (cc-reviewer / cc-uiux) for judgment items, and writes a `quality_gate_runs` verdict scoped to the diff SHA. **A green token is required to merge/deploy on client/prod paths — enforced in the merge/deploy wrapper, fail-closed, not by promise.** It blocks on objective floor failures + missing mandatory reviews; it keeps taste verdicts advisory (267-H1) and escalates only genuine forks. It **orchestrates** existing quality capacity (ihsan-gate doctrine, cc-reviewer, cc-uiux, the design pipeline, the QA-EDGE pattern) without duplicating it, splits cleanly from SRE (HoQ = pre-ship correctness, SRE = post-ship health), and scales by risk-based depth + parallel ephemeral reviewers + SHA-caching. The one doctrine change (hub-attention enforcement → lease-held blocking gate) goes to cai; the bar's *content* is unchanged.

### Phased rollout plan

| Phase | Ships | Reversible via | Gate |
|---|---|---|---|
| **0** | (exists) `ihsan-gate.md`, cc-reviewer/`spawn_reviewer.sh`, cc-uiux/`spawn_uiux_review.sh`, `review_dimensions`, the QA-EDGE pattern | — | — |
| **1 — CODIFY (zero enforcement).** Author `ihsan_gate_manifest` (the six-item floor + G1–G10 + risk classes as versioned data) + a **read-only gate evaluator** that scores a diff and writes an advisory `quality_gate_runs` row. **Changes nothing** — lanes/hub just *read* the score. Builds confidence against real ship traffic at zero risk. | stop reading the column | none (observation only) |
| **2 — FLOOR BLOCKS.** Arm the **~0-false-positive deterministic floor** (CI green, PII/secret leak, deployed==GitHub, no test-pollution on client store) to **fail-close merge/deploy** via the wrapper. These block from day one; nothing subjective yet. | `QUALITY_GATE=advisory\|enforce` flag flips back | **cai** (the enforcement doctrine change — §6 ask) |
| **3 — FULL GATE + AUTO-INVOKE.** Arm the remaining deterministic items (synthetic role×flow matrix, i18n parity, scoping/deny-by-default) + **auto-invoke cc-reviewer/cc-uiux** for J-items; risk-tiered by class; advisory verdicts surface, forks escalate. | per-item disarm to advisory | depends on Phase-1 confidence data + Phase-2 doctrine |
| **4 — FAILOVER + SCALE.** `quality_lease` + CAS-takeover (gate survives a body/host death) + SHA-cache + parallel ephemeral reviewers + gate self-latency metric (SRE-style). | lease hands back on recovery | cai (parallels `orch_lease` DR) |

**Ship Phase 1 first.** It is free, it de-risks every threshold against real ship traffic before anything blocks, and it changes nothing a lane can feel. Phase 2 delivers the first real guarantee (the objective floor can no longer be skipped) and is the only phase needing the cai doctrine ratification, so it's worth settling early. Phases 3–4 add judgment-routing and resilience on the proven base. **Every phase: provably safe + dead-man's-switch (fails loud, floor defaults to strict) + reversible via flag** — the target-topology autonomy standard.
