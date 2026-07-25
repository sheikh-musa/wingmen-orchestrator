# GIRO / bank-statement reconciliation — SYNTHETIC prototype brief

**From:** cc-orchestrator (hub) · **Driver:** client velocity ask (Elly #4981) — synthetic prototype in ~2 days · **Date:** 2026-07-17
**Repo:** ihsanos (irsyad tabung/donations live here). **Worktree:** `~/wingmen/ihsanos-wt/giro-synth` (branch `feat/giro-reconcile-synthetic`, off origin/main f623979). Scope doc: `reports/irsyad-design-scopes-20260717.md` §3.

## ⛔ HARD BOUNDARY — SYNTHETIC ONLY (money/residency safety)
This is a **fast UX/flow prototype the client reacts to** — it must touch **NO live data**:
- Build & test against a **SYNTHETIC tenant + seeded test donations** and a **fabricated sample bank statement** ONLY. Do NOT read or write any real irsyad/tabung/donation rows.
- Do NOT write to any live money table. Do NOT make a residency decision — the goumlyne-vs-ceayj question for real bank-statement PII is a **cai-gated live-cutover concern** and is explicitly OUT of scope here. Do NOT apply any migration to a live DB; any new schema stays authored-unapplied and synthetic-scoped.
- The LIVE version (real donations, real PII siting, retention, tamper-evidence mechanism) is separately gated: cai governance review + operator sign-off + residency verify. This prototype exists to de-risk the flow and get the client's reaction FAST, ahead of that gate.

## Build (per scope §3 design a–d — synthetic)
- **(a) Upload + extract:** upload a bank-statement/GIRO file (CSV first; PDF if quick) → parse entries (date, amount, reference) → **user-confirm step** (financial extract is verify-before-trust — never silently trusted; show parsed rows for confirmation).
- **(b) Match engine:** match statement entries ↔ recorded (synthetic) donations by amount + date + reference → classify **matched / unmatched / partial**. Sensible default tolerance (flag the tolerance rule as a client/cai open question, don't hard-decide it).
- **(c) Reconciliation view:** per-period tie-out — recorded total vs banked total, the delta, and where it lives (which entries are unmatched on each side). This is the Jan–Sept audit tie-out the client needs.
- **(d) Tamper-evident (basic):** append-only log of reconciliation actions (no silent edits). Full hash-chain mechanism is a live-cutover open question — a simple append-only audit table is enough for the prototype; flag the mechanism choice for cai.

## Proof (synthetic e2e)
Drive the whole flow on synthetic data: upload a fabricated statement → confirm extract → match against seeded donations → reconciliation view shows matched/unmatched/partial + the period tie-out + delta. Include a deliberately-unmatched + a partial case so the surfacing is proven. Unit + an e2e; tsc 0, lint 0, `next build` green. REAL pasted output.

## Report back to cc-orchestrator
Branch + SHA; files; the synthetic e2e output; screenshots/wireframe of the reconciliation view (this is client-facing — eyeball quality matters, mobile + desktop); and a crisp list of the LIVE-cutover open questions for cai/operator (residency, match tolerance, raw-statement retention, tamper-evidence mechanism). AUTHORED only — no merge, no live apply, no deploy. Hub reviews + eyeballs; client reacts to the synthetic prototype; the live build follows the cai/operator gate. Move fast — the client is holding us to days/hours.
