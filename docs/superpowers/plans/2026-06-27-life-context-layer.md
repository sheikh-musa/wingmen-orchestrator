# Life-Context Layer ("Jarvis" lever)

**Status:** spec / not-yet-built. **Owner:** cc-orchestrator (memory + orchestration substrate is cc-orch domain).
**Origin:** operator 2026-06-27 — "how can i give you more context to my life so you can better orchestrate? how do i turn you into jarvis". cc-orch recommended starting here (biggest orchestration upgrade, low risk); operator: "proceed".
**Governance:** substrate schema via cai review + direct-psycopg dry-run→apply (decision-962). PII = the operator's personal life data → encrypted at rest, access-controlled, never leaked across slices. Build-for-the-need / no-takalluf: ship P1+P2, defer P3/P4 until a real need.

## The shift
Today the orchestrator remembers **facts** in a flat memory store (MEMORY.md + memory/*.md). The leap to "Jarvis" is a richer **life graph** + a few integrations so cc-orch is aware of the operator's *week and world*, not just his messages — flipping it from **reactive** (he asks) to **proactive** (it preps + surfaces "here's what needs you today" ahead of time), strictly within the rails (real actions only; the no-fake-autopilot rule holds).

## Decisions (operator 2026-06-28)
- **Proactivity = b+c**: a daily morning **plate** AND **active nudges** through the day (blocked-on-you / commitment-near). Real signals only — no fake-autopilot.
- **Calendar = read + WRITE**. Writes start **propose-then-confirm** (I draft the event, operator taps ok) until trust is earned, then loosen to auto-add for routine items. (Read gives week-awareness; write lets me add commitments when needed.)
- **Privacy posture** (operator asked, this is the data-governance line): only data LOADED INTO A PROMPT reaches Anthropic — bulk Supabase data stays at rest (SG/Sydney), never transmitted unless queried into context. Anthropic **commercial/API/Max terms = NOT trained on** (vs consumer claude.ai); processed-to-answer ≠ trained-on. So the control we own = **minimise PII-into-context**: load slices not dumps (life-graph selective-load helps), de-identify where possible, **sensitivity-tag** entities so the most sensitive load only when truly needed. Evaluate enabling ZDR for the plan. See [[reference_anthropic_data_usage_minimize_pii_in_context]].

## Data model (decided w/ operator 2026-06-28)
**Supabase/Postgres — additive, NOT a new system.** A *property graph modelled in Postgres*:
- `entities` — typed nodes (person / project / org / commitment), with attributes + a sensitivity tag.
- `relationships` (edges) — typed links between entities (person —is-on→ project, commitment —belongs-to→ project, channel —maps-to→ slice).
- `commitments` — dated obligations (presentations, deadlines, collections) linked to projects/people.
- Reuse existing substrate as the backing detail: `repo_context`, `strategic_decisions`, the channels-CRUD slice registry, the memory/*.md files — the graph is the **connective layer over** these, not a replacement.
- Access control: **RLS, sensitivity-tagged** — personal entries never leak into a partner/vendor slice (channel-discipline + scope-gating rules apply).
- **pgvector** (native to Supabase) for semantic recall — fuzzy "what do I know about X" over the graph + memories. This is the main net-new capability.
- Traversal/queries in SQL (recursive CTEs / joins) — fine at our scale (dozens–hundreds of entities). **No graph DB (Neo4j etc.)** = takalluf for a problem we don't have; one substrate keeps RLS + the cai-gated dry-run→apply discipline.

## Components

### P1 — Life graph + per-project context packs (low risk, high value — DO FIRST)
A structured operator-context store (substrate, cai-gated schema):
- **people** — Zahidah (fiancée), the two step-sons (~8/10), Shen (partner), Gazzabyte (vendor), org contacts; relationship, scope, channel, sensitivity.
- **projects** — each vertical/client/slice: goal, current state, constraints, the people on it, the channel. (Overlaps the channels-CRUD slice registry — share the table where sensible.)
- **goals / commitments** — durable goals + dated commitments (presentations, launches, deadlines).
- **preferences / constraints** — operating rules already captured as feedback memories, surfaced structurally.
- **per-project context pack**: a loadable bundle (goals + people + constraints + live state) so every lane AND cc-orch load *that project's* context on demand — the cure for lane context-bloat + drift. Ties to channels-CRUD.

### P2 — Proactive daily orchestration (reuses the autonomous loop)
- A morning **"plate"** brief: what needs the operator today, prepped + prioritized (the organized-plate format, generated proactively not on-request).
- Context-aware nudges (a commitment is tomorrow + X is blocked on him). Real, actionable only — no timer-driven theatre.

### P3 — Integrations (defer until needed)
- **Calendar** (read-only first): his week/commitments → I'm aware of what's coming.
- **Email** (optional, scoped): surface what needs action. Heavier privacy surface — only on explicit need + cai review.

### P4 — Voice (defer)
- Voice notes both ways (Telegram/Discord) — lowest priority; nice-to-have.

## cai §6.6 GATE — CAI-RESP-336 (approved-in-direction; 5 conditions before DDL grant)
cai grants the §6.6 apply once the pinned DDL implements these. **Build the ISOLATION + its PROOF-TEST FIRST; the plate/nudges ride on top only once isolation is proven.**
1. **Cross-slice isolation = DENY-BY-DEFAULT** at query AND RLS (not tag-by-convention) + a **proof-test**: a partner/vendor/agent slice CANNOT retrieve a personal entity.
2. **Embeddings are derived PII** — pgvector embeddings leak via similarity → must inherit the same isolation + **encrypt-at-rest** + **similarity search scope-filtered** (never returns cross-slice neighbours).
3. **Calendar-WRITE** = propose-then-confirm on EVERY write, audited + reversible + his-calendar-only; the confirm gate **cannot be bypassed by a proactivity nudge**.
4. **Amanah bar > PDPA** (it's his own data, but stricter): minimise-into-context, not-trained-on, never shipped to an untrusted external provider, operator-controlled retention+purge, encrypt sensitive entities at rest.
5. **Self-imposed periodic ISOLATION-INTEGRITY AUDIT** (because he declined external verification) — fold into the fleet self-audit.

BUILD ORDER (revised per the gate): isolation foundation + proof-test → pinned DDL → cai §6.6 grant → apply (synthetic dry-run first) → THEN entities/edges/commitments + the daily plate + nudges. (Careful security-critical substrate build — operator's private data; not a rushed job.)
NOTE: the DPA/PDPA-pack for nasi-mandi was handled cai-direct via the new cai-bridge (cai now reaches Musa directly) — that coordination no longer routes through cc-orch.

## Build order
1. **P1 schema** — design the life_graph / operator_context tables → cai §6.6 dry-run review → direct-psycopg apply. (NOT a midnight cowboy table.)
2. **P1 context packs** — wire load-on-demand into cc-orch + lane boot (extends the boot_briefing pattern).
3. **P2 daily plate** — generate proactively in the autonomous loop; deliver via the bridge.
4. P3/P4 — only when the need is real.

## Guardrails
- Encrypted-at-rest + access-controlled (his life data, sensitivity-tagged); no cross-slice leakage (a partner/vendor slice never sees personal life-graph entries — the channel-discipline + scope-gating rules apply).
- cai reviews the schema + any integration (privacy surface).
- Build for the genuine need; start small (P1 store + P2 plate), prove value, then deepen. Relates to [[channels-CRUD]] (shared project/people registry) and the substrate-as-product vision.
