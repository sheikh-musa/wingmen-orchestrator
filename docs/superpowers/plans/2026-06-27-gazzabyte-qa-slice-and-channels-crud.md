# Gazzabyte QA Slice + (deferred) Channels-CRUD

**Status:** spec / not-yet-built. **Owner:** cc-orchestrator (bridge + console are cc-orch domain).
**Governance:** cai CAI-RESP-332 (guardrails below) + CAI-RESP-245 (synthetic/de-id QA). Substrate changes via direct-psycopg dry-run→apply (decision-962), NOT `supabase db push`.
**Origin:** operator 2026-06-26/27 — set up a Gazzabyte (external QA vendor) group "like the wingmen group with me in it", bug-bot for staff intake, tickets routed to the Gazzabyte group for approval, and "how do we scale if we have more groups and projects".

## Concrete-first (cai steer)
cai CAI-RESP-332: **build this ONE slice concretely; let the 2nd external tenant drive the generic pattern — don't over-build now.** So: ship the Gazzabyte slice + de-id pipeline + bug-bot concretely first; generalize into a data-driven channels registry + console CRUD only when a 2nd external tenant justifies it. The generic design is sketched at the end but is NOT this build's deliverable.

## Surfaces (two bots + one group)
The operator will create **2 new BotFather tokens** (cai bot is separate, task #29; the existing @wingmennorchbot is untouched):
1. **Gazzabyte bug-bot** — staff DM it → it clarifies the user journey → raises a ticket → posts into the Gazzabyte group. Per cai Q1: **dedicated bot, separate token, a DISTINCT SCOPED substrate identity (never a fleet identity)**; chat_id + sender allowlist enforced **server-side** (only the Gazzabyte group + allowlisted staff); **capability-gated** to ingest bugs + post to irsyad-QA only — no read/relay of PII/money/other verticals, no tenant-enumeration surface; rate-limited, audited, time-boxed, revocable. An external party never touches the fleet bridge.
2. **Gazzabyte coordination group** — cc-orch (@wingmennorchbot) participates *like the Shen group*, but **scope-locked to irsyad-QA** (synthetic/de-id only; gated out of money/prod/other verticals). The scope-lock gives "me in the group" AND isolation.

## Resolution loop (NOT a 3rd bot)
The resolver is a **CC engineering lane**, not a Telegram bot. Flow: staff → bug-bot (intake + journey-clarify) → ticket → Gazzabyte group (triage/approve) → a CC lane reproduces on the **synthetic/de-id QA org** → fix → PR → approve → merge+deploy+verify → bug-bot posts "resolved ✅". Fix-ownership fork (operator to pick): (A) a CC fleet agent auto-attempts the fix (recommended, fastest, operator approves PR) or (B) Gazzabyte's devs fix and the bot tracks ticket→PR→approval→deploy. Recommended: A-first, Gazzabyte for the hard ones.

## De-id pipeline + sign-off bar (cai CAI-RESP-332 Q2 — non-negotiable, amanah/PDPA)
- **Prefer fully synthetic** live-shaped data.
- If real-derived: (a) strip ALL direct identifiers; (b) **k-anonymity k≥5** on quasi-identifier combos (age→band, dates→month, rare cats→other); (c) a **ZERO JOIN-BACK test** — join the de-id quasi-identifiers against the real silos, must be **zero** unique re-id matches; (d) minimize — seed only the shape QA needs.
- QA org = **`is_synthetic=true`** (so it can never masquerade as real — the SMOKE-residue lesson) + **RLS-isolated** from every real org.
- **SIGN-OFF GATE:** a **dry-run to cai before ANY external exposure**, with: the re-id-risk report (k achieved + quasi-id inventory), the zero-join-back result, proof of `is_synthetic` + RLS isolation, and **INDEPENDENT verification** (reviewer lane or cai spot-check, NOT the building lane's self-report). **No external eyes before cai's sign-off.** Building is non-contingent; **EXPOSURE is the gate.**

## Build order (all non-contingent except exposure)
1. Synthetic Irsyad QA org + de-id pipeline + re-id-risk report (→ cai dry-run).
2. Bug-bot (distinct scoped identity, server-side allowlist, capability-gated, audited) — intake + journey-clarify + ticket model + route-to-group.
3. Resolution loop wiring (CC lane picks up approved tickets; A/B per operator).
4. Wire the Gazzabyte group (cc-orch scope-locked to irsyad-QA).

## Blocked on operator inputs
- Gazzabyte folks' Telegram **@handles** (sender allowlist).
- The **2 BotFather tokens** (bug-bot + cai bot).
- Fix-ownership **A/B** choice.

## Deferred generalization → Channels-CRUD (2nd-tenant-driven)
When a 2nd external tenant arrives, generalize to a data-driven **channels/slices registry**: one row per group `{ chat_id · name · scope (repos/verticals) · members (@handles+roles) · data_scope (prod/synthetic/de-id) · gates (money?/prod?) }`; the bridge routes by chat_id → looks up the slice → auto-applies scope + sender-auth (zero code per group); a fleet-console CRUD panel to manage slices. This is task #31 / the substrate-as-product play — each slice a scoped, monetizable channel. Relates to ARCH-035 three-channel taxonomy (governance layer). Do NOT build the abstraction before the 2nd tenant (cai: don't over-build).
