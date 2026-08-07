# Head of Revenue — Turning the Factory Into Income

_Design spec (not a build). Written 2026-07-22. A "hire" from `reports/fleet-target-topology-20260721.md`; format/rigor mirror `reports/chief-of-staff-one-front-door-spec.md` and `reports/head-of-quality-spec.md`._

_Status: **DRAFT FOR REVIEW**. This does not invent a new commercial mandate — the mandate already exists (`project_substrate_north_star_revenue_autonomy` war-room 2026-06-28; `feedback_revenue_first_payment_before_proceed` 2026-07-17). It proposes making that mandate a **standing, visible, self-maintaining revenue pipeline** — a durable ledger + a detect→act loop + on-demand analyst lanes — so revenue stops depending on the operator carrying every opportunity in his head. The one governance point (Head of Revenue proposes + closes; the money/residency gate stays cai's) is a boundary, not a doctrine change — §5, §6._

---

## 0. TL;DR (read this, then decide)

- **What:** The Head of Revenue (HoR) is a **role built on a durable pipeline ledger**, not a new always-on "sales agent." It is three parts: **{ a versioned `opportunities` pipeline substrate — the revenue ledger, source of truth for every live money thread }** + **{ a standing detect→act "revenue loop" that keeps the pipeline fresh, surfaces the next-best-action, and flags where the operator is the blocker }** + **{ on-demand analyst lanes spawned for the deep work — proposal drafting, pricing models, grant applications, competitive recon }**. In steady state the *relationship/commercial-judgment* half is carried by Nazim (the operator's CTO/commercial partner); the *state* lives in substrate, not in any body's context. We **reject** a standing always-on BD agent — it's a new single point, a context-bloat sink, and a sales bot drifts toward hype outreach, which violates no-takalluf.
- **Why this shape differs from the other two hires:** revenue is **more stateful** than quality (a stateless gate) or the front door (a stateless router). An opportunity has a *lifecycle measured in weeks* — lead → scoped → priced → proposed → won → paid → brief → delivered. So the HoR's substrate half (the ledger) is heavier and more central than the CoS's triage annotations or the HoQ's manifest. But the "role not a body" logic still holds, and *harder*: put the durable state in substrate so it survives resets/failover, and fan the work to ephemeral lanes so nothing accretes.
- **What it owns:** the whole pre-sale revenue lifecycle across the eight live threads — shipforge, storefront/merchants, Gazzabyte (reseller channel + irsyad), SushiTei (F&B, B2B2B), Alderei/COSEM AR-PWA, cosem/ADCDA + Ray-AI, and the UAE→SEA Shariah fintech rails (Hafiz) — plus the **grants** (IMDA/Tech Data: pursue vendor-of-record pre-approval + match to a willing SG SME partner) and the **partner-relationship ops** (Gazzabyte, Hafiz, Desmond). It ends at the **paid + scoped-brief handoff** to the builder lanes, and at the future **Finance/Treasurer** for actual billing/collection.
- **The seam:** a won-and-paid opportunity becomes a **scoped build brief on the goal queue** (topology Phase 3) — the HoR already scoped the work to *price* it, so the brief is a by-product of the proposal. The builder lanes pull it; the HoQ gates what ships back; Client-Success (the Ray-AI pattern) owns post-sale.
- **The money-first gate stays the operator's + cai's.** HoR **proposes** pricing and **closes** commercially, but it never authorizes money: every payment structure, residency call (Alderei UAE, GIRO audit data), and cross-border-fintech commitment routes to operator (GO) + cai (gate). Operator owns the relationships and the final money call; HoR is his BD-ops, not his signer.
- **The token-economics unlock (§7):** the fleet's Max subscriptions are **weekly token pools that reset — use-it-or-lose-it** (`feedback_weekly_token_pools_use_it`, 2026-07-22). The marginal cost of fleet build effort *during idle weekly capacity is ~zero*. HoR (demand-sensing) + SRE (supply-sensing) together turn perishable idle capacity into **speculative revenue work** — free prototypes for warm leads, grant applications, competitive recon — that a human agency could never afford. That is a structural GTM advantage, and naming it is part of this role's job.
- **First ship (zero risk):** the **pipeline ledger + a weekly pipeline digest** — codify the eight threads into an `opportunities` table (stage, value, next-action, owner, gate-status, first-dollar-blocker) and produce a read-only weekly snapshot. The operator just *reads* "here's the money pipeline, here's your next action, here's where you're the blocker." Directly useful given his revenue-first, currently-between-roles posture; changes nothing anyone can feel.
- **Top open questions:** (a) does HoR ever *send* client-facing commercial comms autonomously, or is outbound always operator-voiced? (b) who carries the commercial-judgment half long-term — Nazim, or a scoped BD lane, or the operator keeps it? (c) does the pipeline ledger get a `revenue_lease` like the other hires, or is it pure shared substrate with no singleton pen?

---

## 1. The problem (grounded in what exists today)

The fleet has a world-class **execution** engine and a real, operator-set **revenue mandate** — and almost nothing connecting them into a *system*. Four grounded facts:

**(1) The mandate is explicit and urgent; the machinery to run it is a human's head.** The operator set the substrate north star (war-room 2026-06-28): "REVENUE NOW — monetize shipforge + storefront ASAP; have each lane name its shortest path to first paying customer." He sharpened it 2026-07-17 (`feedback_revenue_first_payment_before_proceed`): **currently between roles, real financial pressure, wants the payment structure worked out BEFORE more build effort, lead with paid work.** That is a standing directive with no standing owner. Today "revenue" is tracked in the operator's memory + a scatter of memory files + Nazim's per-session attention. There is no ledger that answers, on demand, *"what is every live money thread, what stage is it at, what's the next action, and where am I the blocker?"* — the single most useful artifact for a revenue-first operator.

**(2) The pipeline is real, live, and multi-threaded — and drifting for lack of a tracker.** Eight concurrent revenue threads exist right now (§3.1). Several have **hard deadlines and money/audit stakes** — Gazzabyte's GIRO reconciliation is due end-Sept 2026; SushiTei has an *active-breach buying trigger*; shipforge has a *known revenue leak* (the watermark-bypassable clean render — no reason to pay). These are not hypotheticals; they are opportunities decaying in real time. A missed follow-up here is lost revenue, and nothing today guarantees the follow-up.

**(3) The proposal/pricing capacity exists, on-request and ad-hoc.** Nazim has drafted payment-structure options for Gazzabyte, an elevator pitch for an Indonesian rails partner, the Hafiz brief, the SushiTei roadmap/pitch (`share.wingmen.dev/r/sushitei`). The fleet can *produce* commercial artifacts fast. But like the reviewers before the HoQ, they fire **only when someone remembers to spawn them** — there is no standing function that says "this opportunity is warm, draft the proposal now" or "this lead is stale 10 days, nudge it." The capacity is real; the *systematic drive* is not.

**(4) Capacity is being left on the table — literally.** `feedback_weekly_token_pools_use_it` (2026-07-22): the Max subscriptions are weekly pools that reset; unused capacity is *lost*. The operator's "full tilt" is about converting a fixed weekly cost into maximum output before the reset. But "maximum output" is only revenue if it's pointed at *convertible* work. Right now no one is matching **idle weekly capacity → warm-lead conversion work**. The fleet can afford speculative build (free prototypes, grant apps) at ~zero marginal cost during idle windows — and doesn't, because no role owns the demand side of that equation.

**The pattern behind all four: the revenue mandate is real, the artifacts are producible, the capacity is (perishably) abundant — but there is no system that turns intent + capacity into tracked, driven, converted income.** The HoR closes that gap: it makes the pipeline a durable object, keeps it fresh with a loop, and points idle capacity at the highest-conversion work — without ever taking the money call away from the operator + cai.

### What already works and must be preserved

- **The revenue doctrine** — `project_substrate_north_star_revenue_autonomy` (revenue is *instrumental* — it buys independence, per the canonical Khizanah north star; the both-test is a real ship-gate), `feedback_revenue_first_payment_before_proceed`, `feedback_no_takalluf`. The HoR *operationalizes* these; it does not reset the mission.
- **The commercial-artifact capacity** — Nazim's proposal/pitch drafting, `share.wingmen.dev` (`publish_share.sh`) for password-gated client-facing plans/roadmaps, the design pipeline for pitch-grade assets. The HoR *drives and tracks* these; it does not re-implement them.
- **The money/residency gate** — cai's authority (`feedback_always_run_gates_through_cai`), `require_verified_authorization`, the residency doctrine (TENANT-RESIDENCY-001). The HoR **routes to** these unchanged; it never becomes a way around them.
- **The partner scoping model** — Desmond (shipforge/storefront, deploy authority), Hafiz (fintech judgment, NDA-gated), Gazzabyte (partner-candor). The HoR *tracks* these relationships; the operator + cai still *own* them.
- **The lease/loop patterns** — `orch_lease.py`, the CoS `front_door_lease`, the HoQ `quality_lease`, the auto context-reset detect→act loop (f77b434). The HoR's revenue-loop and (optional) `revenue_lease` copy these.

The HoR is therefore, like the CoS and HoQ, **mostly a re-wiring of existing pieces** — mandate → ledger, ad-hoc pitches → driven pipeline, hub/Nazim-attention → a standing loop, idle capacity → matched demand. That is why it ships incrementally and reversibly.

---

## 2. What it is (recommendation, with the alternatives judged)

**Three candidate forms were on the table:**

| Form | Verdict | Why |
|---|---|---|
| (a) A **new always-on "Head of Revenue / BD" agent** that owns outreach + pipeline | **Reject** | Adds a body = a new single point + a context-bloat sink (it would accrete every opportunity's full history — the 100%-context death that hit the hub). Worse, a standing *sales* agent structurally drifts toward volume outreach and hype — the exact **takalluf** the operator forbids (`feedback_no_takalluf`: build/sell for genuine need, not show). And it would put a bot in client-facing commercial commitments, where only the operator's voice belongs. Maximum cost, worst resilience, worst values-fit. |
| (b) A **durable pipeline ledger** (the CRM-as-data) that both agents and the operator read/write | **Adopt (state half)** | Revenue is stateful; the state must outlive any body's context and survive a reset/failover. A versioned `opportunities` substrate is the single source of truth for "what's the money pipeline." This is the heaviest half — heavier than the CoS's triage annotations or the HoQ's manifest — because a lifecycle-in-weeks needs real memory. |
| (c) A **standing detect→act revenue loop** + **on-demand analyst lanes**, with the commercial-judgment carried by Nazim | **Adopt (drive half)** | The loop keeps the pipeline fresh (stale-lead nudge, next-best-action, blocker-flagging) — the Phase-1 topology pattern applied to money. The deep work (proposal drafting, pricing, grant apps, recon) fans out to *ephemeral* lanes with their own context windows. Judgment (what to charge, when to close, what to promise) stays with Nazim/operator — never automated. |

**Recommendation: (b) + (c). The Head of Revenue is a ROLE = { a versioned `opportunities` pipeline ledger } + { a standing revenue detect→act loop } + { on-demand analyst lanes }, with the commercial-judgment half carried by Nazim and the money call reserved to the operator + cai.** It is a **role, not a body** — for the CoS/HoQ reason (cheap to fail over, cheap to keep from bloating) *plus* a values reason unique to this domain: a role that *drives a tracked pipeline* stays honest and need-driven, whereas a body that *does outreach* drifts toward takalluf. The ledger holds the memory; the lanes do the work; the loop does the nudging; the human owns the relationship and the money.

Precise definition:

- **The ledger (`opportunities`)** — a versioned substrate table: one row per live money thread, with `stage` (lead / scoped / priced / proposed / won / paid / delivered / parked / lost), `value` (est. + billing model), `owner` (who holds the relationship — usually the operator), `next_action` + `next_action_owner` + `due`, `first_dollar_blocker` (the shortest-path-to-first-payment obstacle), `gate_status` (money/residency/cai state), and `client_ref` (partner/channel). This *is* the eight threads turned into data — the answer to "what's the pipeline" becomes a query, not a memory-jog.
- **The revenue loop** — a scheduled detect→act watchdog (the topology Phase-1 pattern) that reads the ledger and acts: flags stale opportunities past an SLA, computes the next-best-action, surfaces where *the operator* is the blocker (so he can unblock himself), and produces the weekly pipeline digest. It escalates genuine forks (a live deadline slipping, a money decision needed) and self-heals the rest. It never sends client-facing comms.
- **The analyst lanes** — ephemeral, spawned on demand for a specific piece: draft a proposal, model a pricing option, write a grant application, run competitive recon (the Epicware-vs-us teardown pattern), build a speculative prototype for a warm lead. Each in its own tmux session / context window (`feedback_parallelize_by_default`). They produce artifacts (to `work_outputs` / `share.wingmen.dev`); they do not accrete.

Why a ledger-plus-loop and not a body, restated for this domain: a pipeline must be **persistent and visible** (a ledger the operator can read at a glance), **self-maintaining** (a loop that nudges), and **honest** (driven by tracked genuine opportunities, not manufactured outreach). A body is none of those — it's a queue that forgets on reset and drifts toward selling for its own sake.

---

## 3. The charter / what it owns — the revenue lifecycle, against the real threads

The HoR owns the **pre-sale revenue lifecycle** end to end, plus grants and partner-ops, ending at two handoffs (build brief → lanes; billing → Finance). Concretely, against what is live today:

### 3.1 The pipeline (the eight live threads, as ledger rows)

| Thread | Stage today | Billing model | First-dollar blocker / next action HoR drives |
|---|---|---|---|
| **shipforge** (flagship) | built ~80%, revenue-leaking | **outcome-billed** (final deliverable + hosting; per-site monthly management fee) `project_shipforge_fluid_clone_first_model` | The **auth/entitlement gate** (clean `/r/…html` render is publicly grabbable → no reason to pay) + Stripe wired to the *outcome-billed* model, not the dead 5-direction unlock. HoR tracks: close the leak → first content client (dogfood a Musa site) → first commerce client. |
| **storefront + merchants** (Hadi etc.) | product live on seeded store | recurring **SaaS + a cut of payments** (PayNow/Xendit) | Merchant-onboard TG identity-bridge; PayNow semi-auto verification. HoR tracks: first paying merchant + the payment-cut structure. |
| **Gazzabyte** (partner/reseller) | **live deliverables in hand**, nearest to price | agency/partner terms — **per-project / retainer / rev-share** on their madrasah contract | Draft + agree the **partner payment structure** (HoR's clearest near-term close). Plus the GIRO/Tabung-Fajr roadmap (money/audit, **hard end-Sept 2026** deadline → cai + operator gated). |
| **SushiTei** (via Gazzabyte, B2B2B) | roadmap/pitch drafted; active-breach trigger | full-suite: shipforge + storefront + branditqr + F&B modules | Convert the hack into the beachhead; **beat Epicware** (55/100 Lighthouse, 32s FCP vs our 91/1.3s). HoR tracks: Phase-0 secure-beachhead scope + price; this is the **forcing function for shipforge↔storefront wiring**. |
| **Alderei / COSEM AR-PWA** (via Nahar/ADCDA) | ~75-80% built (ihsanos invoicing module); white-label PWA specced | modular product — **Alderei = tenant #1, next client = config** | 5 small gaps + a white-label PWA. Residency **resolved → SG** (COSEM AR data). HoR tracks: synthetic prototype → real COSEM data (cai-ratify) → repeatable AR-tracking vertical. |
| **cosem / ADCDA + Ray-AI** | Ray-AI CA proposal delivered; course-video backlog | productized admin agent (per-project bot); course-video deliverable (due 2026-07-31) | HoR tracks: is Ray-AI a *sold* product (COSEM/ADCDA pays for the CA agent) or internal? + the ADCDA "beyond current functionality" bigger-play the operator invited. |
| **UAE→SEA Shariah fintech rails** (Hafiz) | north-star; building blocks; Hafiz brief sent | capital-light **SaaS/ERP riding licensed rails** (Thunes/Nium); fees = **ujrah not riba** | Longest horizon, biggest prize. HoR tracks the GTM *sequence* (free PayNow-QRIS now → build the ERP moat → approach Thunes with real volume). Commercial terms with Hafiz = operator + cai. |
| **Desmond Shen** (scoped partner) | active on shipforge/storefront | (partner, not client) — deploy authority on shipforge/storefront | Not a sale — a *channel/partner*. HoR tracks his workstreams' revenue output (shipforge/storefront clients he brings/ships). |

The ledger makes this table a **live query**, not a snapshot — the operator sees it fresh every week (§4.4), and the loop keeps `next_action`/`due` honest.

### 3.2 Proposals + pricing

HoR owns producing **proposal + pricing artifacts** (via analyst lanes → `work_outputs` / `share.wingmen.dev`), to the operator's models: **outcome-billed** for shipforge (bill the chosen deliverable + hosting, refinements-within-reason, keep the *experience* unlimited but *billing* bounded so fluid iteration doesn't bleed compute); **recurring SaaS + payment-cut** for storefront; **agency/retainer/rev-share** for Gazzabyte; **modular per-tenant** for the AR product. Pricing *options + a recommendation* the operator can act on (`feedback_revenue_first_payment_before_proceed` — "bring concrete payment-structure proposals, not 'let's talk'"). **HoR proposes; the operator decides the number; cai gates any money-structure that's irreversible or cross-border.**

### 3.3 The grants (IMDA / Tech Data)

A distinct HoR workstream, because it lowers the *client's* price barrier and unlocks the SG SME market at scale: pursue getting a wingmen product (storefront and/or shipforge) **pre-approved as a vendor-of-record solution** under a SG digitalisation grant (IMDA's SMEs-Go-Digital / Productivity-Solutions-Grant family), distributed via a channel like **Tech Data**. This needs, per the research, **(a) a registered vendor entity** eligible to be listed, and **(b) a willing SG SME partner** to co-apply and prove the solution. HoR owns: mapping the grant requirements, identifying the vendor-entity gap (a governance/operator + cai item — company registration is an operator decision), and **matching a willing partner from the existing book** (Gazzabyte's madrasah/F&B clients, Hadi, a SushiTei outlet) to a first co-application. The payoff: subsidised clients convert faster, and pre-approval is a durable channel, not a one-off sale.

### 3.4 Partner-relationship ops

HoR **tracks and preps** the partner relationships; the operator + cai **own** them. Gazzabyte (reseller channel → B2B2B multiplier; partner-candor posture), Hafiz (fintech judgment/regulatory-navigator; NDA-gated — hold client specifics until under NDA), Desmond (shipforge/storefront build + deploy authority). "Track and prep" = keep the relationship's opportunities + next-actions in the ledger, draft the briefs/pitches, flag when a partner is the blocker or is being under-served — **never** commit terms, equity, or money (that's operator + cai, always).

### 3.5 The two handoffs (where HoR's ownership ends)

- **→ Delivery (builder lanes):** a won-and-paid opportunity becomes a **scoped build brief** (§4.1). HoR wrote the scope to price it, so the brief is a by-product. HoR's ownership of *building* ends here; it *tracks* delivery status back into the ledger (for the client-facing "where's my thing") but does not run the build (that's the lanes + HoQ).
- **→ Finance/Treasurer (future):** actual invoicing, collection, reconciliation, and the "revenue that collects on autopilot" the north star wants. HoR closes the deal and hands the *billing* to Finance. Until that hire exists, HoR flags the billing action to the operator; it does **not** move money itself.

---

## 4. How it plugs into the fleet (revenue → delivery, without a bottleneck)

The HoR sits *upstream* of the builder lanes and *alongside* the operator, feeding the goal queue and reading the ledger. The flow:

```
opportunity enters (operator, partner, inbound, or HoR-sourced)
        │
        ▼
  [ opportunities ledger ]  ── revenue loop keeps fresh (stale-nudge, next-action, blocker-flag)
        │
   scope + price (analyst lane) ──► proposal artifact (share.wingmen.dev)
        │
   operator decides ──► MONEY-FIRST GATE (operator GO + cai gate) ──► WON + PAID
        │
        ▼
  scoped build brief ──► GOAL QUEUE (topology Phase 3) ──► builder lanes pull
        │                                                        │
        │                                                   HoQ gates ship
        ▼                                                        ▼
  ledger tracks delivery ◄──────────────────────────── delivered ──► Client-Success (Ray pattern)
```

### 4.1 Where an opportunity enters, and how it becomes a build brief (the revenue → delivery seam)

An opportunity enters four ways: the **operator** names one, a **partner** brings one (Gazzabyte/Desmond channel), an **inbound** arrives (via the CoS front door — the CoS *routes* a commercial signal into the ledger), or **HoR sources** one (competitive recon surfaces a beatable incumbent like Epicware). It lands as a ledger row.

The **seam** to delivery is the key design point: because HoR **scoped the work to price it** (you can't quote what you haven't scoped), the winning proposal *already contains* the build scope. On won-and-paid, HoR emits a **scoped build brief onto the goal queue** — the same durable queue the topology's Phase 3 defines for autonomous pickup. The builder lanes self-assign it; the HoQ gates what ships back; the ledger tracks delivery status. **No hand-dispatch:** HoR produces a *brief* (direction), not a *dispatch* (a poke at a specific lane) — consistent with the topology's "direction, not dispatch."

### 4.2 The money-first gate (payment before proceed) — operator + cai, never HoR

This is the load-bearing boundary. `feedback_revenue_first_payment_before_proceed`: the payment structure is worked out **before** more build effort. HoR *enforces the discipline* (the ledger's `stage` cannot advance to a build brief until `paid` or a paid-structure is agreed) but **does not make the money call**. Concretely:

- HoR produces the payment-structure proposal (options + recommendation).
- The **operator** says GO on the number/structure (his call, his relationship, his accountability — `feedback_no_fabricated_operator_confirmation`: HoR never claims "operator confirmed" without a real bridge artifact).
- **cai** gates anything money/irreversible/residency/cross-border (`feedback_always_run_gates_through_cai`): the Gazzabyte GIRO audit-data path, Alderei's residency, any fintech-rails money movement, any Stripe/payment-cut structure. Operator GO ≠ substitute for cai's gate; the two are independent and both required on a money path.
- The `gate_status` field on the ledger row makes this *visible and unskippable* — a row on a money path with an open cai gate cannot emit a build brief. This is the HoQ's "no green token, no merge" pattern applied to money: **no cleared gate, no build spend.**

**The anti-over-gating counterweight (explicit):** the operator's own `feedback_loosen_nonmoney_gates_keep_audit` warns *over*-gating is a failure mode too — "don't gate-wall clients." So the gate bites on **money/irreversible/residency** only; a non-money next-step (a synthetic prototype, a pitch, a free demo — zero-cost, reversible) proceeds and is merely *logged*. The revenue role's job is to **reduce friction to first-dollar** (clear the shortest-path blocker, per the north-star directive), not to add commercial ceremony. Over-caution that slows a warm lead is as much a failure as under-gating a money commitment.

### 4.3 Closed loop, self-healing (not a human chasing a spreadsheet)

The revenue loop is a **closed detect→act loop** (topology Phase 1): it doesn't page the operator "please check the pipeline" — it *reads* the ledger, *nudges* stale opportunities, *computes* next-best-actions, and *escalates only genuine forks* (a deadline slipping, a money decision due, a warm lead going cold). Everything it can resolve (refresh a stage from `work_outputs`, re-compute a due date, draft the follow-up for the operator to send) it resolves; only real judgment reaches the human. Every action writes an auditable trail so a rebooted/failed-over loop reconstitutes pipeline state from substrate — the ledger is the durable memory, exactly as `operator_messages` is for the CoS.

### 4.4 Reporting to the operator without becoming a bottleneck

HoR reports via a **weekly pipeline digest** (and on-demand) — pushed through the CoS front door / `nazim_send.sh` — that answers the three revenue-first questions in one glance: **(1) the pipeline** (each thread, stage, value, movement since last week), **(2) your next actions** (what the operator specifically must do), **(3) where you're the blocker** (a decision only he can make, sitting idle — the `feedback_nudge_operator_builder_adhd` "track his plate, nudge when he's the blocker" pattern applied to money). It is a *report + a nudge*, not a request for dispatch — the operator reads, decides the forks, and the loop drives the rest. HoR is a pipeline *producer*, not a station the operator queues at.

---

## 5. The ihsan constraint on revenue — revenue WITH ihsan, not despite it

Revenue is **instrumental**, not the goal. The canonical Khizanah north star (constitutional): *products that help humanity while being profitable enough that you never rely on those who don't want to see you succeed* — profit exists to buy the **right to refuse** money, not to accumulate. The HoR must be built so that pursuing income *is* pursuing the mission, never in tension with it. Four hard constraints, encoded into the role:

1. **The both-test is a ship-gate on revenue too.** Every opportunity the HoR advances must pass BOTH: (1) does it help more people? (2) is it aligned with Quranic principles? A deal that fails either does not enter the pipeline as `won` — it's `parked` or `declined`, on the record. This is the `project_substrate_north_star` both-test applied at the *sales* seam, not just the product seam.
2. **Shariah-compliant by construction on the fintech rails (ujrah, not riba).** For the UAE→SEA rails, the commercial model is **fee-for-service (ujrah), never interest (riba)**; FX is **spot, not speculative**; merchants are ethically screened; a Shariah advisor/board governs. HoR tracks the fintech pipeline *only* within these rails — a pricing model that smuggled in interest would be a finding, not a clever margin. The halal structure is the **moat**, not a constraint to route around.
3. **No-takalluf in proposals and outreach.** HoR sells *genuine need*, not hype. No manufactured urgency, no feature-inflation to pad a quote, no volume outreach for its own sake. This is *why* HoR is a tracked-pipeline role and not an outreach bot — the shape enforces the value. Proposals are **honest**: real status, real capability, the supported-~80% named plainly (never WooCommerce-parity theatre), abstracted client detail held until NDA (the Hafiz-brief discipline). Underpromise, overdeliver (`feedback_underpromise_overdeliver_pings`).
4. **Honesty in pricing and in the pipeline itself.** The ledger tells the operator the truth — a stalled thread is `stalled`, a cold lead is cold, a leak is a leak. No optimistic pipeline inflation (the analog of the HoQ's "no fake-autopilot"). A revenue role that flatters its own numbers is worse than none.

And the governance line, restated: **HoR proposes + closes; cai gates the money/irreversible; the operator owns the relationship + the final call.** Revenue authority is *proposal* authority, never *money* authority. This is the same asymmetry as the money-gate philosophy — the fleet can generate and recommend commercial moves at full tilt, but the irreversible money/residency step always clears the human + cai gate.

---

## 6. Boundaries vs existing (no duplication, clear lines)

The HoR **orchestrates** the commercial lifecycle; it does not absorb delivery, governance, or the relationship. Boundaries:

| Existing thing | What it does | HoR boundary |
|---|---|---|
| **Client-Success (Ray-AI pattern)** — `project_ai_course_administrator_ray` | Post-sale: onboarding, ongoing ops, retention, the productized per-client admin agent | **Clean split at the paid+delivered line: HoR owns PRE-sale (lead → proposal → close → paid → brief); Client-Success owns POST-sale (run it, keep them, grow them).** They meet at handoff — Client-Success feeds *expansion signals* (upsell, renewal, a new need) back into the HoR ledger as new opportunities. Mirrors the HoQ/SRE "before vs after the ship" split. |
| **cai** | Governance authority; money/residency/irreversible gate; ratifies doctrine | HoR **routes to** cai for every money-structure, residency call, and cross-border commitment (unchanged gates). HoR **proposes + closes commercially**; cai **gates the money**. HoR never overrides or routes around a cai gate; `gate_status` on the ledger makes the gate state visible and blocking. |
| **Builder lanes** | Delivery — build the thing | HoR hands a **scoped build brief** to the goal queue; lanes pull + build; HoQ gates. HoR does **not** dispatch lanes or run builds — it produces direction (the brief), the lanes self-assign. HoR *reads* delivery status back into the ledger. |
| **The operator** | Owns the relationships; final money call; accountable signer; the human in every client/partner room | HoR is his **BD-ops / commercial chief-of-staff** — it tracks pipeline, drafts proposals, preps briefs, nudges his plate, surfaces where he's the blocker. It **never** commits terms/money on his behalf, never fabricates his confirmation, and (open Q §8) does not send client-facing commercial comms in his voice. The relationship and the close stay *his*. |
| **Chief of Staff (front door)** | The operator's one door; routes inbound to the right executor | HoR is one of the CoS's **executors/producers** — a commercial inbound routes *into* the HoR ledger; HoR's digest goes *out* through the CoS door. The CoS handles the *conversation*; HoR handles the *pipeline*. No overlap: routing vs revenue-state. |
| **Head of Quality** | The pre-ship gate; nothing sub-bar ships | HoR **depends on** the HoQ — "one relationship owner, invisible machinery, reliably excellent output" only holds if what ships through the deal clears the ihsan bar. HoR sells; HoQ guarantees the thing sold is good. HoR's proposals can *promise* the quality bar precisely because the HoQ makes it structural. |
| **SRE (cc-fleet-health)** | Keeps infra healthy; tracks weekly capacity headroom per subscription | HoR is the **demand** side to SRE's **supply** side (§7). SRE senses idle weekly capacity; HoR senses convertible pipeline; together they match perishable capacity to revenue work. No overlap: capacity supply vs revenue demand — they meet at a matcher. |

The one-line rule: **the operator + cai own the relationship and the money; the builder lanes + HoQ own the delivery and the quality; the HoR owns the *pipeline between them* — tracking, proposing, closing, and handing off — and never crosses into the money call or the build.**

---

## 7. Scaling + the token-economics angle (the perishable-capacity unlock)

As the pipeline and the fleet both grow, the HoR must scale *and* it must exploit the fleet's most unusual asset. Both come from the same insight.

**The token-economics reframe (`feedback_weekly_token_pools_use_it`, 2026-07-22).** The fleet runs **several Max subscriptions — this Mini (Nazim), the Studio bodies (hub/cai), each lane — each a weekly token pool that resets. Unused capacity is lost.** The operator's "full tilt" is *converting a fixed weekly cost into maximum output before the reset*. The strategic consequence the HoR must internalize: **the marginal cost of fleet build effort during idle weekly capacity is ~zero.** A human agency pays salaries whether or not it works; the fleet's weekly pool is already paid-for and *evaporates unused*. That inverts the economics of speculative work.

**The HoR + SRE matcher (the load-bearing scaling lever).** SRE senses **supply** (weekly headroom per subscription — the natural SRE loop `feedback_weekly_token_pools_use_it` already names: "track weekly headroom per subscription → route/scale work to fill it"). HoR senses **demand** (warm leads, beatable incumbents, grant-eligible prospects — convertible pipeline). A thin **capacity↔pipeline matcher** closes the loop: when a subscription has idle weekly headroom, point it at the **highest-conversion speculative revenue work** — a free bespoke prototype for a warm lead (SushiTei off Epicware; a shipforge clone of a prospect's actual site), a grant application, a competitive-recon teardown, a pitch-grade `share.wingmen.dev` asset. Each is *~zero marginal cost during idle capacity* and *directly conversion-driving*. This is a **structural GTM advantage no human agency has**: the fleet can afford to invest real build effort into prospects speculatively, because the alternative is the capacity vanishing unused.

Four scaling levers, all borrowed from patterns the fleet trusts:

1. **The ledger is the scale unit, not a body.** N opportunities = N rows, not N context windows. The pipeline scales with rows; nothing accretes. The loop reads rows; the lanes are ephemeral. There is no queue that serializes.
2. **Parallel, ephemeral analyst lanes.** Proposal drafts, pricing models, grant apps, recon — each a fresh lane in its own window (`feedback_parallelize_by_default`, sharpened by the use-it-or-lose-it WHY). M opportunities needing artifacts = M lanes in parallel, filling the weekly pools. **But on the *right* things** — the `feedback_weekly_token_pools_use_it` balance: parallelize aggressively on verified, non-duplicated, convertible work; rework/thrash/speculative-on-a-dead-lead burns the pool for no value. HoR's job is to point the perishable capacity at *conversion*, not just at *activity*.
3. **Deterministic-first reporting.** The weekly digest, stale-lead detection, blocker-flagging, next-action due-dates — all mechanical over the ledger, no LLM. Judgment (what to charge, whether to close, is-this-lead-real) is where the model spends, and only there. Spend judgment where it converts, nowhere else.
4. **The model-flip lever + grant subsidy as margin tools.** Mechanical analyst work (recon scraping, proposal formatting) can run on Sonnet (`project_fleet_model_flip_lever` — separate weekly Max limits), preserving Opus headroom for judgment-heavy closes. And the grants (§3.3) scale *client-side* economics — a subsidised client converts at a lower effective price, widening the market the fleet can profitably serve.

The scaling failure mode to watch: **speculative capacity spent on cold leads** — burning the pool on prototypes for prospects who will never convert (activity mistaken for conversion). Mitigation: the matcher points idle capacity at *warm, qualified, both-test-passing* pipeline only; a speculative build is itself a tracked opportunity with a conversion hypothesis, not a vanity demo (no-takalluf again). The perishability makes speculation *cheap*, not *free-of-judgment*.

---

## 8. Its place in the org / topology + open questions

**Topology (`fleet-target-topology-20260721.md`):** the HoR is a **Phase-1 closed loop (the revenue loop) feeding the Phase-3 goal queue.** The revenue loop is the detect→act pattern applied to money — it detects (stale/decaying pipeline) and acts (nudge, next-action, escalate forks), keeping a human out of the *tracking* critical path. And it is a primary **producer of goals** for Phase 3: a won-and-paid opportunity *is* a goal on the queue that the builder lanes self-assign — "direction, not dispatch" flows through the HoR at the revenue seam. It also makes the "survives the operator's absence" bar real for income: revenue that is *tracked, driven, and handed off by the fleet* (not carried in the operator's head) is revenue that keeps moving while he's away — the mandate's whole point.

**Company org (the "hires"):** the fleet is being built as a company. The HoR is the **Head of Revenue / Growth** — the function that turns the factory's output into income:

- **SRE** (cc-fleet-health, running) — keeps infra healthy + senses weekly capacity headroom (the supply side of §7).
- **Chief of Staff** (specced) — the operator's one door; routes inbound (including commercial signals into the HoR ledger).
- **Head of Quality** (specced) — the pre-ship gate; guarantees what the HoR sells is reliably excellent.
- **Head of Revenue** (this spec) — the pipeline; turns build capacity into income; hands paid work to the lanes and the deal to Finance.
- **Client-Success** (Ray-AI pattern, emerging) — post-sale ops; feeds expansion signals back to the HoR.
- **Finance / Treasurer** (future) — billing, collection, "revenue that collects on autopilot."

The deepest payoff mirrors the CoS's and HoQ's: **the HoR is the fleet's internalization of "revenue is instrumental — it buys the right to refuse."** A fleet that tracks and converts its own pipeline with ihsan — honest proposals, ujrah-not-riba, the both-test at the sales seam, speculative capacity spent on genuine need — earns the independence the north star exists for, without the operator holding the whole pipeline in his head. Build the revenue engine well, and "profitable enough to never rely on those who don't want us to succeed" becomes a structural fact of the substrate, not a hope.

### Open questions (for operator / cai)

1. **Outbound voice.** Does the HoR ever *send* client-facing commercial comms autonomously, or is all outbound operator-voiced (HoR drafts, operator/Nazim sends)? (Recommend: **operator/Nazim-voiced always** for external commercial commitments — the relationship and the voice stay human; HoR drafts + tracks. Revisit only for low-stakes internal-partner status relays.)
2. **Who carries the commercial-judgment half?** Nazim (already the CTO/commercial partner), a dedicated scoped BD lane, or the operator keeps it and HoR is pure ledger+loop? (Recommend: **Nazim carries judgment in steady state; HoR = ledger + loop + analyst lanes**; a dedicated BD lane only if pipeline volume outgrows Nazim's attention — the same "role not body until volume forces it" discipline.)
3. **`revenue_lease` — yes or no?** Does the pipeline get a singleton pen/lease like `orch_lease`/`front_door_lease`/`quality_lease`, or is it pure shared substrate with no singleton action? (Recommend: **mostly shared substrate — the ledger has no singleton pen** since reading/proposing isn't a singleton act; a lease is only needed if HoR ever *sends* client-facing comms — see Q1 — in which case that specific pen leases like the others. Start lease-free.)
4. **The vendor entity for grants.** Getting a wingmen product IMDA/Tech-Data pre-approved needs a registered vendor entity — is there one, or is company registration itself an operator decision to sequence? (This is an operator + cai governance item; HoR surfaces the requirement, does not resolve it.)
5. **First-dollar priority order.** Given the revenue-first + between-roles pressure, which thread does the HoR drive *first*? (Recommend the ledger sort by *nearest-to-first-dollar*: **Gazzabyte payment-structure** (live deliverables, nearest close) and **shipforge leak-close** (flagship, one blocker from billable) lead; SushiTei/Alderei follow (built, need scoping+close); fintech rails is the long game. Operator's call — the digest surfaces it, he sequences.)
6. **Speculative-capacity spend authority.** Can the HoR+SRE matcher spend idle weekly capacity on a speculative prototype for a warm lead *without* a per-instance operator GO (since marginal cost ~0 and it's reversible), or does each speculative build need a nod? (Recommend: **yes, autonomous within a qualified-pipeline + both-test guardrail** — it's zero-cost, reversible, non-money, and the whole point of the perishable-capacity unlock; logged, not gated. Money/client-commitment still gates.)

---

## 9. RECOMMENDED design (decision)

> **The Head of Revenue is a ROLE built on a durable pipeline ledger — not a new always-on body.** It is `{ a versioned` opportunities `substrate — the revenue ledger, source of truth for the eight live money threads }` + `{ a standing detect→act revenue loop that keeps the pipeline fresh, computes next-best-actions, flags where the operator is the blocker, and produces a weekly pipeline digest }` + `{ on-demand ephemeral analyst lanes for the deep work — proposals, pricing, grant applications, competitive recon, speculative warm-lead prototypes }`, with the commercial-judgment half carried by Nazim and the money call reserved to the operator + cai. It owns the **pre-sale lifecycle** (lead → scoped → priced → proposed → won → paid) across shipforge, storefront, Gazzabyte, SushiTei, Alderei/COSEM, cosem/ADCDA + Ray-AI, and the UAE→SEA Shariah rails, plus the **grants** (IMDA/Tech-Data vendor-of-record pursuit) and **partner-relationship ops** (Gazzabyte, Hafiz, Desmond). It ends at two handoffs: a **scoped build brief onto the goal queue** (the builder lanes pull; HoQ gates) and, later, **billing to Finance**. The **money-first gate stays the operator's + cai's** — HoR proposes and closes commercially, but `gate_status` on every money/residency/irreversible row is visible and blocking, and no cleared gate means no build spend. Revenue is pursued **with ihsan** — the both-test at the sales seam, ujrah-not-riba on the rails, no-takalluf in proposals, honesty in the pipeline. It scales by a **ledger-as-scale-unit + parallel ephemeral lanes**, and it exploits the fleet's unique asset — **perishable weekly Max capacity, ~zero marginal cost when idle** — via a **HoR (demand) + SRE (supply) matcher** that converts idle pools into speculative revenue work no human agency could afford. It splits cleanly from Client-Success (post-sale), cai (the money gate), the builder lanes (delivery), and the operator (who owns the relationships + the final call).**

### Phased rollout plan

| Phase | Ships | Reversible via | Gate |
|---|---|---|---|
| **0** | (exists) revenue doctrine (north star, revenue-first, no-takalluf), Nazim's proposal/pitch capacity, `share.wingmen.dev` (`publish_share.sh`), the design pipeline, the cai money-gate, the lease/loop patterns | — | — |
| **1 — LEDGER + DIGEST (zero risk).** Codify the eight threads into an `opportunities` ledger (stage, value, owner, next-action, first-dollar-blocker, gate-status) + a **read-only weekly pipeline digest** through the CoS/`nazim_send` door. **Changes nothing** — the operator just *reads* his pipeline, next actions, and where he's the blocker. Directly useful given the revenue-first posture; de-risks the model against real threads. | stop reading the digest | none (observation only) |
| **2 — REVENUE LOOP.** Arm the detect→act loop: stale-opportunity nudge, next-action computation, blocker-flagging, digest auto-generation, fork-escalation. Still no outbound client comms; still no autonomous money. | `REVENUE_LOOP=advisory\|off` flag | none (internal nudging only) |
| **3 — PROPOSAL/RECON LANES + THE SEAM.** Auto-spawn analyst lanes for proposals/pricing/grant-apps/recon on demand; wire the **won-and-paid → scoped-build-brief → goal-queue** seam (with the `gate_status` money-first block enforced). Proposals are drafted; the operator/Nazim still send + close. | drop to Phase-2 (loop + manual artifacts) | **operator + cai** confirm the gate-block + brief-emit contract; depends on Phase-3 goal queue |
| **4 — CAPACITY↔PIPELINE MATCHER + (optional) FAILOVER.** Wire the HoR(demand)+SRE(supply) matcher to point idle weekly capacity at qualified speculative revenue work (autonomous within the both-test guardrail, logged); add a `revenue_lease` **only if** HoR ever sends client-facing comms (Q1/Q3). | disable the matcher; lease hands back on recovery | cai (speculative-spend guardrail + any client-facing-comms pen) |

**Ship Phase 1 first.** It is free, it turns the operator's in-his-head pipeline into a durable, glanceable ledger the moment he most needs it (revenue-first, between roles), and it changes nothing anyone can feel. Phase 2 makes the pipeline self-maintaining; Phase 3 wires the revenue→delivery seam and the money-first gate into code; Phase 4 unlocks the perishable-capacity advantage. **Every phase: provably safe + a dead-man's-switch (fails loud; the money gate defaults to blocking) + reversible via flag** — the target-topology autonomy standard. And every phase keeps the invariant: **HoR proposes + closes; the operator + cai own the money; revenue serves the mission, never the reverse.**
